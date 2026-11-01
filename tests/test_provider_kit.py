import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from provider_kit import assert_provider_contract

from bitacora.collector import Collector
from bitacora.model import Event, ProcInfo, Session
from bitacora.providers import all_providers
from bitacora.providers.base import Capabilities, Provider, Support, resume_value
from bitacora.sources import ReaderHealth, SessionRef, ref_key
from bitacora.store import Memory


class ToySession(Session):
    provider = "toy"

    def handle(self, o):
        ts = o.get("ts")
        text = o.get("text")
        if o.get("id"):
            self.session_id = o["id"]
        if text:
            self.messages.append((ts, text))
            self.last_agent_message = text
            self.add(Event(ts, "note", "", text))


class ToyProvider(Provider):
    name = "toy"
    label = "Toy"
    marker = "toy"
    session_cls = ToySession
    label_key = "toy"
    short_label_key = "toy"
    accent = "secondary"
    tier = "experimental"
    verified_versions = ()
    capabilities = Capabilities(
        processes=True, history=True, resume=Support.FULL, messages=Support.FULL, commands=Support.NONE,
        files=Support.NONE, questions=Support.NONE, usage=Support.NONE, context=Support.NONE,
        quota=Support.NONE, titles=Support.NONE, subagents=Support.NONE,
    )
    process_names = ("toy",)

    def __init__(self, home: Path):
        self._home = home

    def home(self) -> Path:
        return self._home

    def available(self) -> bool:
        return True

    def history(self, since):
        for path in sorted((self._home / "sessions").glob("*.jsonl")):
            try:
                if path.stat().st_mtime >= since:
                    yield path
            except OSError:
                continue

    def match(self, pi, claimed, peers=()):
        """The one known session file, so the kit's `file` branch of the match check really runs."""
        return self._home / "sessions" / "toy-1.jsonl"

    def resume(self, run):
        sid = resume_value(run.session.session_id) if run.session else None
        return {"argv": ["toy", "resume", sid], "cwd": None} if sid else None


def toy_home(tmp_path: Path) -> Path:
    home = tmp_path / "toy-home"
    (home / "sessions").mkdir(parents=True)
    path = home / "sessions" / "toy-1.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in (
        {"id": "toy-session-1", "ts": time.time(), "text": "hello"},
        {"ts": time.time(), "text": "world"},
    )) + "\n", encoding="utf-8")
    return home


class RowSession(Session):
    provider = "rows"


class RowReader:
    """One row read once; the row identity, not a path, is what a reader keeps."""

    def __init__(self, ref: SessionRef, row: str):
        self.ref = ref
        self._row = row
        self._reads = 0
        self._session = RowSession()
        self._session.session_id = row
        self._session.locator = ref.locator

    def update(self) -> bool:
        if self._reads:
            return False
        self._reads = 1
        self._session.records_total = 1
        return True

    def session(self) -> Session:
        return self._session

    def checkpoint(self) -> dict:
        return {"row": self._row, "reads": self._reads}

    def health(self) -> ReaderHealth:
        return ReaderHealth(state="ok", records=self._reads, parsed=self._reads)


class RowProvider(Provider):
    """A non-file source: one locator (a database), sessions told apart by their reference key alone."""

    name = "rows"
    label = "Rows"
    label_key = "rows"
    short_label_key = "rows"
    accent = "secondary"
    tier = "experimental"
    verified_versions = ()
    capabilities = Capabilities(
        processes=False, history=True, resume=Support.NONE, messages=Support.NONE, commands=Support.NONE,
        files=Support.NONE, questions=Support.NONE, usage=Support.NONE, context=Support.NONE,
        quota=Support.NONE, titles=Support.NONE, subagents=Support.NONE,
    )
    rows = ("row-1", "row-2")
    refs = tuple(SessionRef("rows", ref_key("rows", ("db", row)), "db", kind="sqlite") for row in rows)
    row_of = {ref.key: row for ref, row in zip(refs, rows, strict=True)}

    def home(self) -> Path:
        return Path("db")

    def discover(self, since):
        return iter(self.refs)

    def open(self, ref: SessionRef) -> RowReader:
        return RowReader(ref, self.row_of[ref.key])

    def match_ref(self, pi: ProcInfo, claimed: set[Path], peers=()) -> SessionRef | None:
        return next((ref for ref in self.refs if self.row_of[ref.key] == pi.session_id), None)


def test_shipped_providers_pass_the_conformance_kit(demo_env, tmp_path):
    from bitacora.i18n import S

    codex_sid = next(key.split(":", 1)[1] for key in demo_env["state"] if key.startswith("codex:"))
    # Both match by session id, so the kit checks a process that really resolves to a demo transcript.
    procs = {
        "codex": ProcInfo("codex", 4242, time.time(), ["codex"], session_id=codex_sid),
        "claude": ProcInfo("claude", 4243, time.time(), ["claude"],
                           session_id="5f0c2a1e-0000-4000-8000-000000000006"),
    }
    for provider in all_providers():
        assert_provider_contract(provider, tmp_path, proc=procs.get(provider.name))
        assert provider.label_key in S and provider.short_label_key in S


def test_a_toy_provider_passes_the_same_kit(tmp_path):
    assert_provider_contract(ToyProvider(toy_home(tmp_path)), tmp_path)


def test_collector_skips_history_and_quota_when_not_declared(tmp_path):
    class NoHistoryProvider(ToyProvider):
        capabilities = replace(ToyProvider.capabilities, history=False)

        def history(self, since):
            raise AssertionError("history must not be called")

        def quota(self, sessions):
            raise AssertionError("quota must not be called")

    collector = Collector([NoHistoryProvider(toy_home(tmp_path))], Memory(), 24, scan_procs=False)
    assert collector.collect() == []
    assert "toy" not in collector.quotas


def test_collector_derives_process_prefixes_from_providers(monkeypatch, tmp_path):
    seen = {}

    def fake_snapshot(names=None):
        seen["names"] = tuple(names or ())
        return []

    monkeypatch.setattr("bitacora.collector.procs.snapshot", fake_snapshot)
    provider = ToyProvider(toy_home(tmp_path))
    Collector([provider], Memory(), 24, scan_procs=True).collect()
    assert "toy" in seen["names"]


def test_non_file_refs_share_a_locator_and_match_by_identity(tmp_path):
    provider = RowProvider()
    assert_provider_contract(provider, tmp_path)
    pi = ProcInfo("rows", 4242, time.time(), ["rows"], session_id="row-2")
    assert provider.match(pi, set()) is None
    assert provider.match_ref(pi, set()) == provider.refs[1]


def test_kit_rejects_a_capability_that_the_session_contradicts(tmp_path):
    class Dishonest(ToyProvider):
        capabilities = replace(ToyProvider.capabilities, messages=Support.NONE)

    with pytest.raises(AssertionError):
        assert_provider_contract(Dishonest(toy_home(tmp_path)), tmp_path)
