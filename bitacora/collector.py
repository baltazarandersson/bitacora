"""Joins live processes with their session sources, plus recent history, across providers."""
from __future__ import annotations

import time

from bitacora import procs
from bitacora.model import ProcInfo, Run, Session
from bitacora.providers import Provider
from bitacora.providers.base import Capabilities
from bitacora.sources import HEALTH_RANK, ProviderHealth, SessionReader, SessionRef
from bitacora.store import Memory
from bitacora.util import _format_error, label_from_out, label_from_prompt

# A live process with no transcript this long after it started is not shown: nothing to read there.
NO_TRANSCRIPT_SECS = 600


def _reports_service(prov: Provider) -> bool:
    """Whether a provider overrides `service_running`; the others never need a process snapshot for it."""
    return type(prov).service_running is not Provider.service_running


def _worst_health(states) -> str:
    worst = None
    for state in states:
        if worst is None or HEALTH_RANK.get(state, HEALTH_RANK["ok"]) > HEALTH_RANK.get(worst, HEALTH_RANK["ok"]):
            worst = state
    return worst or "ok"


class Collector:
    def __init__(self, providers: list[Provider], memory: Memory, hours: float, scan_procs: bool = True):
        for prov in providers:
            if not isinstance(prov.capabilities, Capabilities):
                raise TypeError(f"{prov.name or type(prov).__name__}: capabilities must be a Capabilities instance")
        self.providers = providers
        self.memory = memory
        self.hours = hours
        self.scan_procs = scan_procs
        # Keyed by (provider, ref locator): two providers may expose the same locator.
        self.sessions: dict[tuple[str, object], Session] = {}
        self.readers: dict[tuple[str, object], SessionReader] = {}
        self.pid_map: dict[tuple[str, int, float], object] = {}
        self.log_seen: dict[tuple[str, int, float], str | None] = {}
        self.quotas: dict[str, dict] = {}
        self.errors: dict[str, str] = {}  # provider -> sanitized failure of the last cycle
        self.health: dict[str, ProviderHealth] = {}  # provider -> reader aggregate of the last cycle
        # Last runs read cleanly per provider, kept when a later read fails midway.
        self._last_good: dict[str, dict[str, Run]] = {}
        # Refs survive polls so a matched locator reopens without calling match_ref again.
        self._refs: dict[tuple[str, object], SessionRef] = {}
        self._updated: set[tuple[str, object]] = set()  # (provider, locator) already read once in this cycle

    def _session(self, prov: Provider, ref: SessionRef) -> Session:
        key = (ref.provider, ref.locator)
        reader = self.readers.get(key)
        if reader is None:
            reader = self.readers[key] = prov.open(ref)
            self.sessions[key] = reader.session()
        s = reader.session()
        if key not in self._updated:
            reader.update()
            self._updated.add(key)
        title = prov.title(s) if prov.capabilities.titles else None
        if title:
            s.title = title
        return s

    @staticmethod
    def _present(locator: object) -> bool:
        exists = getattr(locator, "exists", None)
        if callable(exists):
            try:
                return bool(exists())
            except OSError:
                return False
        return True  # non-file locators are validated by their reader, not by path existence

    def _live_run(self, prov: Provider, pi: ProcInfo, claimed: set, peers: list[ProcInfo]) -> Run:
        # PIDs are reused. Include process creation time so a new process cannot inherit an old transcript or log.
        key = (prov.name, pi.pid, pi.create)
        locator = self.pid_map.get(key)
        ref: SessionRef | None = None
        if locator is None or not self._present(locator):
            ref = prov.match_ref(pi, claimed, peers)
            locator = ref.locator if ref is not None else None
        s = None
        if locator is not None:
            self.pid_map[key] = locator
            claimed.add(locator)
            if ref is None:
                ref = self._refs.get((prov.name, locator)) or prov.ref_for(locator)
            self._refs[(prov.name, locator)] = ref
            s = self._session(prov, ref)
        if ref is not None and ref.kind != "file":
            skey = ref.key  # non-file sources own their run key; it never carries the locator
        else:
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
        run = Run(skey or f"{prov.name}:pid{pi.pid}", prov.name, s, pi, label, out, pi.log)
        run.meta = prov.run_meta(s, pi)
        return run

    @staticmethod
    def _history_key(prov: Provider, ref: SessionRef, s: Session) -> str:
        if ref.kind == "file":  # historical shape, even when the locator is a path
            return f"{prov.name}:{s.session_id}" if s.session_id else f"{prov.name}:{ref.locator}"
        return ref.key

    @staticmethod
    def _fallback_label(ref: SessionRef) -> str:
        stem = getattr(ref.locator, "stem", None)
        return str(stem)[-12:] if stem is not None else ref.key[-12:]

    def _provider_health(self, unavailable: set[str], errors: dict[str, str]) -> dict[str, ProviderHealth]:
        health: dict[str, ProviderHealth] = {}
        for prov in self.providers:
            if prov.name in unavailable:
                health[prov.name] = ProviderHealth("unavailable")
                continue
            readers = [reader.health() for reader in self.readers.values()
                       if reader.ref.provider == prov.name]
            # A session file deleted since discovery must not hide a reader that skipped records.
            present = [r for r in readers if r.state != "unavailable"] or readers
            state = "error" if prov.name in errors else _worst_health(r.state for r in present)
            health[prov.name] = ProviderHealth(
                state=state, readers=len(readers),
                records=sum(r.records for r in readers), parsed=sum(r.parsed for r in readers),
                discarded=sum(r.discarded for r in readers), unknown=sum(r.unknown for r in readers),
                details=tuple(sorted({r.detail for r in readers if r.detail})),
                last_good=max((r.last_good for r in readers if r.last_good is not None), default=None),
            )
        return health

    def collect(self) -> list[Run]:
        # One snapshot per cycle serves both the process providers and the service checks.
        names = tuple(sorted({name for prov in self.providers
                              if prov.capabilities.processes or _reports_service(prov)
                              for name in prov.process_names}))
        snap = procs.snapshot(names) if self.scan_procs else []
        service: dict[str, bool | None] = {}  # provider -> shared service running, from this cycle's snapshot
        runs: dict[str, Run] = {}
        since = time.time() - self.hours * 3600
        seen: set[tuple[str, object]] = set()
        alive: set[tuple[str, int, float]] = set()
        unavailable: set[str] = set()
        errors: dict[str, str] = {}
        failed: set[str] = set()  # providers whose session read failed: their last good state survives
        self._updated = set()
        for prov in self.providers:
            claimed: set[object] = set()
            try:
                if not prov.available():
                    unavailable.add(prov.name)
                    continue
                if self.scan_procs and _reports_service(prov):
                    service[prov.name] = prov.service_running(snap)
                procs_here = prov.procs(snap) if prov.capabilities.processes else []
                for pi in sorted(procs_here, key=lambda x: x.create):
                    alive.add((prov.name, pi.pid, pi.create))
                    run = self._live_run(prov, pi, claimed, procs_here)
                    if run.session is None and time.time() - pi.create > NO_TRANSCRIPT_SECS:
                        continue  # a process that never writes a transcript is not a session we can show
                    runs[run.key] = run
                seen |= {(prov.name, locator) for locator in claimed}
                if prov.capabilities.history:
                    for ref in prov.discover(since):
                        locator = ref.locator
                        if locator in claimed:
                            continue
                        self._refs[(prov.name, locator)] = ref
                        s = self._session(prov, ref)
                        seen.add((prov.name, locator))
                        key = self._history_key(prov, ref, s)
                        if key in runs:
                            continue
                        cached = self.memory.session(key)
                        label = (label_from_out(cached.get("out")) or s.title or cached.get("label")
                                 or label_from_prompt(s.first_prompt) or self._fallback_label(ref))
                        run = Run(key, prov.name, s, None, label, cached.get("out"), cached.get("log"))
                        run.meta = prov.run_meta(s, None)
                        runs[key] = run
                if prov.capabilities.quota:
                    mine = [s for (name, locator), s in self.sessions.items()
                            if s.provider == prov.name and (name, locator) in seen]
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
                seen |= {(prov.name, locator) for locator in claimed}
                # A partial read keeps the last good data: restore this provider's runs without overwriting
                # the fresh ones built before the failure and without borrowing another provider's rows.
                for key, run in self._last_good.get(prov.name, {}).items():
                    locator = None
                    if run.session is not None:
                        locator = (run.session.locator if run.session.locator is not None
                                   else run.session.path)
                        if locator in claimed:
                            continue  # a live run from this pass already represents that transcript
                    runs.setdefault(key, run)
                    if locator is not None:
                        seen.add((prov.name, locator))
        processes = {prov.name: bool(prov.capabilities.processes) for prov in self.providers}
        for run in runs.values():
            if run.provider in processes:
                # Neutral facts for the UI: whether a run of this provider can own a process, and whether its shared
                # service runs (only when this cycle looked at the process table).
                run.meta["processes"] = processes[run.provider]
                if service.get(run.provider) is not None:
                    run.meta["service"] = service[run.provider]
        self.errors = errors
        self.pid_map = {k: v for k, v in self.pid_map.items() if k in alive}
        self.log_seen = {k: v for k, v in self.log_seen.items() if k in alive}
        self.sessions = {p: s for p, s in self.sessions.items() if p in seen or s.provider in failed}
        self.readers = {p: r for p, r in self.readers.items()
                        if p in seen or r.ref.provider in failed}
        self._refs = {k: v for k, v in self._refs.items() if k in seen or k[0] in failed}
        self.health = self._provider_health(unavailable, errors)
        self.memory.save()
        return sorted(runs.values(), key=lambda r: (not r.live, -(r.start or 0)))
