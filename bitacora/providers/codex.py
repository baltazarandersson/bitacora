"""Codex CLI: rollouts in $CODEX_HOME/sessions, processes `codex exec` and `codex`."""
from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from bitacora.model import Event, ProcInfo, Run, Session, latest_limit
from bitacora.procs import Proc
from bitacora.providers.base import Provider
from bitacora.util import UUID_RE, access_kind, as_epoch, first_line, norm_path, parse_iso, rel_to, same_path

NAME_TS_RE = re.compile(r"rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-")
VALUE_FLAGS = {"-m", "--model", "-c", "--config", "-s", "--sandbox", "-C", "--cd", "-o",
               "--output-last-message", "-p", "--profile", "-i", "--image", "--color",
               "--output-schema", "--add-dir", "-a", "--ask-for-approval", "--enable", "--disable"}
# Subcommands that do not own a conversation.
NON_SESSION = {"app-server", "mcp", "mcp-server", "login", "logout", "completion", "debug", "apply",
               "sandbox", "proto", "cloud", "features", "help", "update", "doctor", "plugin", "marketplace"}
CODEX_WINDOW_NAMES = {300: "5h", 10080: "weekly"}
CODEX_WINDOW_IDS = {300: "5h", 10080: "week"}


def parse_codex_args(argv: list) -> dict | None:
    """Model, sandbox, paths and resume id from a `codex` command line. None if it is not a session."""
    args = argv[1:]
    if any(a.startswith("--type=") for a in args):
        return None
    headless = "exec" in args[:3]
    rest = args[args.index("exec") + 1:] if headless else args
    info: dict = {"headless": headless}
    resume = False
    positionals: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        key, eq, inline = a.partition("=") if a.startswith("--") else (a, "", "")
        if key in VALUE_FLAGS:
            v = inline if eq else (rest[i + 1] if i + 1 < len(rest) else "")
            i += 0 if eq else 1
            if key in ("-m", "--model"):
                info["model"] = v
            elif key in ("-s", "--sandbox"):
                info["sandbox"] = v
            elif key in ("-C", "--cd"):
                info["cd"] = norm_path(v)
            elif key in ("-o", "--output-last-message"):
                info["out"] = norm_path(v)
            elif key in ("-c", "--config"):
                k, _, val = v.partition("=")
                k, val = k.strip(), val.strip().strip("\"'")
                if k == "model_reasoning_effort":
                    info["effort"] = val
                elif k == "sandbox_mode":
                    info["sandbox"] = val
                elif k == "model":
                    info["model"] = val
        elif not a.startswith("-"):
            if not positionals and not headless and a in NON_SESSION:
                return None
            if a == "resume" and not positionals:
                resume = True
            else:
                positionals.append(a)
        i += 1
    if resume:
        info["session_id"] = next((p for p in positionals if UUID_RE.fullmatch(p)), None)
    return info


def origin_of(meta: dict) -> tuple[str, bool]:
    source = meta.get("source")
    originator = str(meta.get("originator") or "").casefold()
    if source == "exec" or originator == "codex_exec":
        return "exec", True
    if isinstance(source, dict):
        return "subagent", True
    if "desktop" in originator.lower():
        return "desktop", False
    if originator == "codex-tui" or source == "cli":
        return "tui", False
    if source == "vscode":
        return "ide", False
    return str(source or originator or "?"), False


def raw_origin_of(meta: dict) -> str:
    source = meta.get("source")
    if isinstance(source, dict):
        return json.dumps(source, sort_keys=True, ensure_ascii=False)
    originator = str(meta.get("originator") or "")
    if source == "exec":
        return "exec"
    if "desktop" in originator.casefold():
        return originator
    if originator.casefold() == "codex-tui":
        return originator
    return str(source if source is not None else meta.get("originator") or "?")


def origin_kind_for_raw(raw: str | dict | None) -> str:
    """Normalize a serialized Codex source or originator value for desktop consumers."""
    if isinstance(raw, dict):
        return "subagent"
    value = str(raw or "").strip()
    if value.startswith("{"):
        try:
            if isinstance(json.loads(value), dict):
                return "subagent"
        except ValueError:
            pass
    lowered = value.casefold()
    if lowered in ("exec", "codex_exec"):
        return "background"
    if "desktop" in lowered:
        return "desktop"
    if lowered in ("codex-tui", "cli"):
        return "terminal"
    if lowered == "vscode":
        return "ide"
    return "other"


def origin_kind_of(meta: dict) -> str:
    source = meta.get("source")
    originator = str(meta.get("originator") or "").casefold()
    if source == "exec" or originator == "codex_exec":
        return "background"
    if isinstance(source, dict):
        return "subagent"
    if "desktop" in originator:
        return "desktop"
    if originator == "codex-tui" or source == "cli":
        return "terminal"
    if source == "vscode":
        return "ide"
    return origin_kind_for_raw(raw_origin_of(meta))


def _task_error(error) -> tuple[str, bool]:
    if isinstance(error, dict):
        fields = [str(value) for value in error.values() if isinstance(value, str) and value]
        message = str(error.get("message") or error.get("codex_error_info") or error.get("detail") or "")
    else:
        message = str(error or "")
        fields = [message] if message else []
    normalized = re.sub(r"[-_]+", " ", " ".join(fields).lower())
    limited = any(marker in normalized for marker in ("usage limit", "rate limit", "limit reached"))
    return message or " ".join(fields), limited


def exhausted_window(rate_limits: dict | None, now: float) -> dict | None:
    """A quota window at 100% that has not reset yet: Codex keeps reporting the bar, not an error."""
    best = None
    for key in ("primary", "secondary"):
        w = (rate_limits or {}).get(key)
        if not w or not isinstance(w.get("used_percent"), (int, float)) \
                or isinstance(w.get("used_percent"), bool) or not math.isfinite(w["used_percent"]) \
                or w["used_percent"] < 100:
            continue
        resets_at = as_epoch(w.get("resets_at"))
        if resets_at is None or resets_at <= now:
            continue
        if best is None or resets_at > best["resets_at"]:
            best = {"window_minutes": w.get("window_minutes"), "resets_at": resets_at, "text": None}
    return best


class CodexSession(Session):
    provider = "codex"
    # response_item lines repeat what item_completed already reports; only function calls matter.
    skip_markers = (b'"type":"response_item"', b'"type":"world_state"', b'"type":"token_usage_record"')
    keep_markers = (b'"function_call',)

    def handle(self, o: dict) -> None:
        typ = o.get("type")
        p = o.get("payload") or {}
        ts = parse_iso(o.get("timestamp"))
        if ts:
            self.last_ts = ts
        if typ == "session_meta":
            self.session_id = p.get("id") or p.get("session_id")
            self.cwd = p.get("cwd")
            self.start = parse_iso(p.get("timestamp")) or ts
            _, self.headless = origin_of(p)
            self.origin = self.origin_raw = raw_origin_of(p)
            self.origin_kind = origin_kind_of(p)
            return
        if typ == "turn_context":
            self.model = p.get("model") or self.model
            if p.get("effort"):
                self.effort = p["effort"]
                self.effort_source = "transcript"
            sandbox = (p.get("sandbox_policy") or {}).get("type")
            if sandbox:
                self.sandbox = sandbox
                self.access_kind = access_kind(sandbox, self.provider)
            return
        if typ == "response_item":
            self._response_item(p, ts)
            return
        if typ != "event_msg":
            return
        et = p.get("type")
        if et == "item_completed":
            self._item(p.get("item") or {}, ts)
        elif et == "task_started":
            self.state = "running"
            self.open_questions.clear()
            self.add(Event(ts, "turn", "", sub="started"))
        elif et == "task_complete":
            self.last_agent_message = p.get("last_agent_message") or self.last_agent_message
            err = p.get("error")
            self.open_questions.clear()
            if err:
                message, limited = _task_error(err)
                self.state = "limited" if limited else "failed"
                if limited:
                    self.limit = (ts or 0.0, message)
                    self.limit_info = None
                self.add(Event(ts, "err", first_line(message, 300)))
            else:
                self.state = "complete"
                self.add(Event(ts, "turn", "", sub="complete"))
        elif et == "turn_aborted":
            self.state = "stopped"
            self.open_questions.clear()
            self.add(Event(ts, "err", str(p.get("reason", "?")), sub="aborted"))
        elif et == "token_count":
            info = p.get("info") or {}
            total = (info.get("total_token_usage") or {}).get("total_tokens")
            last_usage = info.get("last_token_usage") or {}
            context = last_usage.get("input_tokens")
            window = info.get("model_context_window")
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                self.tokens = total
            if "input_tokens" in last_usage:
                self.context_tokens = context if isinstance(context, int) and not isinstance(context, bool) \
                    and context >= 0 else None
            if window is not None:
                self.ctx_window = window if isinstance(window, int) and not isinstance(window, bool) \
                    and window > 0 else None
            rl = p.get("rate_limits") or {}
            # Once the quota runs out Codex reports other limits with empty windows; keep the real ones.
            if rl.get("primary") or rl.get("secondary"):
                self.rate_limits, self.rate_ts = rl, ts
                if not rl.get("rate_limit_reached_type"):
                    self.last_ok_ts = ts
            if rl.get("rate_limit_reached_type"):
                self.state = "limited"
                self.open_questions.clear()
                self.limit = (ts or 0.0, str(rl["rate_limit_reached_type"]))
                self.limit_info = {"type": str(rl["rate_limit_reached_type"])}
        elif et == "thread_settings_applied":
            s = p.get("thread_settings") or {}
            self.model = s.get("model") or self.model
            if s.get("reasoning_effort"):
                self.effort = s["reasoning_effort"]
                self.effort_source = "transcript"
        elif et == "thread_name_updated" or et == "thread_renamed":
            self.title = p.get("name") or p.get("thread_name") or self.title
        elif et in ("error", "stream_error"):
            self.add(Event(ts, "err", first_line(p.get("message", str(p)), 300)))

    def _response_item(self, p: dict, ts: float | None) -> None:
        pt = p.get("type")
        if pt == "function_call":
            name = p.get("name", "")
            args = p.get("arguments", "")
            if "request_user_input" in name:
                q = args
                try:
                    qs = json.loads(args).get("questions") or []
                    q = " | ".join(x.get("title") or x.get("question", "") for x in qs)
                except (ValueError, AttributeError):
                    pass
                self.open_questions[p.get("call_id")] = q
                self.add(Event(ts, "ask", first_line(q, 300), q))
            else:
                self.add(Event(ts, "tool", f"{name}({first_line(args, 100)})", args, sub=name))
        elif pt == "function_call_output":
            self.open_questions.pop(p.get("call_id"), None)

    def _item(self, it: dict, ts: float | None) -> None:
        t = it.get("type")
        if t == "Reasoning":
            # Reasoning is encrypted; there is only text when reasoning summaries are on.
            summ = "\n".join(s if isinstance(s, str) else s.get("text", "")
                             for s in it.get("summary_text") or []).strip()
            self.add_think(ts, summ)
        elif t == "AgentMessage":
            txt = "\n".join(c.get("text", "") for c in it.get("content") or [] if isinstance(c, dict))
            self.messages.append((ts, txt))
            self.add(Event(ts, "msg", first_line(txt, 400), txt))
        elif t == "UserMessage":
            txt = "\n".join(c.get("text", "") for c in it.get("content") or [] if isinstance(c, dict))
            if not self.first_prompt:
                self.first_prompt = txt
            self.add(Event(ts, "user", first_line(txt, 300), txt))
        elif t == "CommandExecution":
            parsed = it.get("parsed_cmd") or []
            head = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
            cmd = head.get("cmd") or (it.get("command") or [""])[-1]
            code = it.get("exit_code")
            dur = it.get("duration")
            secs = (dur.get("secs") or 0) + (dur.get("nanos") or 0) / 1e9 if isinstance(dur, dict) else 0.0
            out = it.get("aggregated_output") or it.get("formatted_output") or ""
            self.add_cmd(Event(ts, "cmd", str(cmd).strip(), out, dur=secs, ok=code in (None, 0, "0"),
                               code=None if code is None else str(code), sub=head.get("type") or ""))
        elif t == "FileChange":
            for path, ch in (it.get("changes") or {}).items():
                ch = ch or {}
                kind = ch.get("type", "?")
                diff = ch.get("unified_diff") or ""
                if diff:
                    lines = diff.splitlines()
                    plus = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
                    minus = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
                else:
                    content = ch.get("content") or ""
                    plus = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                    minus = 0
                rel = rel_to(path, self.cwd)
                self.touch_file(rel, kind, ts, plus, minus)
                self.add(Event(ts, "file", rel, diff or ch.get("content") or "", sub=kind, plus=plus, minus=minus))


class CodexProvider(Provider):
    name = "codex"
    label = "Codex"
    marker = "codex"
    session_cls = CodexSession

    def __init__(self) -> None:
        self._index: dict[str, str] = {}
        self._index_mtime = 0.0

    def home(self) -> Path:
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")

    def title(self, session: Session) -> str | None:
        """Thread names live in session_index.jsonl (one JSON object per rename)."""
        idx = self.home() / "session_index.jsonl"
        try:
            mtime = idx.stat().st_mtime
        except OSError:
            return None
        if mtime != self._index_mtime:
            names: dict[str, str] = {}
            try:
                with open(idx, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        try:
                            o = json.loads(line)
                        except ValueError:
                            continue
                        if o.get("id") and o.get("thread_name"):
                            names[o["id"]] = o["thread_name"]
            except OSError:
                return None
            self._index, self._index_mtime = names, mtime
        return self._index.get(session.session_id or "")

    def _session_dirs(self) -> list[Path]:
        return [self.home() / "sessions", self.home() / "archived_sessions"]

    def _date_dirs(self, days: int) -> list[Path]:
        dirs = []
        today = datetime.now()
        live, archived = self._session_dirs()
        for d in range(days + 1):
            day = today - timedelta(days=d)
            p = live / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
            if p.exists():
                dirs.append(p)
        if archived.exists():
            dirs.append(archived)
        return dirs

    @staticmethod
    def name_time(path: Path) -> float | None:
        m = NAME_TS_RE.search(path.name)
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S").timestamp()
        except ValueError:
            return None

    @staticmethod
    def peek_meta(path: Path) -> dict:
        try:
            with open(path, "rb") as fh:
                o = json.loads(fh.readline().decode("utf-8", "replace"))
            return (o.get("payload") or {}) if o.get("type") == "session_meta" else {}
        except (OSError, ValueError):
            return {}

    def procs(self, snap: list[Proc]) -> list[ProcInfo]:
        result = []
        for p in snap:
            if p.name not in ("codex.exe", "codex") or not p.cmdline:
                continue
            info = parse_codex_args(p.cmdline)
            if info is None:
                continue
            result.append(ProcInfo("codex", p.pid, p.create, p.cmdline, proc_cwd=p.cwd(), **info))
        return result

    def match(self, pi: ProcInfo, claimed: set[Path], peers=()) -> Path | None:
        if pi.session_id:
            for base in self._session_dirs():
                hits = list(base.rglob(f"*{pi.session_id}.jsonl")) if base.exists() else []
                if hits:
                    return hits[0]
            return None
        # A transcript born after a newer same-folder process may belong to that process even when the older
        # process is the first one visited. Leave the older process unmatched instead of greedily stealing it.
        if not pi.cwd:
            return None  # Time alone cannot establish which project/process owns a transcript.
        rivals = [q for q in peers if q is not pi and q.create > pi.create and q.cwd and pi.cwd
                  and same_path(q.cwd, pi.cwd)]
        candidates: list[tuple[float, Path]] = []
        for d in self._date_dirs(1):
            for f in d.glob("rollout-*.jsonl"):
                nt = self.name_time(f)
                if f in claimed or nt is None or abs(nt - pi.create) > 180:
                    continue
                meta = self.peek_meta(f)
                st = parse_iso(meta.get("timestamp")) or nt
                if pi.cwd and meta.get("cwd") and not same_path(pi.cwd, meta["cwd"]):
                    continue
                sid = meta.get("id") or meta.get("session_id")
                if any(q.session_id and q.session_id == sid for q in peers if q is not pi):
                    continue
                if any(pi.create < q.create <= st for q in rivals):
                    continue
                diff = abs(st - pi.create)
                if diff < 120:
                    candidates.append((diff, f))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        # Timestamps have finite precision; near-ties across live processes are not enough evidence to attach
        # a PID to a transcript. A false negative is safer than offering actions on the wrong process.
        if len(candidates) > 1 and candidates[1][0] - candidates[0][0] <= 0.25:
            return None
        return candidates[0][1]

    def history(self, since: float) -> Iterable[Path]:
        days = max(0, int((datetime.now().timestamp() - since) // 86400)) + 1
        for d in self._date_dirs(days):
            for f in d.glob("rollout-*.jsonl"):
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                if max(mtime, self.name_time(f) or 0) >= since:
                    yield f

    def resume_cmd(self, run: Run) -> str | None:
        sid = run.session.session_id if run.session else None
        if not sid:
            return None
        if not run.headless:
            return f"codex resume {sid}"
        parts = ["codex exec resume"]
        if run.model:
            parts.append(f"-m {run.model}")
        if run.effort:
            parts.append(f'-c model_reasoning_effort="{run.effort}"')
        if run.sandbox:
            parts.append(f'-c sandbox_mode="{run.sandbox}"')
        parts += [sid, "-"]
        return " ".join(parts)

    def quota(self, sessions: Iterable[Session]) -> dict | None:
        """Return Codex windows and any active limit in the shared quota shape."""
        sessions = list(sessions)
        now = time.time()
        best = None
        for s in sessions:
            if s.rate_limits and (best is None or (s.rate_ts or 0) > best[0]):
                best = (s.rate_ts or 0, s.rate_ts, s.rate_limits)
        _, snapshot_ts, snapshot = best if best else (None, None, {})
        windows = []
        for window_id in ("primary", "secondary"):
            raw = snapshot.get(window_id)
            if not isinstance(raw, dict):
                continue
            minutes = raw.get("window_minutes")
            minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0 else None
            resets_at = as_epoch(raw.get("resets_at"))
            raw_percent = raw.get("used_percent")
            used_percent = raw_percent if isinstance(raw_percent, (int, float)) and \
                not isinstance(raw_percent, bool) and math.isfinite(raw_percent) else None
            if resets_at is not None and resets_at <= now:
                used_percent = None
            windows.append({
                "id": CODEX_WINDOW_IDS.get(minutes, f"{minutes}m" if minutes else None),
                "name": CODEX_WINDOW_NAMES.get(minutes),
                "minutes": minutes,
                "scope": "account",
                "used_percent": used_percent,
                "resets_at": resets_at,
                "as_of": snapshot_ts,
                "source": "recorded",
            })

        limit = exhausted_window(snapshot, now)
        if limit:
            limit["reached_at"] = snapshot_ts
        else:
            limit = latest_limit(sessions, now)
        if limit:
            limit_type = limit.get("type")
            source_window = snapshot.get(limit_type) if limit_type in ("primary", "secondary") else None
            minutes = limit.get("window_minutes")
            if minutes is None and source_window:
                minutes = source_window.get("window_minutes")
            if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
                minutes = None
            resets_at = limit.get("resets_at")
            if resets_at is None and source_window:
                resets_at = source_window.get("resets_at")
            normalized_limit = {
                "window": CODEX_WINDOW_IDS.get(minutes),
                "name": CODEX_WINDOW_NAMES.get(minutes),
                "scope": "account",
                "reached_at": as_epoch(limit.get("reached_at")) if limit.get("reached_at") is not None
                    else snapshot_ts,
                "resets_at": as_epoch(resets_at),
                "reset_text": limit.get("text"),
                "source": "recorded" if limit.get("type") or limit.get("window_minutes")
                or limit.get("resets_at") else "message",
            }
            if normalized_limit["resets_at"] is not None and normalized_limit["resets_at"] <= now:
                normalized_limit = None
        else:
            normalized_limit = None
        reporting = "windows" if windows else "limit-only" if normalized_limit else "none"
        return {"reporting": reporting, "windows": windows, "limit": normalized_limit}
