"""Reading session files away from the UI.

Parsing large JSON lines holds Python's GIL, which made the interface stutter while it read. The reader
runs in its own process and sends only what changed: new events, counters and run metadata. The UI keeps a
light mirror of each session and applies those changes in a few milliseconds.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import queue
import threading
import time
from dataclasses import fields
from pathlib import Path

from bitacora.collector import Collector
from bitacora.model import MAX_EVENTS, Event, ProcInfo, Run, Session
from bitacora.providers import all_providers
from bitacora.store import Memory

TAIL = 400  # events sent for a session the UI has not seen yet
MESSAGES = 60
EVENT_FIELDS = tuple(field.name for field in fields(Event))


def _format_error(exc: BaseException) -> str:
    """Reader diagnostics omit exception filenames and payloads, which can contain local session details."""
    name = type(exc).__name__
    code = getattr(exc, "errno", None)
    detail = getattr(exc, "strerror", None)
    if code is not None and detail:
        return f"{name} (errno {code}): {detail[:160]}"
    return name
SCALARS = ("session_id", "cwd", "start", "origin", "origin_raw", "origin_kind", "headless", "model", "effort",
           "effort_source", "sandbox", "access_kind", "title", "first_prompt", "last_ts", "state",
           "last_agent_message", "n_cmds", "n_fail", "tokens", "usage_basis", "ctx_used", "ctx_window",
           "context_window_assumed", "rate_limits", "rate_ts", "last_action_ts", "events_total", "limit",
           "limit_info", "last_ok_ts")


class Packer:
    """Turns a full collection into the changes since the previous one."""

    def __init__(self) -> None:
        # path -> (session generation, events sent, messages sent, snapshots of events already sent).
        self.sent: dict[Path, tuple[int, int, int, dict[int, tuple]]] = {}

    def pack(self, runs: list[Run], quotas: dict) -> dict:
        out = []
        live_paths = set()
        for r in runs:
            s = r.session
            sd = None
            if s is not None:
                live_paths.add(s.path)
                ident, ev_sent, msg_sent, signatures = self.sent.get(s.path, (0, 0, 0, {}))
                reset = ident != s.gen or s.events_total < ev_sent
                if reset:
                    ev_sent = msg_sent = 0
                    signatures = {}
                current = list(s.events)
                events = current[-TAIL:] if reset else [
                    e for e in current if e.seq > ev_sent or
                    (e.seq in signatures and _event_signature(e) != signatures[e.seq])
                ]
                messages = s.messages[msg_sent:] if not reset else s.messages[-MESSAGES:]
                sd = {"path": str(s.path), "provider": s.provider, "reset": reset,
                      "attrs": {k: getattr(s, k) for k in SCALARS}, "events": events, "messages": messages,
                      "files": dict(s.files), "open_questions": dict(s.open_questions),
                      "in_flight": dict(s.in_flight)}
                sent_now = signatures if not reset else {}
                for ev in events:
                    sent_now[ev.seq] = _event_signature(ev)
                live_seq = {ev.seq for ev in current}
                sent_now = {seq: sig for seq, sig in sent_now.items() if seq in live_seq}
                self.sent[s.path] = (s.gen, s.events_total, len(s.messages), sent_now)
            out.append({"key": r.key, "provider": r.provider, "proc": r.proc, "label": r.label, "out": r.out,
                        "log": r.log, "session": sd})
        self.sent = {p: v for p, v in self.sent.items() if p in live_paths}
        return {"runs": out, "quotas": quotas}


def _event_signature(event: Event) -> tuple:
    """A stable snapshot catches mutable events such as merged reasoning blocks."""
    return tuple(getattr(event, name) for name in EVENT_FIELDS)


def _touched_since(session: Session, since: float) -> bool:
    """The collector's own test for history: an event or a write after `since`."""
    if (session.last_ts or 0) >= since:
        return True
    try:
        return os.stat(session.path).st_mtime >= since
    except OSError:
        return False


class Mirror:
    """The UI's copy of every session, kept up to date from packed changes."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.quotas: dict = {}

    def apply(self, payload: dict) -> list[Run]:
        runs = []
        seen = set()
        for item in payload["runs"]:
            s = None
            sd = item["session"]
            if sd is not None:
                seen.add(sd["path"])
                s = self.sessions.get(sd["path"])
                if s is None or sd["reset"]:
                    s = self.sessions[sd["path"]] = Session(Path(sd["path"]))
                    s.provider = sd["provider"]
                for k, v in sd["attrs"].items():
                    setattr(s, k, v)
                by_seq = {event.seq: event for event in s.events}
                if any(event.seq in by_seq and _event_signature(by_seq[event.seq]) != _event_signature(event)
                       for event in sd["events"]):
                    s.events_revision = getattr(s, "events_revision", 0) + 1
                by_seq.update((event.seq, event) for event in sd["events"])
                s.events.clear()
                s.events.extend(sorted(by_seq.values(), key=lambda event: event.seq)[-MAX_EVENTS:])
                s.messages.extend(sd["messages"])
                del s.messages[:-MESSAGES * 2]
                s.files, s.open_questions, s.in_flight = sd["files"], sd["open_questions"], sd["in_flight"]
            proc: ProcInfo | None = item["proc"]
            runs.append(Run(item["key"], item["provider"], s, proc, item["label"], item["out"], item["log"]))
        self.sessions = {p: s for p, s in self.sessions.items() if p in seen}
        self.quotas = payload["quotas"]
        return runs


def _loop(send, commands, hours: float, scan_procs: bool, interval: float, stop) -> None:
    """Send data snapshots; on a failed scan send an error-only envelope and retry with bounded backoff."""
    collector = Collector(all_providers(), Memory(), hours, scan_procs)
    packer = Packer()
    failures = 0
    while not stop():
        try:
            payload = packer.pack(collector.collect(), collector.quotas)
        except Exception as exc:
            # A transient file/provider failure must not permanently stop the reader. Keep the last good
            # mirror in place and report health separately from data.
            try:
                send({"error": _format_error(exc)})
            except (BrokenPipeError, EOFError, OSError):
                return
            failures += 1
            delay = min(0.25 * (2 ** min(failures - 1, 5)), 8.0)
        else:
            try:
                send(payload)
            except (BrokenPipeError, EOFError, OSError):
                return
            failures = 0
            delay = interval
        cmd = commands(delay)
        while cmd is not None:
            if cmd == "stop":
                return
            if isinstance(cmd, tuple) and cmd[0] == "hours":
                collector.hours = cmd[1]
            if cmd == "refresh":
                break
            cmd = commands(0)


def process_main(conn, hours: float, scan_procs: bool, interval: float) -> None:
    def commands(timeout: float):
        try:
            return conn.recv() if conn.poll(timeout) else None
        except (EOFError, OSError):  # the UI is gone: nobody left to read for
            return "stop"

    try:
        _loop(conn.send, commands, hours, scan_procs, interval, lambda: False)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


class Source:
    """Reader API for any renderer.

    `poll()` remains data-only (runs or None); `health`, `error`, and monotonic `last_update` describe freshness.
    """

    def __init__(self, hours: float, scan_procs: bool = True, interval: float = 2.0, process: bool = True):
        self.mirror = Mirror()
        self.process = process
        self.interval = interval
        self.args = (hours, scan_procs, interval)
        self._proc = None
        self._conn = None
        self._thread = None
        self._q: queue.Queue = queue.Queue()
        self._cmds: queue.Queue = queue.Queue()
        self._stopped = False
        self._started = False
        self._closed = False
        self.hours = hours  # the window shown; runs outside it are dropped even from a payload built before a change
        self._latest: list[Run] | None = None  # the runs last returned, so a new window can narrow them at once
        self._window_changed = False
        self.error: str | None = None  # sanitized diagnostic; excludes filenames and session payloads
        self.last_update: float | None = None  # time.monotonic() at the last successful collection received

    @property
    def health(self) -> str:
        """Reader health for a renderer: loading, ready, stale or error."""
        if self.error:
            return "error"
        if self.last_update is None:
            return "loading"
        return "stale" if time.monotonic() - self.last_update > max(3 * self.interval, 10.0) else "ready"

    @property
    def status(self) -> str:
        """Alias for `health`, kept convenient for desktop and terminal renderers."""
        return self.health

    def start(self) -> None:
        if self._started or self._stopped:
            return
        self._started = True
        try:
            if self.process:
                ctx = mp.get_context("spawn")
                self._conn, child = ctx.Pipe()
                self._proc = ctx.Process(target=process_main, args=(child, *self.args), daemon=True,
                                         name="bitacora-reader")
                try:
                    self._proc.start()
                except Exception:
                    child.close()
                    self._conn.close()
                    self._conn = None
                    raise
                child.close()
            else:
                def commands(timeout: float):
                    try:
                        return self._cmds.get(timeout=timeout) if timeout else self._cmds.get_nowait()
                    except queue.Empty:
                        return None

                self._thread = threading.Thread(target=_loop, daemon=True, name="bitacora-reader",
                                                args=(self._q.put, commands, *self.args, lambda: self._stopped))
                self._thread.start()
        except Exception as exc:
            self.error = _format_error(exc)

    def _send(self, cmd) -> None:
        if self.process:
            try:
                self._conn.send(cmd)
            except (OSError, AttributeError):
                pass
        else:
            self._cmds.put(cmd)

    def refresh(self) -> None:
        self._send("refresh")

    def set_hours(self, hours: float) -> None:
        self.hours = hours
        self._window_changed = True
        self._send(("hours", hours))

    def _windowed(self, runs: list[Run]) -> list[Run]:
        """Show only the window asked for last. The reader answers a change on its next pass, so a payload built
        for the previous window may still arrive first; live runs stay whatever their age, as documented."""
        since = time.time() - self.hours * 3600
        return [run for run in runs if run.live or run.session is None or _touched_since(run.session, since)]

    def poll(self) -> list[Run] | None:
        """Apply every pending update; returns the runs if anything arrived or a new window narrowed them."""
        runs = None
        if self.process:
            try:
                while self._conn is not None and self._conn.poll():
                    payload = self._conn.recv()
                    if payload.get("error"):
                        self.error = payload["error"]
                    elif "runs" in payload:
                        runs = self._windowed(self.mirror.apply(payload))
                        self.last_update = time.monotonic()
                        self.error = None
            except (EOFError, OSError) as exc:
                self.error = f"Reader connection closed: {_format_error(exc)}"
        else:
            while True:
                try:
                    payload = self._q.get_nowait()
                except queue.Empty:
                    break
                if payload.get("error"):
                    self.error = payload["error"]
                elif "runs" in payload:
                    runs = self._windowed(self.mirror.apply(payload))
                    self.last_update = time.monotonic()
                    self.error = None
        if self._started and not self._stopped and not self.error:
            if self.process and self._proc is not None and not self._proc.is_alive():
                self.error = f"Reader process exited with code {self._proc.exitcode}."
            elif not self.process and self._thread is not None and not self._thread.is_alive():
                self.error = "Reader thread stopped unexpectedly."
        if runs is None and self._window_changed and self._latest is not None:
            runs = self._windowed(self._latest)  # the window changed: narrow what is shown before the reader answers
        self._window_changed = False
        if runs is not None:
            self._latest = runs
        return runs

    def stop(self) -> None:
        if self._closed:
            return
        self._stopped = True
        self._send("stop")
        if self._proc is not None and self._proc.pid is not None:
            self._proc.join(timeout=2)
            if self._proc.is_alive():
                try:
                    self._proc.terminate()
                except OSError as exc:
                    self.error = f"Reader process termination failed: {_format_error(exc)}"
                self._proc.join(timeout=1)
                if self._proc.is_alive():
                    self.error = "Reader process did not stop before the shutdown timeout."
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError as exc:
                    self.error = f"Reader connection close failed: {_format_error(exc)}"
                self._conn = None
        elif self._thread is not None and self._thread.ident is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                self.error = "Reader thread did not stop before the shutdown timeout."
        self._closed = True
