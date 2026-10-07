"""Provider-neutral data model: events, sessions, processes and runs."""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

MAX_EVENTS = 3000
# A session without a matched process still counts as live if it wrote this recently.
RECENT_SECS = 120


@dataclass
class Event:
    ts: float | None
    kind: str  # think | msg | cmd | file | user | tool | ask | turn | err | note
    text: str
    detail: str = ""
    seq: int = 0
    count: int = 1  # think: reasoning blocks merged into this one
    dur: float = 0.0  # think/cmd: seconds
    ok: bool = True  # cmd: succeeded
    code: str | None = None  # cmd: exit code
    sub: str = ""  # cmd: read/search/list_files · file: add/update/delete · turn: started/complete
    plus: int = 0  # file: lines added
    minus: int = 0  # file: lines removed
    anchor: float | None = None  # think: time of the previous action


class Session:
    """A session file read incrementally. Providers subclass it and implement `handle`."""

    provider = "?"
    # Cheap byte filters: lines that contain `skip` but none of `keep` are not parsed.
    skip_markers: tuple[bytes, ...] = ()
    keep_markers: tuple[bytes, ...] = ()

    def __init__(self, path: Path):
        self.path = path
        self.reset()

    def reset(self) -> None:
        self.offset = 0
        self.pending = b""
        self.session_id: str | None = None
        self.cwd: str | None = None
        self.start: float | None = None
        self.origin = "?"  # exec, desktop, ide, tui, cli, sdk...
        self.headless = False
        self.model: str | None = None
        self.effort: str | None = None
        self.sandbox: str | None = None
        self.title = ""
        self.first_prompt = ""
        self.last_ts: float | None = None
        self.state = "unknown"  # running | complete | aborted | unknown
        self.last_agent_message = ""
        self.events: deque[Event] = deque(maxlen=MAX_EVENTS)
        self.events_total = 0
        self.messages: list[tuple[float | None, str]] = []
        self.files: dict[str, list] = {}  # path -> [kind, count, ts, plus, minus]
        self.n_cmds = 0
        self.n_fail = 0
        self.tokens = 0
        self.ctx_used = 0
        self.ctx_window = 0
        self.rate_limits: dict | None = None
        self.rate_ts: float | None = None
        self.open_questions: dict[str, str] = {}
        self.in_flight: dict[str, Event] = {}  # tool calls still waiting for a result
        self.last_action_ts: float | None = None

    # -- events
    def add(self, ev: Event) -> Event:
        self.events_total += 1
        ev.seq = self.events_total
        self.events.append(ev)
        if ev.kind != "think":
            self.last_action_ts = ev.ts
        return ev

    @property
    def last_event(self) -> Event | None:
        return self.events[-1] if self.events else None

    def add_think(self, ts: float | None, summary: str = "") -> None:
        """Consecutive reasoning blocks merge into one event that measures thinking time."""
        last = self.last_event
        if last and last.kind == "think":
            last.count += 1
            if ts and last.anchor:
                last.dur = ts - last.anchor
            if summary:
                last.detail = (last.detail + "\n" + summary).strip()
            return
        anchor = self.last_action_ts or ts
        self.add(Event(ts, "think", "", summary, anchor=anchor,
                       dur=(ts - anchor) if ts and anchor else 0.0))

    def add_cmd(self, ev: Event) -> None:
        self.n_cmds += 1
        if not ev.ok:
            self.n_fail += 1
        self.add(ev)

    def touch_file(self, path: str, kind: str, ts: float | None, plus: int, minus: int) -> None:
        rec = self.files.setdefault(path, [kind, 0, ts, 0, 0])
        rec[0], rec[1], rec[2] = kind, rec[1] + 1, ts
        rec[3] += plus
        rec[4] += minus

    # -- reading
    def update(self) -> bool:
        try:
            size = self.path.stat().st_size
        except OSError:
            return False
        if size < self.offset:  # rewritten
            self.reset()
        if size == self.offset:
            return False
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read(size - self.offset)
        self.offset = size
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        for raw in lines:
            if not raw.strip() or self._skippable(raw):
                continue
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(obj, dict):
                try:
                    self.handle(obj)
                except Exception:  # a malformed record must not stop the session
                    pass
        return True

    def _skippable(self, raw: bytes) -> bool:
        head = raw[:200]
        return any(m in head for m in self.skip_markers) and not any(m in raw[:400] for m in self.keep_markers)

    def handle(self, obj: dict) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- derived
    @property
    def label(self) -> str:
        from bitacora.util import label_from_prompt
        return self.title or label_from_prompt(self.first_prompt)

    def recently_active(self, now: float | None = None) -> bool:
        now = now or time.time()
        return self.state == "running" and self.last_ts is not None and now - self.last_ts < RECENT_SECS


@dataclass
class ProcInfo:
    provider: str
    pid: int
    create: float
    cmdline: list
    headless: bool = False
    model: str | None = None
    effort: str | None = None
    sandbox: str | None = None
    cd: str | None = None
    out: str | None = None
    session_id: str | None = None  # known from --resume / --session-id
    log: str | None = None
    proc_cwd: str | None = None

    @property
    def cwd(self) -> str | None:
        return self.cd or self.proc_cwd


@dataclass
class Run:
    key: str
    provider: str
    session: Session | None
    proc: ProcInfo | None
    label: str
    out: str | None = None
    log: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def alive(self) -> bool:
        return self.proc is not None

    @property
    def headless(self) -> bool:
        if self.proc:
            return self.proc.headless
        return bool(self.session and self.session.headless)

    @property
    def origin(self) -> str:
        if self.session and self.session.origin != "?":
            return self.session.origin
        return "exec" if self.headless else "?"

    @property
    def live(self) -> bool:
        return self.alive or bool(self.session and self.session.recently_active())

    @property
    def status(self) -> str:
        """One of: ask, starting, running, idle, active, done, aborted, interrupted, unknown."""
        s = self.session
        if self.alive:
            if s and s.open_questions:
                return "ask"
            if s is None:
                return "starting"
            if s.state in ("complete", "aborted") and not self.headless:
                return "idle"  # interactive session waiting for the user
            return "running"
        if s is None:
            return "unknown"
        if s.open_questions and s.recently_active():
            return "ask"
        if s.recently_active():
            return "active"
        if s.state == "complete":
            return "done"
        if s.state == "aborted":
            return "aborted"
        return "interrupted" if s.state == "running" else "unknown"

    @property
    def cwd(self) -> str | None:
        return (self.session and self.session.cwd) or (self.proc and self.proc.cwd)

    @property
    def repo(self) -> str:
        return Path(self.cwd).name if self.cwd else "-"

    @property
    def model(self) -> str | None:
        return (self.session and self.session.model) or (self.proc and self.proc.model)

    @property
    def effort(self) -> str | None:
        return (self.session and self.session.effort) or (self.proc and self.proc.effort)

    @property
    def sandbox(self) -> str | None:
        return (self.proc and self.proc.sandbox) or (self.session and self.session.sandbox)

    @property
    def start(self) -> float | None:
        if self.proc:
            return self.proc.create
        return self.session.start if self.session else None

    @property
    def duration(self) -> float | None:
        if self.start is None:
            return None
        end = time.time() if self.alive else (self.session.last_ts if self.session else None)
        return None if end is None else end - self.start
