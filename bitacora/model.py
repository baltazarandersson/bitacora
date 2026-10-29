"""Provider-neutral data model: events, sessions, processes and runs."""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

MAX_EVENTS = 3000
MAX_QUARANTINE = 20  # discarded records kept as metadata, newest last
_GENERATION = itertools.count(1)  # identifies one Session object for the lifetime of the process
# A session without a matched process still counts as live if it wrote this recently.
RECENT_SECS = 120
ASK_STALE_SECS = 6 * 3600
LIMIT_WHEN = re.compile(r"(?:try again at|resets?(?: at)?)\s+([^.()]+?)(?:\s*\(|\.|$)", re.I)


def latest_limit(sessions, now: float | None = None) -> dict | None:
    """Return the newest still-active limit hit shared by providers."""
    now = time.time() if now is None else now
    hit_s = max((s for s in sessions if s.limit), default=None, key=lambda s: s.limit[0])
    if hit_s is None:
        return None
    ts, message = hit_s.limit
    info = dict(hit_s.limit_info or {})
    limit_type = str(info.get("type") or "").lower()

    def applies_to(session) -> bool:
        # Claude's Opus and Sonnet weekly limits are independent model pools.
        model_scope = "opus" if limit_type.endswith("_opus") else "sonnet" if limit_type.endswith("_sonnet") else None
        if not model_scope:
            return True
        model = str(getattr(session, "model", "") or "").lower()
        if session is hit_s and not model and info.get("model"):
            model = str(info["model"]).lower()
        return model_scope in model

    if any(applies_to(s) and (s.last_ok_ts or 0) > ts for s in sessions):
        return None
    from bitacora.util import as_epoch
    resets_at = as_epoch(info.get("resets_at"))
    if resets_at is not None and resets_at <= now:
        return None
    if info.get("resets_at") is not None:
        info["resets_at"] = resets_at
    match = LIMIT_WHEN.search(message)
    info["text"] = match.group(1).strip() if match else None
    info["reached_at"] = ts if ts else None
    return info


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
        self.gen = 0
        self.reset()

    def reset(self) -> None:
        # A reset starts a new stream even when the same Session object is reused after a file rewrite.
        self.gen = next(_GENERATION)
        self.offset = 0
        self.pending = b""
        self._file_identity: tuple[int, int] | None = None
        self._file_mtime_ns: int | None = None
        self._file_ctime_ns: int | None = None
        self._file_prefix = b""
        self._file_tail = b""
        self._file_samples: list[tuple[int, bytes]] = []
        self._content_digest = hashlib.blake2b()
        self.session_id: str | None = None
        self.cwd: str | None = None
        self.start: float | None = None
        self.origin = "?"  # provider's raw origin value
        self.origin_raw = "?"
        self.origin_kind = "other"
        self.headless = False
        self.model: str | None = None
        self.effort: str | None = None
        self.effort_source = "unknown"  # transcript | launch | unknown
        self.sandbox: str | None = None
        self.access_kind: str | None = None
        self.title = ""
        self.first_prompt = ""
        self.last_ts: float | None = None
        self.state = "unknown"  # running | complete | stopped | failed | limited | unknown
        self.last_agent_message = ""
        self.events: deque[Event] = deque(maxlen=MAX_EVENTS)
        self.events_total = 0
        self.records_total = 0  # non-empty, non-skippable records attempted in this stream
        self.discarded = 0  # records with invalid JSON or a handle() exception
        # (record number, kind, exception name, bytes): never the raw line or the exception message.
        self.quarantine: deque[tuple[int, str, str, int]] = deque(maxlen=MAX_QUARANTINE)
        self.messages: list[tuple[float | None, str]] = []
        self.files: dict[str, list] = {}  # path -> [kind, count, ts, plus, minus]
        self.n_cmds = 0
        self.n_fail = 0
        self.tokens = 0
        self.usage_basis = {"codex": "reported", "claude": "processed"}.get(self.provider, "unknown")
        self.context_tokens: int | None = None
        self.ctx_window: int | None = None
        self.context_window_assumed = False
        self.rate_limits: dict | None = None
        self.rate_ts: float | None = None
        self.limit: tuple[float, str] | None = None  # (when, message) of the last usage-limit hit
        # Structured details of that hit when the agent records them: {"window_minutes" | "type", "resets_at"}.
        self.limit_info: dict | None = None
        self.last_ok_ts: float | None = None  # last time the agent answered normally
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
            fh = open(self.path, "rb")
        except OSError:
            return False
        changed = False
        try:
            with fh:
                st = os.fstat(fh.fileno())
                size = st.st_size
                identity = (st.st_dev, st.st_ino)
                mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
                ctime_ns = getattr(st, "st_ctime_ns", int(st.st_ctime * 1_000_000_000))

                replaced = self._file_identity is not None and identity != self._file_identity
                truncated = size < self.offset
                if replaced or truncated:
                    self.reset()
                    changed = True
                    fh.seek(0)
                    self._consume(fh.read(size))
                elif (size == self.offset and self.offset and
                      (mtime_ns != self._file_mtime_ns or ctime_ns != self._file_ctime_ns or
                       not self._checkpoint_matches(fh, self.offset))):
                    # Same-length rewrites do not change the offset. Check the whole file only when metadata
                    # says it may have changed; ordinary idle polls and appends stay incremental.
                    fh.seek(0)
                    rewritten = fh.read(size)
                    digest = hashlib.blake2b(rewritten).digest()
                    if digest != self._content_digest.digest():
                        self.reset()
                        changed = True
                        self._consume(rewritten)
                    else:
                        self._file_mtime_ns = mtime_ns
                        self._file_ctime_ns = ctime_ns
                        return False
                elif size > self.offset and self.offset and not self._checkpoint_matches(fh, self.offset):
                    # A truncate-and-regrow between polls can leave size >= offset. The saved prefix and tail
                    # distinguish that case from a normal append without rereading the full transcript.
                    fh.seek(0)
                    rewritten = fh.read(size)
                    self.reset()
                    changed = True
                    self._consume(rewritten)
                else:
                    fh.seek(self.offset)
                    chunk = fh.read(max(0, size - self.offset))
                    if chunk:
                        self._consume(chunk)
                        changed = True
                self._remember_samples(fh, self.offset)
                self._file_identity = identity
                self._file_mtime_ns = mtime_ns
                self._file_ctime_ns = ctime_ns
                return changed
        except OSError:
            # Files are written or rotated by another process. A transient open/read race is retried next poll.
            return False

    def _checkpoint_matches(self, fh, offset: int) -> bool:
        if self._file_prefix:
            fh.seek(0)
            if fh.read(len(self._file_prefix)) != self._file_prefix:
                return False
        if self._file_tail:
            fh.seek(offset - len(self._file_tail))
            if fh.read(len(self._file_tail)) != self._file_tail:
                return False
        for position, sample in self._file_samples:
            fh.seek(position)
            if fh.read(len(sample)) != sample:
                return False
        return True

    def _remember_samples(self, fh, offset: int) -> None:
        if self._file_samples or offset < 512:
            return
        sample_size = 64
        try:
            for fraction in (0.25, 0.5, 0.75):
                position = max(0, min(offset - sample_size, int(offset * fraction) - sample_size // 2))
                fh.seek(position)
                sample = fh.read(sample_size)
                if len(sample) == sample_size:
                    self._file_samples.append((position, sample))
        except OSError:
            self._file_samples.clear()

    def _consume(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._content_digest.update(chunk)
        self.offset += len(chunk)
        if not self._file_prefix:
            self._file_prefix = chunk[:128]
        self._file_tail = (self._file_tail + chunk)[-128:]
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        for raw in lines:
            if not raw.strip() or self._skippable(raw):
                continue
            self.records_total += 1
            try:
                obj = json.loads(raw.decode("utf-8", "replace"))
            except ValueError as exc:
                self.discarded += 1
                self.quarantine.append((self.records_total, "json", type(exc).__name__, len(raw)))
                continue
            if isinstance(obj, dict):
                try:
                    self.handle(obj)
                except Exception as exc:  # a malformed record must not stop the session
                    self.discarded += 1
                    self.quarantine.append((self.records_total, "handle", type(exc).__name__, len(raw)))

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

    @property
    def total_tokens(self) -> int:
        """Compatibility name for the provider-specific cumulative total in `tokens`."""
        return self.tokens

    @total_tokens.setter
    def total_tokens(self, value: int) -> None:
        self.tokens = value

    @property
    def ctx_used(self) -> int | None:
        """Compatibility alias that preserves missing context usage as unknown."""
        return self.context_tokens

    @ctx_used.setter
    def ctx_used(self, value: int | None) -> None:
        self.context_tokens = value

    @property
    def context_window(self) -> int | None:
        return self.ctx_window

    @context_window.setter
    def context_window(self, value: int | None) -> None:
        self.ctx_window = value

    @property
    def access_raw(self) -> str | None:
        return self.sandbox


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


def run_status(turn: str | None, *, has_process: bool, transcript_fresh: bool, question_fresh: bool,
               open_question: bool, headless: bool) -> str:
    """Map normalized turn signals and process evidence to one user-facing status."""
    if turn is None:
        return "starting" if has_process else "unknown"
    if open_question and turn in ("running", "unknown"):
        return "asking" if has_process or question_fresh else "quiet"
    if turn in ("failed", "limited", "stopped"):
        return turn
    if turn == "complete":
        return "done" if headless else "your_turn"
    if turn == "running":
        return "running" if has_process or transcript_fresh else "quiet"
    return "starting" if has_process else "unknown"  # a live process whose transcript has no turn yet


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
    def origin_raw(self) -> str:
        if self.session:
            raw = getattr(self.session, "origin_raw", None)
            if raw not in (None, "", "?"):
                return raw
            return self.session.origin
        return "exec" if self.headless else "?"

    @property
    def origin_kind(self) -> str:
        if self.session and self.session.origin_raw not in ("?", ""):
            if self.session.origin_kind != "other":
                return self.session.origin_kind
            if self.provider == "codex":
                from bitacora.providers.codex import origin_kind_for_raw
                return origin_kind_for_raw(self.session.origin_raw)
            if self.provider == "claude":
                from bitacora.providers.claude import origin_kind_for_raw
                return origin_kind_for_raw(self.session.origin_raw)
            return self.session.origin_kind
        return "background" if self.headless else "other"

    @property
    def live(self) -> bool:
        s = self.session
        question_open = bool(s and s.open_questions and self.status == "asking")
        return self.alive or bool(s and s.recently_active()) or question_open

    @property
    def status(self) -> str:
        """The normalized user-facing status for this turn."""
        s = self.session
        return run_status(s.state if s else None, has_process=self.alive,
                          transcript_fresh=bool(s and s.recently_active()),
                          question_fresh=bool(s and s.last_ts is not None
                                              and time.time() - s.last_ts < ASK_STALE_SECS),
                          open_question=bool(s and s.open_questions), headless=self.headless)

    @property
    def status_source(self) -> str | None:
        """Evidence for a running status, available to Details as process or transcript."""
        if self.status != "running":
            return None
        return "process" if self.alive else "transcript"

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
    def effort_source(self) -> str:
        if self.session and self.session.effort:
            return self.session.effort_source if self.session.effort_source != "unknown" else "transcript"
        if self.proc and self.proc.effort:
            return "launch"
        return "unknown"

    @property
    def sandbox(self) -> str | None:
        return (self.session and self.session.sandbox) or (self.proc and self.proc.sandbox)

    @property
    def access_kind(self) -> str | None:
        if self.session and self.session.sandbox:
            if self.session.access_kind:
                return self.session.access_kind
        if self.sandbox:
            from bitacora.util import access_kind
            return access_kind(self.sandbox, self.provider)
        return None

    @property
    def access_raw(self) -> str | None:
        return self.sandbox

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
