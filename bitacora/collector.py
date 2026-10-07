"""Joins live processes with their session files, plus recent history, across providers."""
from __future__ import annotations

import time
from pathlib import Path

from bitacora import procs
from bitacora.model import ProcInfo, Run, Session
from bitacora.providers import Provider
from bitacora.store import State
from bitacora.util import label_from_out, label_from_prompt


class Collector:
    def __init__(self, providers: list[Provider], state: State, hours: float, scan_procs: bool = True):
        self.providers = providers
        self.state = state
        self.hours = hours
        self.scan_procs = scan_procs
        self.sessions: dict[Path, Session] = {}
        self.pid_map: dict[tuple[str, int], Path] = {}
        self.log_seen: dict[tuple[str, int], str | None] = {}
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

    def _live_run(self, prov: Provider, pi: ProcInfo, claimed: set[Path]) -> Run:
        key = (prov.name, pi.pid)
        path = self.pid_map.get(key)
        if path is None or not path.exists():
            path = prov.match(pi, claimed)
        s = None
        if path:
            self.pid_map[key] = path
            claimed.add(path)
            s = self._session(prov, path)
        sid = s.session_id if s else None
        skey = f"{prov.name}:{sid}" if sid else ""
        cached = self.state.session(skey) if skey else {}
        out = pi.out or cached.get("out")
        if pi.log is None:
            # The redirect is fixed at launch and walking parent processes is slow: look once per process.
            if key not in self.log_seen:
                self.log_seen[key] = cached.get("log") or procs.find_log(pi.pid, out, prov.marker)
            pi.log = self.log_seen[key]
        label = (label_from_out(out) or (s and s.title) or cached.get("label")
                 or (s and label_from_prompt(s.first_prompt)) or f"pid {pi.pid}")
        if skey:
            self.state.remember(skey, {"label": label, "out": out, "log": pi.log, "cmdline": pi.cmdline})
        return Run(skey or f"{prov.name}:pid{pi.pid}", prov.name, s, pi, label, out, pi.log)

    def collect(self) -> list[Run]:
        snap = procs.snapshot() if self.scan_procs else []
        runs: dict[str, Run] = {}
        since = time.time() - self.hours * 3600
        seen: set[Path] = set()
        alive: set[tuple[str, int]] = set()
        for prov in self.providers:
            if not prov.available():
                continue
            claimed: set[Path] = set()
            for pi in sorted(prov.procs(snap), key=lambda x: x.create):
                alive.add((prov.name, pi.pid))
                run = self._live_run(prov, pi, claimed)
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
                cached = self.state.session(key)
                label = (label_from_out(cached.get("out")) or s.title or cached.get("label")
                         or label_from_prompt(s.first_prompt) or f.stem[-12:])
                runs[key] = Run(key, prov.name, s, None, label, cached.get("out"), cached.get("log"))
            mine = [s for p, s in self.sessions.items() if s.provider == prov.name and p in seen]
            quota = prov.quota(mine)
            if quota:
                self.quotas[prov.name] = quota
        self.pid_map = {k: v for k, v in self.pid_map.items() if k in alive}
        self.log_seen = {k: v for k, v in self.log_seen.items() if k in alive}
        self.sessions = {p: s for p, s in self.sessions.items() if p in seen}
        self.state.save()
        return sorted(runs.values(), key=lambda r: (not r.live, -(r.start or 0)))
