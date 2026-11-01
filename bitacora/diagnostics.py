"""Opt-in local diagnostics: counters, latency percentiles and memory, never session content or network."""
from __future__ import annotations

import platform
import tempfile
import time
import tracemalloc
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import psutil

from bitacora import __version__, desktop, model, procs, worker
from bitacora.collector import Collector
from bitacora.providers import all_providers
from bitacora.providers.base import Provider, Support
from bitacora.sources import ProviderHealth
from bitacora.store import Memory, load_config

SUPPORT_FIELDS = ("resume", "messages", "commands", "files", "questions", "usage", "context", "quota",
                  "titles", "subagents")


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 2)


def _quarantine_summary(sessions: Iterable[model.Session]) -> dict:
    """Aggregate discarded-record metadata only: no content, paths, record numbers or exception messages."""
    sessions = list(sessions)
    retained = [entry for session in sessions for entry in session.quarantine]
    return {
        "sessions": sum(1 for session in sessions if session.discarded > 0),
        "records_total": sum(session.records_total for session in sessions),
        "discarded": sum(session.discarded for session in sessions),
        "kinds": dict(sorted(Counter(kind for _, kind, _, _ in retained).items())),
        "exceptions": dict(sorted(Counter(name for _, _, name, _ in retained).items())),
        "sample_limit": model.MAX_QUARANTINE,
    }


def _process_prefixes(providers: Iterable[Provider]) -> tuple[str, ...]:
    """Executable prefixes worth scanning, only from providers that report processes."""
    return tuple(sorted({name for provider in providers if provider.capabilities.processes
                         for name in provider.process_names}))


def _capabilities(capabilities) -> dict:
    """Support values as plain strings: no enums and no provider objects cross the JSON boundary."""
    out = {"processes": bool(capabilities.processes), "history": bool(capabilities.history)}
    for name in SUPPORT_FIELDS:
        out[name] = Support(getattr(capabilities, name)).value
    return out


def _health_summary(health: dict[str, ProviderHealth]) -> dict:
    return {name: {"state": item.state, "readers": item.readers, "records": item.records,
                   "parsed": item.parsed, "discarded": item.discarded, "unknown": item.unknown,
                   "details": list(item.details), "last_good": item.last_good}
            for name, item in sorted(health.items())}


def collect(passes: int = 3, hours: float = 24, memory: bool = False) -> dict:
    """Scan the real collector a few times and describe the work without exporting session data.

    ``quarantine`` totals cover the passes of this run; ``kinds`` and ``exceptions`` are a bounded sample
    because each session retains at most ``sample_limit`` entries as metadata (record number, kind,
    exception class and record size), never record content, paths or exception messages.
    """
    passes = max(1, min(int(passes or 1), 10))
    providers = all_providers(enabled=load_config().providers)
    disabled = sorted({provider.name for provider in all_providers()} - {provider.name for provider in providers})
    scans: list[float] = []
    runs = []
    with tempfile.TemporaryDirectory(prefix="bitacora-diagnostics-") as tmp:
        # Diagnostics must not write labels or paths into the user's sessions.json.
        collector = Collector(providers, Memory(Path(tmp) / "sessions.json"), hours=hours, scan_procs=True)
        if memory:
            tracemalloc.start()
        for _ in range(passes):
            started = time.perf_counter()
            runs = collector.collect()
            scans.append((time.perf_counter() - started) * 1000)
        peak = tracemalloc.get_traced_memory()[1] if memory else 0
        if memory:
            tracemalloc.stop()

    prefixes = _process_prefixes(providers)
    started = time.perf_counter()
    visible = procs.snapshot(prefixes)
    snapshot_ms = (time.perf_counter() - started) * 1000
    process = psutil.Process()
    walk_secs = next((provider.WALK_SECS for provider in providers if hasattr(provider, "WALK_SECS")), None)
    out = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "hours": hours,
        "passes": passes,
        "providers": [
            {"name": provider.name, "available": provider.available(),
             "capabilities": _capabilities(provider.capabilities),
             "tier": provider.tier, "verified_versions": list(provider.verified_versions)}
            for provider in providers
        ],
        "disabled": disabled,
        "health": _health_summary(collector.health),
        "process": {"pid": process.pid, "rss_mb": round(process.memory_info().rss / 1048576, 1),
                    "cpu_percent": round(process.cpu_percent(interval=0.1), 1)},
        "processes": {"prefixes": list(prefixes), "visible": len(visible),
                      "snapshot_ms": round(snapshot_ms, 2)},
        "scan": {"ms": [round(value, 2) for value in scans],
                 "p50_ms": _percentile(scans, 0.5), "p95_ms": _percentile(scans, 0.95),
                 "runs": len(runs), "sessions": sum(1 for run in runs if run.session is not None),
                 "by_provider": {provider.name: sum(1 for run in runs if run.provider == provider.name)
                                 for provider in providers},
                 "by_status": dict(sorted(Counter(run.status for run in runs).items()))},
        "quarantine": _quarantine_summary(collector.sessions.values()),
        "limits": {"max_events": model.MAX_EVENTS, "tail_events": worker.TAIL,
                   "payload_messages": worker.MESSAGES, "page_limit": worker.PAGE_LIMIT,
                   "read_limit": desktop.READ_LIMIT, "walk_secs": walk_secs},
    }
    if memory:
        out["memory"] = {"tracemalloc_peak_mb": round(peak / 1048576, 2),
                         "note": "tracemalloc inflates parse timings"}
    return out
