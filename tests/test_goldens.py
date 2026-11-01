"""Golden projection of the demo environment.

Provider refactors must not change what the user sees. The projection keeps only normalized, time-independent
data: no run keys, absolute host paths, absolute timestamps, durations or wall-clock values. Relative file and
repo paths stay because they are part of the visible payload. Regenerate the fixture explicitly with
UPDATE_GOLDENS=1 after a deliberate behavior change.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from test_opencode import build_db

from bitacora.collector import Collector
from bitacora.providers import all_providers
from bitacora.providers.opencode import OpenCodeProvider
from bitacora.store import Memory

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "codex-claude.json"
OPENCODE_GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "opencode.json"
# Limit messages embed a local wall-clock time; it is not part of the user-visible contract.
CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s?(?:AM|PM)\b", re.I)


def _text(value):
    return CLOCK.sub("<clock>", value) if isinstance(value, str) else value


def _quota(quota: dict | None) -> dict | None:
    if not quota:
        return None
    limit = quota.get("limit")
    if limit:
        limit = {"window": limit.get("window"), "name": limit.get("name"), "type": limit.get("type")}
    windows = [{"id": window.get("id"), "used_percent": window.get("used_percent")}
               for window in quota.get("windows") or []]
    return {"reporting": quota.get("reporting"), "windows": sorted(windows, key=lambda w: str(w["id"])),
            "limit": limit}


def _run(run) -> dict:
    session = run.session
    return {
        "provider": run.provider,
        "label": _text(run.label),
        "status": run.status,
        "origin": run.origin,
        "origin_raw": run.origin_raw,
        "origin_kind": run.origin_kind,
        "headless": run.headless,
        "repo": run.repo,
        "model": run.model,
        "effort": run.effort,
        "usage_basis": session.usage_basis if session else None,
        "tokens": session.tokens if session else None,
        "context_tokens": session.context_tokens if session else None,
        "ctx_window": session.ctx_window if session else None,
        "n_cmds": session.n_cmds if session else None,
        "n_fail": session.n_fail if session else None,
        "n_files": len(session.files) if session else None,
        "state": session.state if session else None,
        "title": _text(session.title) if session else None,
        "first_prompt": _text(session.first_prompt) if session else None,
        "last_agent_message": _text(session.last_agent_message) if session else None,
        "open_questions": sorted(session.open_questions) if session else [],
        "messages": [_text(text) for _, text in session.messages] if session else [],
        "events": [{"kind": event.kind, "text": _text(event.text), "detail": _text(event.detail),
                    "sub": event.sub, "ok": event.ok, "code": event.code, "plus": event.plus,
                    "minus": event.minus, "count": event.count}
                   for event in session.events] if session else [],
        "files": [{"path": path, "kind": record[0], "count": record[1], "plus": record[3],
                   "minus": record[4]}
                  for path, record in sorted(session.files.items())] if session else [],
    }


# The demo also holds OpenCode runs. They are checked by their own assertions, so this golden stays byte-identical.
GOLDEN_PROVIDERS = ("codex", "claude")


def project(demo_env) -> dict:
    memory = Memory()
    for key, entry in demo_env["state"].items():
        memory.remember(key, entry)
    collector = Collector(all_providers(), memory, hours=24, scan_procs=False)
    runs = collector.collect()
    return {
        "runs": sorted((_run(run) for run in runs if run.provider in GOLDEN_PROVIDERS),
                       key=lambda entry: (entry["provider"], entry["label"])),
        "quotas": {name: _quota(quota) for name, quota in sorted(collector.quotas.items())
                   if name in GOLDEN_PROVIDERS},
    }


def test_demo_holds_three_opencode_runs_in_three_states(demo_env):
    runs = Collector(all_providers(), Memory(), hours=24, scan_procs=False).collect()
    assert len(runs) == 19  # 16 Codex and Claude runs and 3 OpenCode runs
    assert sorted(run.status for run in runs if run.provider == "opencode") == ["limited", "running", "your_turn"]


def test_demo_projection_matches_the_golden_fixture(demo_env):
    actual = project(demo_env)
    if os.environ.get("UPDATE_GOLDENS") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8", newline="\n")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected


def project_opencode(db: Path) -> dict:
    """The seeded OpenCode store, projected like the demo: runs only, no keys, paths or timestamps."""
    collector = Collector([OpenCodeProvider()], Memory(), hours=24, scan_procs=False)
    runs = [_run(run) for run in collector.collect()]
    return {"runs": sorted(runs, key=lambda entry: json.dumps(entry, sort_keys=True))}


def test_opencode_projection_matches_its_golden_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db", scenarios=True)))
    actual = project_opencode(tmp_path / "opencode.db")
    if os.environ.get("UPDATE_GOLDENS") == "1":
        OPENCODE_GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                   encoding="utf-8", newline="\n")
    expected = json.loads(OPENCODE_GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
