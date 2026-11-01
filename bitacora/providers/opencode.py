"""OpenCode: sessions from its SQLite store, read without writing. Message rows map onto the session model."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

from bitacora.model import Event, Run, Session
from bitacora.procs import Proc
from bitacora.providers.base import Capabilities, Provider, Support, resume_cwd, resume_value
from bitacora.sources import SessionReader, SessionRef, SqliteReader, connect_readonly, locator_text, ref_key
from bitacora.util import count_lines, first_line, rel_to, same_path

# Message types OpenCode documents. A row of any other type is counted as unknown.
MESSAGE_TYPES = frozenset({
    "user", "assistant", "system", "idle", "compaction", "synthetic", "shell", "skill", "agent-switched",
    "model-selected", "location-switched",
})
TOKEN_COLUMNS = ("tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")
# OpenCode has no process per session. A busy session that has not written for this long is no longer assumed live,
# so a crashed service does not keep a session running forever.
BUSY_SECS = 30 * 60
SHORT_NOTE = 200  # a synthetic record's description becomes a note only when it is this short
SEARCH_KINDS = {"read": "read", "grep": "search", "glob": "list_files"}
SHORT_INPUT = {"webfetch": "url", "websearch": "query", "skill": "id"}
DONE = ("completed", "error")


def _number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _count(value: object) -> int:
    return _number(value) or 0


def _seconds(ms: object) -> float | None:
    """OpenCode stores epoch milliseconds."""
    return ms / 1000 if isinstance(ms, (int, float)) and not isinstance(ms, bool) else None


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _text(value: object) -> str:
    """Tool output: a string, the text parts of a list, or the message of an error object."""
    if isinstance(value, list):
        return "\n".join(_str(_dict(part).get("text")) for part in value)
    if isinstance(value, dict):
        return _str(value.get("message"))
    return _str(value)


def _error_text(error: dict) -> str:
    """What an error object says: its message, else its type, else its status code."""
    for key in ("message", "type"):
        if _str(error.get(key)):
            return _str(error.get(key))
    status = _number(error.get("status"))
    return str(status) if status is not None else "?"


def _limited(error: dict) -> bool:
    """A usage or rate limit: HTTP 429, a limit type, or a message that names one."""
    kind = _str(error.get("type")).casefold()
    message = _str(error.get("message")).casefold()
    return (_number(error.get("status")) == 429 or "limit" in kind
            or "rate limit" in message or "usage limit" in message)


def _model_id(raw: object) -> str | None:
    """The model id from the JSON object OpenCode keeps in the session row; anything else is unknown."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    model = value.get("id") if isinstance(value, dict) else None
    return model if isinstance(model, str) and model else None


def data_home() -> Path:
    """The XDG data home, which OpenCode also uses on Windows."""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


ROOT_COLUMNS = frozenset({"id", "parent_id", "time_archived", "time_updated"})


class SchemaChanged(Exception):
    """The session table no longer has the columns discovery needs."""


class OpenCodeSession(Session):
    provider = "opencode"
    usage_basis = "reported"
    busy = False  # the session row shows a turn being worked on; the reader sets it

    def recently_active(self, now: float | None = None) -> bool:
        now = now or time.time()
        if super().recently_active(now):
            return True
        # A long tool call keeps a running turn live; a failed or limited turn is over whatever the row says.
        return (self.state == "running" and self.busy and self.last_ts is not None
                and now - self.last_ts < BUSY_SECS)

    def handle(self, obj: dict) -> None:
        typ = obj.get("type")
        if typ not in MESSAGE_TYPES:
            self.note_unknown()
            return
        data = _dict(obj.get("data"))
        ts = _seconds(obj.get("ts"))
        if ts is not None and (self.last_ts is None or ts > self.last_ts):
            self.last_ts = ts
        if typ == "user":
            self._user(data, ts)
        elif typ == "assistant":
            self._assistant(data, ts)
        elif typ == "idle":
            self._idle(data, ts)
        elif typ == "compaction":
            self.add(Event(ts, "note", "context compacted", sub="compacted"))  # translated when rendered
        elif typ == "synthetic":
            description = _str(data.get("description")).strip()
            if description and len(description) <= SHORT_NOTE:
                self.add(Event(ts, "note", first_line(description, SHORT_NOTE)))
        # system, shell, skill, agent-switched, model-selected and location-switched are known and not shown.

    def _user(self, data: dict, ts: float | None) -> None:
        self.state = "running"
        self.open_questions.clear()
        self.add(Event(ts, "turn", "", sub="started"))
        text = _str(data.get("text"))
        if not text.strip():
            return
        if not self.first_prompt:
            self.first_prompt = text
        self.messages.append((ts, text))
        self.add(Event(ts, "user", first_line(text, 300), text))

    def _assistant(self, data: dict, ts: float | None) -> None:
        tokens = _dict(data.get("tokens"))
        cache = _dict(tokens.get("cache"))
        # The last answer's prompt is the context in use. The window is not in the database, so it stays unknown.
        prompt = _number(tokens.get("input"))
        cached = _count(cache.get("read")) + _count(cache.get("write"))
        # An answer that failed before reaching the model reports no prompt: keep the last known context.
        if prompt is not None and prompt + cached > 0:
            self.context_tokens = prompt + cached
        model = _dict(data.get("model"))
        if model:  # an answer that names its model also says whether it has a variant
            variant = model.get("variant")
            self.effort = variant if isinstance(variant, str) and variant else None
            self.effort_source = "transcript" if self.effort else "unknown"
        texts: list[str] = []
        pending = False
        for part in _list(data.get("content")):
            part = _dict(part)
            kind = part.get("type")
            if kind == "text":
                text = _str(part.get("text"))
                if text.strip():
                    texts.append(text)
                    self.messages.append((ts, text))
                    self.add(Event(ts, "msg", first_line(text, 400), text))
            elif kind == "reasoning":
                started = _seconds(_dict(part.get("time")).get("created"))
                self.add_think(started if started is not None else ts, _str(part.get("text")).strip())
            elif kind == "tool":
                pending = self._tool(part, ts) or pending
            else:
                self.note_unknown()
        finish = data.get("finish")
        if finish == "error":
            self._fail(ts, _dict(data.get("error")))
            return
        self.last_ok_ts = ts
        if finish == "stop" and not pending:
            self.state = "complete"
            self.open_questions.clear()
            if texts:
                self.last_agent_message = "\n".join(texts)
            self.add(Event(ts, "turn", "", sub="complete"))
        else:
            self.state = "running"

    def _idle(self, data: dict, ts: float | None) -> None:
        outcome = data.get("outcome")
        if outcome == "succeeded":
            if self.state != "complete":
                self.add(Event(ts, "turn", "", sub="complete"))
            self.state = "complete"
            self.open_questions.clear()
        elif outcome == "failed":
            self._fail(ts, _dict(data.get("error")))

    def _fail(self, ts: float | None, error: dict) -> None:
        message = _error_text(error)
        self.open_questions.clear()
        if _limited(error):
            self.state = "limited"
            self.limit = (ts or 0.0, message)
            self.limit_info = None
        else:
            self.state = "failed"
        self.add(Event(ts, "err", first_line(message, 300)))

    def _tool(self, part: dict, ts: float | None) -> bool:
        """One tool call. Returns True while it has no result yet."""
        state = _dict(part.get("state"))
        times = _dict(part.get("time"))
        name, tid = _str(part.get("name")), _str(part.get("id"))
        inp = _dict(state.get("input"))
        created, completed = _seconds(times.get("created")), _seconds(times.get("completed"))
        start = created if created is not None else ts
        done = state.get("status") in DONE
        if name == "question":
            return self._question(tid, inp, done, start)
        if not done:
            self.in_flight[tid] = Event(start, "cmd" if name == "shell" else "tool", self._describe(name, inp),
                                        sub=name)
            return True
        self.in_flight.pop(tid, None)
        ok = state.get("status") == "completed"
        dur = completed - created if created is not None and completed is not None else 0.0
        out = _text(state.get("content")) or _text(state.get("error"))
        self._result(name, inp, _dict(state.get("metadata")), start, ok, out, dur)
        return False

    def _question(self, tid: str, inp: dict, done: bool, ts: float | None) -> bool:
        asked = [_str(_dict(item).get("question")) for item in _list(inp.get("questions"))]
        text = " | ".join(question for question in asked if question) or "question"
        if done:
            self.open_questions.pop(tid, None)
        else:
            self.open_questions[tid] = text
        self.add(Event(ts, "ask", first_line(text, 300), text, sub="question"))
        return not done

    def _describe(self, name: str, inp: dict) -> str:
        if name == "shell":
            command, workdir = _str(inp.get("command")), _str(inp.get("workdir"))
            return f"cd {workdir} && {command}" if workdir and not same_path(workdir, self.cwd) else command
        if name in ("read", "edit", "write"):
            return rel_to(_str(inp.get("path")), self.cwd)
        if name == "grep":
            path = rel_to(_str(inp.get("path")), self.cwd) if inp.get("path") else ""
            return " ".join(part for part in (_str(inp.get("pattern")), path) if part)
        if name == "glob":
            return _str(inp.get("pattern"))
        if name == "subagent":
            return f"agent: {_str(inp.get('description')) or _str(inp.get('agent'))}"
        if name in SHORT_INPUT and _str(inp.get(SHORT_INPUT[name])):
            return _str(inp.get(SHORT_INPUT[name]))
        return f"{name}({first_line(json.dumps(inp, ensure_ascii=False), 100)})"

    def _result(self, name: str, inp: dict, metadata: dict, start: float | None, ok: bool, out: str,
                dur: float) -> None:
        if name == "shell":
            code = _number(metadata.get("exit"))
            self.add_cmd(Event(start, "cmd", self._describe(name, inp), out, dur=dur, ok=ok and code in (None, 0),
                               code=None if code is None else str(code)))
        elif name in SEARCH_KINDS:
            self.add_cmd(Event(start, "cmd", self._describe(name, inp), out, dur=dur, ok=ok,
                               code=None if ok else "error", sub=SEARCH_KINDS[name]))
        elif name in ("edit", "write"):
            self._file_change(name, inp, metadata, start, ok, out, dur)
        else:
            self.add(Event(start, "tool" if ok else "err", self._describe(name, inp), out, sub=name, dur=dur))

    def _file_change(self, name: str, inp: dict, metadata: dict, start: float | None, ok: bool, out: str,
                     dur: float) -> None:
        rel = rel_to(_str(inp.get("path")), self.cwd)
        if not ok:
            self.add(Event(start, "err", f"{name} {rel}", out, dur=dur, sub=name))
            return
        if name == "write":
            # OpenCode gives no diff for a write: the lines written count as added, and a path the session has
            # not touched before counts as new.
            content = _str(inp.get("content"))
            kind = "update" if rel in self.files else "add"
            plus = count_lines(content)
            self.touch_file(rel, kind, start, plus, 0)
            self.add(Event(start, "file", rel, content, sub=kind, plus=plus, minus=0))
            return
        changes = [_dict(item) for item in _list(metadata.get("files")) if isinstance(item, dict)] or [{}]
        for change in changes:
            path = rel_to(_str(change.get("file")) or _str(inp.get("path")), self.cwd)
            plus, minus = _count(change.get("additions")), _count(change.get("deletions"))
            self.touch_file(path, "update", start, plus, minus)
            self.add(Event(start, "file", path, sub="update", plus=plus, minus=minus))


class OpenCodeReader(SqliteReader):
    header_table = "session_v2"
    stream_table = "session_message"
    required = {
        "session_v2": ("id", "directory", "title", "model", "time_created", "time_updated", "time_busy", "time_idle",
                       *TOKEN_COLUMNS),
        "session_message": ("session_id", "seq", "time_created", "time_updated", "type", "data"),
    }

    def fill_header(self, row: dict) -> None:
        s = self._session
        s.session_id = row["id"]
        s.title = row["title"] or ""
        s.cwd = row["directory"] or None
        s.model = _model_id(row["model"])
        s.tokens = sum(_count(row[name]) for name in TOKEN_COLUMNS)
        s.start = _seconds(row["time_created"])
        # The newest of the row's own write and the newest message already ingested.
        stamps = [stamp for stamp in (_seconds(row["time_updated"]), s.last_ts) if stamp is not None]
        s.last_ts = max(stamps, default=None)
        busy, idle = _number(row["time_busy"]), _number(row["time_idle"])
        s.busy = busy is not None and (idle is None or busy > idle)


class OpenCodeProvider(Provider):
    name = "opencode"
    label = "OpenCode"
    home_env = "OPENCODE_DB"  # a database file; without it, discovery reads the XDG data home, see `databases`
    session_cls = OpenCodeSession
    label_key = "provider_opencode"
    short_label_key = "provider_opencode_short"
    accent = "primary"
    tier = "best-effort"
    verified_versions = ("2.2.4",)
    process_names = ("opencode",)  # the shared service's executable, read only for `service_running`
    capabilities = Capabilities(
        processes=False, history=True, resume=Support.PARTIAL, messages=Support.FULL, commands=Support.FULL,
        files=Support.PARTIAL, questions=Support.PARTIAL, usage=Support.FULL, context=Support.PARTIAL,
        quota=Support.NONE, titles=Support.FULL, subagents=Support.PARTIAL,
    )

    def home(self) -> Path:
        return data_home() / "opencode"

    def databases(self) -> list[Path]:
        override = os.environ.get("OPENCODE_DB")
        if override:
            return [] if override == ":memory:" else [Path(override)]
        return sorted(path for path in self.home().glob("opencode*.db") if path.is_file())

    def available(self) -> bool:
        return any(db.is_file() for db in self.databases())

    def discover(self, since: float) -> Iterable[SessionRef]:
        for db in self.databases():
            if not db.is_file():
                continue
            for session_id in self._roots(db, since):
                locator = (locator_text(db), session_id)
                yield SessionRef(self.name, ref_key(self.name, locator), locator, "sqlite")

    @staticmethod
    def _roots(db: Path, since: float) -> list[str]:
        """Root sessions touched after `since`: subagents and archived sessions are not runs."""
        with closing(connect_readonly(db)) as conn:
            present = {row[1] for row in conn.execute("PRAGMA table_info(session_v2)")}
            if not ROOT_COLUMNS <= present:
                raise SchemaChanged("session_v2")  # named in the partial-reader notice, never a crash
            rows = conn.execute("SELECT id FROM session_v2 WHERE parent_id IS NULL AND time_archived IS NULL "
                                "AND time_updated >= ? ORDER BY time_updated DESC", (since * 1000,)).fetchall()
        return [row[0] for row in rows]

    def open(self, ref: SessionRef) -> SessionReader:
        return OpenCodeReader(ref, self.session_cls())

    def resume(self, run: Run) -> dict | None:
        """`opencode --session <id>` from the session folder. Partial: an app-only install may lack the binary."""
        sid = resume_value(run.session.session_id) if run.session else None
        cwd = resume_cwd(run.cwd)
        if not sid or not cwd:
            return None
        return {"argv": ["opencode", "--session", sid], "cwd": cwd}

    def title(self, session: Session) -> str | None:
        return session.title or None

    def origin_kind(self, session: Session) -> str:
        return "other"

    def service_running(self, snap: list[Proc]) -> bool | None:
        """True while the shared service runs. Read from the command line only: no file, and no run is made for it."""
        return any(_is_service(proc) for proc in snap)


def _is_service(proc: Proc) -> bool:
    """`opencode ... serve --service`: the executable is OpenCode's, and the arguments name the shared server."""
    args = [arg.lower() for arg in proc.cmdline]
    if not args:
        return False
    exe = args[0].replace("\\", "/").rsplit("/", 1)[-1]
    return exe.startswith("opencode") and "serve" in args and "--service" in args
