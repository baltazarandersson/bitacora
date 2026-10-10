"""Joins live processes with their session files, plus recent history, across providers."""
from __future__ import annotations

import time
from pathlib import Path

from bitacora import procs
from bitacora.model import ProcInfo, Run, Session
from bitacora.providers import Provider
from bitacora.store import Memory
from bitacora.util import label_from_out, label_from_prompt

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

    def _session(self, prov: Provider, path: Path) -> Session:
        s = self.sessions.get(path)
        if s is None:
            s = self.sessions[path] = prov.session_cls(path)
        s.update()
        title = prov.title(s)
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
        snap = procs.snapshot() if self.scan_procs else []
        runs: dict[str, Run] = {}
        since = time.time() - self.hours * 3600
        seen: set[Path] = set()
        alive: set[tuple[str, int, float]] = set()
        for prov in self.providers:
            if not prov.available():
                continue
            claimed: set[Path] = set()
            procs_here = prov.procs(snap)
            for pi in sorted(procs_here, key=lambda x: x.create):
                alive.add((prov.name, pi.pid, pi.create))
                run = self._live_run(prov, pi, claimed, procs_here)
                if run.session is None and time.time() - pi.create > NO_TRANSCRIPT_SECS:
                    continue  # a process that never writes a transcript is not a session we can show
                runs[run.key] = run
            seen |= claimed
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
            mine = [s for p, s in self.sessions.items() if s.provider == prov.name and p in seen]
            quota = prov.quota(mine)
            if quota:
                self.quotas[prov.name] = quota
            else:
                self.quotas.pop(prov.name, None)
        self.pid_map = {k: v for k, v in self.pid_map.items() if k in alive}
        self.log_seen = {k: v for k, v in self.log_seen.items() if k in alive}
        self.sessions = {p: s for p, s in self.sessions.items() if p in seen}
        self.memory.save()
        return sorted(runs.values(), key=lambda r: (not r.live, -(r.start or 0)))
