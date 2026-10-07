"""Codex CLI: rollouts in $CODEX_HOME/sessions, processes `codex exec` and `codex`."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from bitacora.model import Event, ProcInfo, Run, Session
from bitacora.procs import Proc
from bitacora.providers.base import Provider
from bitacora.util import UUID_RE, first_line, norm_path, parse_iso, rel_to, same_path

NAME_TS_RE = re.compile(r"rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-")
VALUE_FLAGS = {"-m", "--model", "-c", "--config", "-s", "--sandbox", "-C", "--cd", "-o",
               "--output-last-message", "-p", "--profile", "-i", "--image", "--color",
               "--output-schema", "--add-dir", "-a", "--ask-for-approval", "--enable", "--disable"}
# Subcommands that do not own a conversation.
NON_SESSION = {"app-server", "mcp", "mcp-server", "login", "logout", "completion", "debug", "apply",
               "sandbox", "proto", "cloud", "features", "help", "update", "doctor", "plugin", "marketplace"}


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
    originator = str(meta.get("originator") or "")
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
            self.origin, self.headless = origin_of(p)
            return
        if typ == "turn_context":
            self.model = p.get("model") or self.model
            self.effort = p.get("effort") or self.effort
            self.sandbox = (p.get("sandbox_policy") or {}).get("type") or self.sandbox
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
            self.add(Event(ts, "turn", "", sub="started"))
        elif et == "task_complete":
            self.state = "complete"
            self.last_agent_message = p.get("last_agent_message") or self.last_agent_message
            self.add(Event(ts, "turn", "", sub="complete"))
        elif et == "turn_aborted":
            self.state = "aborted"
            self.add(Event(ts, "err", str(p.get("reason", "?")), sub="aborted"))
        elif et == "token_count":
            info = p.get("info") or {}
            self.tokens = (info.get("total_token_usage") or {}).get("total_tokens") or self.tokens
            self.ctx_used = (info.get("last_token_usage") or {}).get("input_tokens") or self.ctx_used
            self.ctx_window = info.get("model_context_window") or self.ctx_window
            if p.get("rate_limits"):
                self.rate_limits, self.rate_ts = p["rate_limits"], ts
        elif et == "thread_settings_applied":
            s = p.get("thread_settings") or {}
            self.model = s.get("model") or self.model
            self.effort = s.get("reasoning_effort") or self.effort
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

    def match(self, pi: ProcInfo, claimed: set[Path]) -> Path | None:
        if pi.session_id:
            for base in self._session_dirs():
                hits = list(base.rglob(f"*{pi.session_id}.jsonl")) if base.exists() else []
                if hits:
                    return hits[0]
            return None
        best = None
        for d in self._date_dirs(1):
            for f in d.glob("rollout-*.jsonl"):
                nt = self.name_time(f)
                if f in claimed or nt is None or abs(nt - pi.create) > 180:
                    continue
                meta = self.peek_meta(f)
                st = parse_iso(meta.get("timestamp")) or nt
                if pi.cwd and meta.get("cwd") and not same_path(pi.cwd, meta["cwd"]):
                    continue
                diff = abs(st - pi.create)
                if diff < 120 and (best is None or diff < best[0]):
                    best = (diff, f)
        return best[1] if best else None

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
        best = None
        for s in sessions:
            if s.rate_limits and (best is None or (s.rate_ts or 0) > best[0]):
                best = (s.rate_ts or 0, s.rate_limits)
        return best[1] if best else None
