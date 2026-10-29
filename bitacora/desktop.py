"""Validated desktop data and actions, independent of the HTML presentation."""
from __future__ import annotations

import math
import os
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import psutil

from bitacora import __version__, i18n, procs
from bitacora.i18n import t
from bitacora.launcher import clean_env
from bitacora.model import Run
from bitacora.runtime import Runtime
from bitacora.sorting import DESKTOP_SORT_COLUMNS, sort_runs
from bitacora.store import capped_hours
from bitacora.themes import DEFAULT_THEME, THEMES, descriptors
from bitacora.util import fmt_dur, fmt_tokens, same_path
from bitacora.views import DEFAULT_VIEW, all_views, counts
from bitacora.worker import PAGE_LIMIT, Source

READ_LIMIT = 128_000
ASPECTS = ("messages", "events", "files", "log", "result", "info")
DOCUMENT_FILES = {".md", ".markdown", ".txt", ".log", ".out", ".json", ".jsonl", ".csv", ".tsv",
                  ".yaml", ".yml", ".toml", ".rst"}


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _context_tokens(session):
    return number(getattr(session, "ctx_used", None)) if session else None


def _usage_basis(session, provider):
    basis = getattr(session, "usage_basis", None)
    if basis not in ("processed", "reported"):
        basis = "processed" if str(provider).lower().startswith("claude") else "reported"
    return basis


def _localized_kind(value, allowed, prefix):
    kind = value if value in allowed else "other"
    return t(f"{prefix}_{kind.replace('-', '_')}")


def _context_window(run, session):
    window = number(getattr(session, "ctx_window", None)) if session else None
    if window is not None and window <= 0:
        window = None
    assumed = None
    if run.provider == "claude":
        from bitacora.providers.claude import context_window_for_model
        assumed = number(context_window_for_model(run.model))
        if assumed:
            window = assumed
    return window, assumed is not None


def _origin_kind(run, session):
    if session is None:
        return getattr(run, "origin_kind", "other")
    if run.provider == "claude":
        from bitacora.providers.claude import origin_kind_for_raw
    else:
        from bitacora.providers.codex import origin_kind_for_raw
    return origin_kind_for_raw(getattr(session, "origin", None))


def _effort_source(run, session):
    if run.provider == "codex" and session and getattr(session, "effort", None):
        return "transcript"
    if run.proc and getattr(run.proc, "effort", None):
        return "launch"
    if session and getattr(session, "effort", None):
        return "transcript"
    return "unknown"


def _access_kind(run, session):
    from bitacora.util import access_kind

    raw = getattr(session, "sandbox", None) if session else None
    raw = raw or getattr(run, "sandbox", None)
    return access_kind(raw, run.provider) or "other"


class DesktopService:
    def __init__(self, runtime: Runtime, *, view=None, live_only=False, source=None, scene_time=None):
        self._runtime = runtime
        self._state = runtime.state
        self._views = all_views(runtime.config.views)
        ids = {v.id for v in self._views}
        chosen = view or self._state.get("view") or DEFAULT_VIEW
        self._prefs = {"view": chosen if chosen in ids else DEFAULT_VIEW, "lang": runtime.lang,
                       "hours": runtime.config.hours, "notify": runtime.config.notify,
                       "theme": self._state.get("theme", DEFAULT_THEME),
                       "font_size": self._state.get("font_size", 14),
                       "list_height": self._state.get("list_height")}
        if self._prefs["theme"] not in THEMES:
            self._prefs["theme"] = DEFAULT_THEME
        if number(self._prefs["font_size"]) is None or not 10 <= self._prefs["font_size"] <= 32:
            self._prefs["font_size"] = 14
        if number(self._prefs["list_height"]) is None or not 0.15 <= self._prefs["list_height"] <= 0.85:
            self._prefs["list_height"] = None
        self._first_run = view is None and self._state.first_run
        self._live_only = bool(live_only)
        self._source = source or Source(runtime.config.hours, scan_procs=runtime.demo_root is None)
        self._runs: list[Run] = []
        self._revision = 0
        self._aspect: dict[str, dict[str, int]] = {}
        self._aspect_sig: dict[str, dict[str, tuple]] = {}
        self._previous: dict[str, str] = {}
        self._notices = []
        self._confirmations: dict[str, tuple[str, int, float, float]] = {}
        self._lock = threading.RLock()
        self._closed = False
        self._scene = None
        self._scene_time = scene_time
        self._scene_start = time.monotonic()
        self._sky = None
        self._scene_colors = {}  # rich Style -> (fg, bg, bold); scene palettes are small
        self._source.start()

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._confirmations.clear()
                self._source.stop()

    def _valid(self, proposed) -> bool:
        return (all(isinstance(proposed[key], str) for key in ("view", "lang", "theme")) and
                proposed["view"] in {v.id for v in self._views} and
                proposed["lang"] in (*i18n.LANGS, "auto") and proposed["theme"] in THEMES and
                isinstance(proposed["notify"], bool) and number(proposed["hours"]) is not None and
                proposed["hours"] > 0 and number(proposed["font_size"]) is not None and
                10 <= proposed["font_size"] <= 32 and
                (proposed["list_height"] is None or
                 number(proposed["list_height"]) is not None and 0.15 <= proposed["list_height"] <= 0.85))

    def _sync(self):
        """Adopt what another interface saved since this window last read the preferences; other keys stay as shown."""
        for key in self._state.refresh():
            if key not in self._prefs:
                continue
            candidate = dict(self._prefs, **{key: self._state.get(key)})
            if not self._valid(candidate):
                continue
            self._prefs = candidate
            if key == "hours":
                self._source.set_hours(candidate["hours"])
            elif key == "notify":
                self._runtime.config.notify = candidate["notify"]

    def bootstrap(self):
        with self._lock:
            self._sync()
            actual = i18n.set_lang(None if self._prefs["lang"] == "auto" else self._prefs["lang"])
            return {"version": __version__, "lang": actual, "strings": {key: t(key) for key in i18n.S},
                    "preferences": dict(self._prefs), "first_run": self._first_run,
                    "live_only": self._live_only,
                    "views": [{"id": v.id, "name": t(v.name) if v.builtin else v.name} for v in self._views],
                    "themes": descriptors()}

    def _view(self):
        return next(v for v in self._views if v.id == self._prefs["view"])

    def _poll(self):
        if self._closed:
            return
        incoming = self._source.poll()
        if incoming is None:
            return
        self._runs = incoming
        self._update_aspects(incoming)
        self._revision += 1
        statuses = {}
        for run in incoming:
            status = run.status
            statuses[run.key] = status
            before = self._previous.get(run.key)
            if not self._prefs["notify"] or not self._view().matches(run) or before is None or before == status:
                continue
            if status == "asking":
                self._notices.append({"kind": "asking", "text": t("n_ask", label=run.label)})
            elif before in ("running", "asking", "starting"):
                if status in ("done", "your_turn"):
                    self._notices.append({"kind": "done", "text": t("n_done", label=run.label)})
                elif status in ("failed", "limited", "stopped"):
                    self._notices.append({"kind": "stopped", "text": t("n_stopped", label=run.label)})
        self._previous = statuses

    @staticmethod
    def _content_sigs(run):
        s = run.session
        if s is None:
            return {"messages": (None,), "events": (None,), "files": (None,)}
        last = s.messages[-1] if s.messages else None
        return {
            # Length alone freezes once the mirror caps the window; the tail still changes on append.
            "messages": (s.gen, len(s.messages), last[0] if last else None, len(last[1]) if last else 0),
            "events": (s.gen, s.events_total),
            "files": tuple(sorted((path, tuple(values)) for path, values in s.files.items())),
        }

    def _update_aspects(self, runs):
        live = {run.key for run in runs}
        for key in list(self._aspect):
            if key not in live:
                self._aspect.pop(key, None)
                self._aspect_sig.pop(key, None)
        for run in runs:
            counts = self._aspect.setdefault(run.key, dict.fromkeys(ASPECTS, 0))
            sigs = self._aspect_sig.setdefault(run.key, {})
            for aspect, sig in self._content_sigs(run).items():
                if sigs.get(aspect) != sig:
                    sigs[aspect] = sig
                    counts[aspect] += 1

    @staticmethod
    def _stat_sig(path):
        try:
            st = os.stat(path)
            return (st.st_size, st.st_mtime_ns)
        except (OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _transcript_sig(session):
        """A result with no file shows the agent's last message: its revision follows the transcript."""
        if session is None:
            return None
        last = session.messages[-1] if session.messages else None
        return (session.gen, len(session.messages), last, session.last_agent_message)

    def _path(self, path):
        if path is None:
            return None
        if self._runtime.demo_root:
            try:
                return "/demo/" + Path(path).resolve().relative_to(self._runtime.demo_root.resolve()).as_posix()
            except ValueError:
                pass
        return str(path)

    def _provider(self, run):
        return next((p for p in self._runtime.providers if p.name == run.provider), None)

    def _resume(self, run):
        provider = self._provider(run)
        return provider.resume_cmd(run) if provider else None

    def _can_open(self, path):
        try:
            return bool(path and Path(path).is_file() and Path(path).resolve().suffix.lower() in DOCUMENT_FILES)
        except OSError:
            return False

    def _can_stop(self, run):
        if self._closed or self._source.health not in ("ready", "partial") or not run.proc or not run.session:
            return False
        if run.proc.session_id and run.session.session_id:
            return run.proc.session_id == run.session.session_id
        return bool(run.session.session_id and same_path(run.proc.cwd, run.session.cwd))

    def _summary(self, run):
        from bitacora.ui.render import activity, agent_table_text, agent_text

        s = run.session
        ctx_used = _context_tokens(s)
        ctx_window, context_window_assumed = _context_window(run, s)
        if ctx_window:
            context = f"{100 * (ctx_used / ctx_window):.0f}%" if ctx_used is not None else t("quota_value_unknown")
        else:
            context = ("0" if ctx_used == 0 else fmt_tokens(ctx_used)) if ctx_used is not None else "-"
        basis = _usage_basis(s, run.provider)
        origin_raw = getattr(s, "origin", getattr(run, "origin", "?")) if s else getattr(run, "origin", "?")
        origin_kind = _origin_kind(run, s)
        if origin_kind not in ("terminal", "background", "desktop", "ide", "subagent", "other"):
            origin_kind = "other"
        effort_source = _effort_source(run, s)
        if effort_source not in ("transcript", "launch", "unknown"):
            effort_source = "unknown"
        access_kind = _access_kind(run, s)
        if access_kind not in ("read-only", "workspace-write", "prompt", "auto-edit", "plan", "full-access", "other"):
            access_kind = "other"
        return {"key": run.key, "provider": run.provider, "agent": agent_table_text(run).plain,
                "agent_full": agent_text(run).plain,
                "label": run.label, "status": run.status, "status_source": run.status_source,
                "live": run.live, "alive": run.alive, "repo": run.repo, "cwd": self._path(run.cwd),
                "model": run.model, "effort": run.effort,
                "sandbox": run.sandbox,
                "usage_basis": basis, "context_window_assumed": context_window_assumed,
                "origin_kind": origin_kind, "origin_raw": origin_raw,
                "effort_source": effort_source, "access_kind": access_kind, "access_raw": run.sandbox,
                "start_ts": number(run.start),
                "started_text": datetime.fromtimestamp(run.start).strftime("%d/%m %H:%M")
                if run.start is not None else "-",
                "duration": number(run.duration), "last_ts": number(s.last_ts) if s else None,
                "tokens": number(s.tokens) if s else None, "context_tokens": ctx_used, "ctx_used": ctx_used,
                "tokens_text": fmt_tokens(s.tokens if s else 0), "context_text": context,
                "ctx_window": ctx_window, "context_window": ctx_window, "n_cmds": s.n_cmds if s else None,
                "n_fail": s.n_fail if s else 0, "n_files": len(s.files) if s else None,
                "pid": run.proc.pid if run.proc else None, "activity": activity(s, run, limit=None).plain,
                "actions": {"open_result": self._can_open(run.out), "open_log": self._can_open(run.log),
                            "copy_resume": bool(self._resume(run)), "stop": self._can_stop(run)}}

    def get_snapshot(self, sort_key=None, sort_direction=None):
        if sort_key is None:
            if sort_direction is not None:
                return {"error": t("desktop_invalid_request")}
        elif (not isinstance(sort_key, str) or sort_key not in DESKTOP_SORT_COLUMNS
              or sort_direction not in ("asc", "desc")):
            return {"error": t("desktop_invalid_request")}
        with self._lock:
            self._poll()
            runs = [r for r in self._runs if self._view().matches(r)]
            runs = sort_runs(runs, sort_key, sort_direction)
            statuses = ("asking", "running", "starting", "your_turn", "done", "failed", "limited",
                        "stopped", "quiet", "unknown")
            totals = {"total": len(runs), "live": sum(r.live for r in runs),
                      **{status: 0 for status in statuses}}
            for run in runs:
                if run.status in statuses:
                    totals[run.status] += 1
            totals["views"] = {v.id: dict(zip(("total", "live"), counts(v, self._runs), strict=True))
                               for v in self._views}
            age = time.monotonic() - self._source.last_update if self._source.last_update is not None else None
            notices, self._notices = self._notices, []
            errors = [] if self._closed else [
                {"provider": provider, "message": message}
                for provider, message in sorted(self._source.provider_errors.items())
            ]
            return {"revision": self._revision,
                    "reader": {"state": "error" if self._closed else self._source.health,
                               "error": t("desktop_reader_closed") if self._closed else self._source.error,
                               "age": age, "errors": errors},
                    "runs": [self._summary(r) for r in runs], "counts": totals,
                    "quotas": self._source.mirror.quotas, "notices": notices}

    def _run(self, key):
        if self._closed or not isinstance(key, str) or len(key) > 4096:
            return None
        self._poll()
        return next((r for r in self._runs if r.key == key and self._view().matches(r)), None)

    def _read(self, path, *, tail=False, what=None):
        out = {"path": self._path(path), "text": "", "available": False}
        if not path:
            return out
        try:
            with open(path, "rb") as fh:
                size = os.fstat(fh.fileno()).st_size
                if tail:
                    fh.seek(max(0, size - READ_LIMIT))
                data = fh.read(READ_LIMIT)
            out.update(text=data.decode("utf-8", "replace"), available=True)
            if size > READ_LIMIT:
                note = t("desktop_file_tail" if tail else "desktop_file_head")
                out["text"] = note + "\n\n" + out["text"]
        except OSError:
            noun = what or ("log" if tail else "result")
            out["error"] = t("n_no_file", what=t(f"desktop_tab_{noun}"))
        return out

    def get_detail(self, key, known=None):
        if known is not None and (not isinstance(known, dict) or set(known) - set(ASPECTS) or
                                  any(isinstance(value, bool) or not isinstance(value, int)
                                      for value in known.values())):
            return {"error": t("desktop_invalid_request")}
        with self._lock:
            run = self._run(key)
            if run is None:
                return {"error": t("desktop_detail_missing")}
            s = run.session
            basis = _usage_basis(s, run.provider)
            ctx_used = _context_tokens(s)
            ctx_window, context_window_assumed = _context_window(run, s)
            if ctx_window:
                context = f"{ctx_used:,} / {ctx_window:,}" if ctx_used is not None else \
                    f"{t('quota_value_unknown')} / {ctx_window:,}"
            else:
                context = ("0" if ctx_used == 0 else f"{ctx_used:,}") if s and ctx_used is not None else "-"
            if s and context_window_assumed:
                context += " · " + t("context_window_assumed")
            effort_source = _effort_source(run, s)
            if effort_source not in ("transcript", "launch", "unknown"):
                effort_source = "unknown"
            access_kind = _localized_kind(
                _access_kind(run, s),
                ("read-only", "workspace-write", "prompt", "auto-edit", "plan", "full-access", "other"),
                "access",
            )
            origin_raw = getattr(s, "origin", getattr(run, "origin", "-")) if s else getattr(run, "origin", "-")
            from bitacora.ui.render import agent_text
            status_source = run.status_source
            status_source_text = (
                t(f"status_source_{status_source}")
                if status_source in ("process", "transcript") else "-"
            )
            fields = [("inf_status", t("st_" + run.status)), ("inf_status_source", status_source_text),
                      ("inf_agent", agent_text(run).plain),
                      ("inf_origin_raw", origin_raw or "-"),
                      ("inf_session", s.session_id if s else "-"), ("inf_folder", self._path(run.cwd) or "-"),
                      ("inf_model", run.model or "-"), ("inf_effort", run.effort or "-"),
                      ("inf_effort_source", t(f"effort_source_{effort_source}")),
                      ("inf_access_kind", access_kind),
                      ("inf_mode", run.sandbox or "-"),
                      ("inf_started", datetime.fromtimestamp(run.start).isoformat(sep=" ", timespec="seconds")
                       if run.start else "-"), ("inf_duration", fmt_dur(run.duration)),
                      (f"usage_{basis}", f"{s.tokens:,}" if s else "-"),
                      ("inf_tokens_basis", t(f"usage_{basis}_basis")),
                      ("inf_context", context),
                      ("inf_result", self._path(run.out) or "-"), ("inf_log", self._path(run.log) or "-"),
                      ("inf_file", self._path(s.path) if s else "-"),
                      ("inf_pid", str(run.proc.pid) if run.proc else t("not_running")),
                      ("inf_command", " ".join(run.proc.cmdline[1:]) if run.proc else "-"),
                      ("inf_resume", self._resume(run) or "-")]
            if s and s.open_questions:
                fields.insert(1, ("inf_question", " | ".join(s.open_questions.values())))
            counts = self._aspect.setdefault(run.key, dict.fromkeys(ASPECTS, 0))
            sigs = self._aspect_sig.setdefault(run.key, {})
            result_sig = self._stat_sig(run.out)
            if result_sig is None:  # no file: the fallback mirrors the transcript; a file appearing replaces this
                result_sig = ("transcript", self._transcript_sig(s))
            for aspect, sig in (("log", self._stat_sig(run.log)), ("result", result_sig)):
                if sigs.get(aspect) != sig:
                    sigs[aspect] = sig
                    counts[aspect] += 1
            # Freshness (ages, durations) keeps its own clock instead of riding on content changes.
            info_sig = (run.status, s.gen if s else None, s.tokens if s else None, ctx_used,
                        run.model, run.effort, run.sandbox, s.events_total if s else 0,
                        tuple(s.open_questions.items()) if s else (),
                        run.proc.pid if run.proc else None, run.cwd, run.out, run.log,
                        int(time.time() // 4) if run.live else 0)
            if sigs.get("info") != info_sig:
                sigs["info"] = info_sig
                counts["info"] += 1
            revisions = {aspect: counts[aspect] for aspect in ASPECTS}
            fresh = {aspect: known is None or known.get(aspect) != revisions[aspect]
                     for aspect in ASPECTS}
            out = {"run": self._summary(run), "revisions": revisions, "resume": self._resume(run)}
            if fresh["messages"]:
                out["messages"] = [{"ts": ts, "text": text} for ts, text in s.messages] if s else []
                total = getattr(s, "messages_total", len(s.messages)) if s else 0
                out["messages_offset"] = max(0, total - len(s.messages)) if s else 0
            if fresh["events"]:
                out["events"] = [asdict(e) for e in s.events] if s else []
                out["events_total"] = s.events_total if s else 0
            if fresh["files"]:
                out["files"] = [{"path": self._path(p), "kind": v[0], "count": v[1], "ts": v[2],
                                 "plus": v[3], "minus": v[4]} for p, v in s.files.items()] if s else []
            if fresh["log"]:
                out["log"] = self._read(run.log, tail=True, what="log")
            if fresh["result"]:
                result = self._read(run.out, tail=True, what="result")
                if not result["available"] and s:
                    latest = s.last_agent_message or (s.messages[-1][1] if s.messages else "")
                    if latest:
                        result["text"] = latest
                        result["fallback"] = True
                out["result"] = result
            if fresh["info"]:
                out["info"] = [{"label": t(label), "value": str(value)} for label, value in fields]
            return out

    def get_detail_page(self, key, before, limit):
        with self._lock:
            run = self._run(key)
            if run is None:
                return {"error": t("desktop_detail_missing")}
            if isinstance(before, bool) or not isinstance(before, int) or before < 0 or \
                    isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= PAGE_LIMIT:
                return {"error": t("desktop_invalid_request")}
            if not hasattr(self._source, "request_page"):
                return {"error": t("desktop_invalid_request")}
            return {"token": self._source.request_page(run.key, before, limit)}

    def get_page(self, token):
        if isinstance(token, bool) or not isinstance(token, int) or token < 0:
            return {"error": t("desktop_invalid_request")}
        with self._lock:
            self._poll()
            page = self._source.take_page(token) if hasattr(self._source, "take_page") else None
        if page and page.get("error"):
            key = {"missing": "desktop_detail_missing", "expired": "desktop_page_expired"}.get(
                page["error"], "desktop_invalid_request")
            return {"error": t(key)}
        return page or {"pending": True}

    def save_preferences(self, patch):
        with self._lock:
            if self._closed or not isinstance(patch, dict) or set(patch) - set(self._prefs):
                return {"error": t("desktop_invalid_request")}
            if "list_height" in patch and number(patch["list_height"]) is None:
                return {"error": t("desktop_invalid_request")}
            if "hours" in patch:
                hours = capped_hours(patch["hours"])
                if hours is None:
                    return {"error": t("desktop_invalid_request")}
                patch = dict(patch, hours=hours)
            self._sync()  # the proposal starts from what is saved now, so no other setting is written back stale
            proposed = dict(self._prefs, **patch)
            if not self._valid(proposed):
                return {"error": t("desktop_invalid_request")}
            for key, value in patch.items():
                self._state.set(key, value)
            if not self._state.save():
                self._state = type(self._state)(self._state.path)
                return {"error": t("desktop_preferences_failed")}
            self._prefs = proposed
            self._runtime.config.notify = proposed["notify"]
            if "hours" in patch:
                self._source.set_hours(proposed["hours"])
            if "view" in patch:
                self._first_run = False
            return self.bootstrap()

    def _open(self, key, attr):
        with self._lock:
            run = self._run(key)
            path = getattr(run, attr) if run else None
            if not self._can_open(path):
                return {"ok": False, "message": t("desktop_unavailable")}
            try:
                if os.name == "nt":
                    os.startfile(path)
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path], env=clean_env())
            except OSError:
                return {"ok": False, "message": t("desktop_action_failed")}
            return {"ok": True, "message": ""}

    def open_result(self, key):
        return self._open(key, "out")

    def open_log(self, key):
        return self._open(key, "log")

    def copy_resume(self, key):
        with self._lock:
            run = self._run(key)
            command = self._resume(run) if run else None
            return {"ok": bool(command), "text": command or "",
                    "message": "" if command else t("desktop_unavailable")}

    def request_stop(self, key):
        with self._lock:
            run = self._run(key)
            if run is None or not self._can_stop(run):
                return {"ok": False, "message": t("desktop_unavailable")}
            try:
                if abs(psutil.Process(run.proc.pid).create_time() - run.proc.create) > 0.01:
                    raise psutil.NoSuchProcess(run.proc.pid)
            except psutil.Error:
                return {"ok": False, "message": t("desktop_process_changed")}
            token = secrets.token_urlsafe(24)
            self._confirmations.clear()
            self._confirmations[token] = (run.key, run.proc.pid, run.proc.create, time.monotonic() + 30)
            return {"ok": True, "token": token, "pid": run.proc.pid,
                    "message": t("desktop_stop_body", label=run.label, pid=run.proc.pid)}

    def confirm_stop(self, token):
        with self._lock:
            if not isinstance(token, str) or len(token) > 128:
                return {"ok": False, "message": t("desktop_invalid_request")}
            record = self._confirmations.pop(token, None)
            if record is None or record[3] < time.monotonic():
                return {"ok": False, "message": t("desktop_stop_expired")}
            key, pid, created, _ = record
            run = self._run(key)
            if not run or not self._can_stop(run) or run.proc.pid != pid or run.proc.create != created:
                return {"ok": False, "message": t("desktop_process_changed")}
        try:
            procs.kill_tree(pid, expected_create=created)
        except (psutil.Error, OSError, ValueError):
            return {"ok": False, "message": t("desktop_process_changed")}
        self._source.refresh()
        return {"ok": True, "message": t("desktop_stop_sent")}

    def get_scene(self, columns):
        from bitacora.ui.masthead import SkyClock
        from bitacora.ui.scene import Scene

        with self._lock:
            if (self._closed or not isinstance(columns, int) or isinstance(columns, bool)
                    or not 40 <= columns <= 240):
                return {"error": t("desktop_invalid_request")}
            if self._scene is None or self._scene.w != columns:
                self._scene = Scene(columns, 16)
            if self._sky is None:  # the sky starts with the first frame
                self._sky = SkyClock(self._scene_time)
            frame = self._scene.frame(time.monotonic() - self._scene_start, self._sky.at())
            # Rows travel as runs [text, fg, bg, bold] with colors indexed into a per-frame palette: the bridge is
            # the bottleneck, and per-cell objects made each frame several times larger.
            palette: dict[str, int] = {}
            cells = []
            for row in frame:
                line = []
                for segment in row:
                    text = segment.text.replace("\r", "").replace("\n", "")
                    if not text:
                        continue
                    style = segment.style
                    colors = self._scene_colors.get(style)
                    if colors is None:
                        fg = style.color.get_truecolor().hex if style and style.color else "#a9b1d6"
                        bg = style.bgcolor.get_truecolor().hex if style and style.bgcolor else "#060a17"
                        colors = self._scene_colors[style] = (fg, bg, int(bool(style and style.bold)))
                    fg = palette.setdefault(colors[0], len(palette))
                    bg = palette.setdefault(colors[1], len(palette))
                    if line and line[-1][1:] == [fg, bg, colors[2]]:
                        line[-1][0] += text
                    else:
                        line.append([text, fg, bg, colors[2]])
                cells.append(line)
            return {"columns": columns, "rows": 16, "palette": list(palette), "cells": cells}
