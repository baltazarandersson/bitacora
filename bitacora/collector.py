"""Joins live processes with their session files, plus recent history, across providers."""
from __future__ import annotations

import time
from pathlib import Path

from bitacora import procs
from bitacora.model import ProcInfo, Run, Session
from bitacora.providers import Provider
from bitacora.store import Memory
from bitacora.util import _format_error, label_from_out, label_from_prompt

# A live process with no transcript this long after it started is not shown: nothing to read there.
NO_TRANSCRIPT_SECS = 600


class Collector:
    def __init__(self, providers: list[Provider], memory: Memory, hours: float, scan_procs: bool = True):
        self.providers = providers
        self.memory = memory
        self.hours = hours
        self.scan_procs = scan_procs
        self.sessions: dict[Path, Session] = {}
        self.pid_map: dict[tuple[str, int, float], Path] = {}
        self.log_seen: dict[tuple[str, int, float], str | None] = {}
        self.quotas: dict[str, dict] = {}
        self.errors: dict[str, str] = {}  # provider -> sanitized failure of the last cycle
        # Last runs read cleanly per provider, kept when a later read fails midway.
        self._last_good: dict[str, dict[str, Run]] = {}

    def _session(self, prov: Provider, path: Path) -> Session:
        s = self.sessions.get(path)
        if s is None:
            s = self.sessions[path] = prov.session_cls(path)
        s.update()
        title = prov.title(s) if prov.capabilities.titles else None
        if title:
            s.title = title
        return s

    def _live_run(self, prov: Provider, pi: ProcInfo, claimed: set[Path], peers: list[ProcInfo]) -> Run:
        # PIDs are reused. Include process creation time so a new process cannot inherit an old transcript or log.
        key = (prov.name, pi.pid, pi.create)
        path = self.pid_map.get(key)
        if path is None or not path.exists():
            path = prov.match(pi, claimed, peers)
        s = None
        if path:
            self.pid_map[key] = path
            claimed.add(path)
            s = self._session(prov, path)
        sid = s.session_id if s else None
        skey = f"{prov.name}:{sid}" if sid else ""
        cached = self.memory.session(skey) if skey else {}
        out = pi.out or cached.get("out")
        if pi.log is None:
            # The redirect is fixed at launch and walking parent processes is slow: look once per process.
            if key not in self.log_seen:
                self.log_seen[key] = cached.get("log") or procs.find_log(pi.pid, out, prov.marker)
            pi.log = self.log_seen[key]
        label = (label_from_out(out) or (s and s.title) or cached.get("label")
                 or (s and label_from_prompt(s.first_prompt)) or f"pid {pi.pid}")
        if skey:
            self.memory.remember(skey, {"label": label, "out": out, "log": pi.log, "cmdline": pi.cmdline})
        return Run(skey or f"{prov.name}:pid{pi.pid}", prov.name, s, pi, label, out, pi.log)

    def collect(self) -> list[Run]:
        names = tuple(sorted({name for prov in self.providers if prov.capabilities.processes
                              for name in prov.process_names}))
        snap = procs.snapshot(names) if self.scan_procs else []
        runs: dict[str, Run] = {}
        since = time.time() - self.hours * 3600
        seen: set[Path] = set()
        alive: set[tuple[str, int, float]] = set()
        errors: dict[str, str] = {}
        failed: set[str] = set()  # providers whose session read failed: their last good state survives
        for prov in self.providers:
            claimed: set[Path] = set()
            try:
                if not prov.available():
                    continue
                procs_here = prov.procs(snap) if prov.capabilities.processes else []
                for pi in sorted(procs_here, key=lambda x: x.create):
                    alive.add((prov.name, pi.pid, pi.create))
                    run = self._live_run(prov, pi, claimed, procs_here)
                    if run.session is None and time.time() - pi.create > NO_TRANSCRIPT_SECS:
                        continue  # a process that never writes a transcript is not a session we can show
                    runs[run.key] = run
                seen |= claimed
                if prov.capabilities.history:
                    for f in prov.history(since):
                        if f in claimed:
                            continue
                        s = self._session(prov, f)
                        seen.add(f)
                        key = f"{prov.name}:{s.session_id or f}"
                        if key in runs:
                            continue
                        cached = self.memory.session(key)
                        label = (label_from_out(cached.get("out")) or s.title or cached.get("label")
                                 or label_from_prompt(s.first_prompt) or f.stem[-12:])
                        runs[key] = Run(key, prov.name, s, None, label, cached.get("out"), cached.get("log"))
                if prov.capabilities.quota:
                    mine = [s for p, s in self.sessions.items() if s.provider == prov.name and p in seen]
                    try:
                        quota = prov.quota(mine)
                    except Exception as exc:  # keep the last known quota: it carries its own as_of
                        errors[prov.name] = _format_error(exc)
                    else:
                        if quota:
                            self.quotas[prov.name] = quota
                        else:
                            self.quotas.pop(prov.name, None)
                self._last_good[prov.name] = {k: r for k, r in runs.items() if r.provider == prov.name}
            except Exception as exc:
                # One provider failing must not hide the others. Keep its runs and claimed transcripts.
                errors[prov.name] = _format_error(exc)
                failed.add(prov.name)
                seen |= claimed
                # A partial read keeps the last good data: restore this provider's runs without overwriting
                # the fresh ones built before the failure and without borrowing another provider's rows.
                for key, run in self._last_good.get(prov.name, {}).items():
                    if run.session is not None and run.session.path in claimed:
                        continue  # a live run from this pass already represents that transcript
                    runs.setdefault(key, run)
                    if run.session is not None:
                        seen.add(run.session.path)
        self.errors = errors
        self.pid_map = {k: v for k, v in self.pid_map.items() if k in alive}
        self.log_seen = {k: v for k, v in self.log_seen.items() if k in alive}
        self.sessions = {p: s for p, s in self.sessions.items() if p in seen or s.provider in failed}
        self.memory.save()
        return sorted(runs.values(), key=lambda r: (not r.live, -(r.start or 0)))
