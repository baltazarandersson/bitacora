import json
import time
from pathlib import Path

from provider_kit import assert_provider_contract

from bitacora.collector import Collector
from bitacora.model import Event, Session
from bitacora.providers import all_providers
from bitacora.providers.base import Capabilities, Provider, resume_value
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
    capabilities = Capabilities(titles=False)
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


def test_shipped_providers_pass_the_conformance_kit(demo_env, tmp_path):
    for provider in all_providers():
        assert_provider_contract(provider, tmp_path)


def test_a_toy_provider_passes_the_same_kit(tmp_path):
    assert_provider_contract(ToyProvider(toy_home(tmp_path)), tmp_path)


def test_collector_skips_history_and_quota_when_not_declared(tmp_path):
    class NoHistoryProvider(ToyProvider):
        capabilities = Capabilities(history=False, quota=False)

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
