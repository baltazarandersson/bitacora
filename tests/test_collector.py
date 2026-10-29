import time
from pathlib import Path

import pytest

from bitacora.collector import Collector
from bitacora.model import ProcInfo, Session
from bitacora.providers import Provider, all_providers
from bitacora.providers.base import Capabilities
from bitacora.store import Memory
from bitacora.views import BUILTIN, all_views, counts, custom_view


def collect(demo_env):
    state = Memory()
    for key, entry in demo_env["state"].items():
        state.remember(key, entry)
    c = Collector(all_providers(), state, hours=24, scan_procs=False)
    return c, {r.label: r for r in c.collect()}


def test_statuses_and_labels(demo_env):
    c, runs = collect(demo_env)
    assert len(runs) == 16
    assert {r.status for r in runs.values()} == {
        "asking", "running", "done", "stopped", "limited", "quiet", "failed", "your_turn", "unknown",
    }
    assert runs["auth-cookies"].status == "running"
    assert runs["pg17-plan"].status == "asking"
    assert runs["a11y-audit"].status == "done"
    assert runs["flaky-orders"].status == "stopped"
    assert runs["backfill-dedupe"].status == "limited"
    assert runs["Review PR 42: rate limiter"].status == "quiet"
    assert runs["Split orders into order lines"].status == "failed"
    assert runs["Fix flaky cart widget test"].status == "your_turn"
    assert sum(r.status == "asking" for r in runs.values()) == 2
    assert runs["a11y-audit"].out.endswith("a11y-audit.result.md")
    assert runs["auth-cookies"].log.endswith("auth-cookies.log")
    assert runs["auth-cookies"].origin == "exec"
    assert runs["Review PR 42: rate limiter"].origin == "claude-desktop"
    codex_windows = {window["id"]: window for window in c.quotas["codex"]["windows"]}
    assert codex_windows["5h"]["used_percent"] == 38.0
    assert codex_windows["week"]["used_percent"] == 84.0
    assert c.quotas["codex"].get("limit") is None  # the Codex cut-off is in its run, not in force now
    assert c.quotas["claude"]["reporting"] == "limit-only"
    assert c.quotas["claude"]["limit"]["window"] == "sonnet-week"
    assert c.quotas["claude"]["limit"]["name"] == "weekly Sonnet"
    # Codex task_complete errors and Claude API errors keep their normalized turn state.
    codex_cut = runs["backfill-dedupe"].session
    assert any(e.kind == "err" and "usage limit" in e.text and "Try again at" in e.text for e in codex_cut.events)
    claude_limit = runs["Fix login redirect loop"].session
    assert claude_limit.limit_info["type"] == "seven_day_sonnet" and claude_limit.limit_info["resets_at"] > 0
    assert runs["offline-sync-retry"].model == "gpt-6.1-sol" and runs["offline-sync-retry"].effort == "max"


def test_live_sessions_sort_first(demo_env):
    c, _ = collect(demo_env)
    ordered = c.collect()
    live = [r.live for r in ordered]
    assert live == sorted(live, reverse=True)
    assert sum(live) == 4


def test_history_window(demo_env):
    state = Memory()
    c = Collector(all_providers(), state, hours=1, scan_procs=False)
    labels = {r.label for r in c.collect()}
    assert "Fix login redirect loop" not in labels  # 7h old
    assert len(labels) == 6


def test_builtin_view_counts(demo_env):
    _, runs = collect(demo_env)
    rs = list(runs.values())
    got = {v.id: counts(v, rs) for v in BUILTIN}
    assert got == {"background": (9, 3), "everything": (16, 4), "interactive": (7, 1), "live": (4, 4)}


def test_custom_views():
    v = custom_view("mine", {"name": "Codex bg", "providers": ["codex"], "kind": "background"})
    assert v.providers == ("codex",) and v.headless is True and not v.builtin
    assert custom_view("bad", {"providers": "codex"}) is None
    views = all_views({"mine": {"name": "x"}, "live": {"name": "shadowed"}})
    assert [x.id for x in views] == ["background", "everything", "interactive", "live", "mine"]


def test_packer_sends_only_what_changed(demo_env):
    from bitacora.worker import Mirror, Packer
    c = Collector(all_providers(), Memory(), hours=24, scan_procs=False)
    packer, mirror = Packer(), Mirror()
    first = mirror.apply(packer.pack(c.collect(), c.quotas))
    assert len(first) == 16
    live = next(r for r in first if r.label.startswith("Update CHANGELOG"))
    n_events = len(live.session.events)
    demo_env["live"][1].say("one more line")  # the Claude run writes a message
    second = packer.pack(c.collect(), c.quotas)
    changed = [r["session"] for r in second["runs"] if r["session"] and r["session"]["events"]]
    assert len(changed) == 1 and not changed[0]["reset"]
    runs = mirror.apply(second)
    again = next(r for r in runs if r.key == live.key)
    assert len(again.session.events) == n_events + 1
    assert again.session is live.session  # the mirror updates sessions in place


def test_packer_resends_a_new_session_object_for_the_same_file(demo_env):
    """A new Session for a file the UI already knows must send everything again, not just what is new."""
    from bitacora.worker import Mirror, Packer

    c = Collector(all_providers(), Memory(), 24, scan_procs=False)
    packer, mirror = Packer(), Mirror()
    first = mirror.apply(packer.pack(c.collect(), c.quotas))
    live = {r.key: r for r in first}
    assert live
    for path in list(c.sessions):  # drop the cache: the next collection builds fresh Session objects
        c.sessions[path].gen = -1
    again = mirror.apply(packer.pack(c.collect(), c.quotas))
    for r in again:
        if r.session is not None:
            assert len(r.session.events) == len(live[r.key].session.events)


class SilentProvider(Provider):
    """Live processes and no transcripts at all."""

    name = "silent"
    label = "Silent"

    def __init__(self, procs_):
        self._procs = procs_

    def home(self):
        return Path(".")

    def available(self):
        return True

    def procs(self, snap):
        return self._procs

    def history(self, since):
        return []

    def quota(self, sessions):
        return None


def test_live_process_without_transcript_is_hidden_only_after_a_while():
    import time

    from bitacora.collector import NO_TRANSCRIPT_SECS
    from bitacora.model import ProcInfo

    now = time.time()
    fresh = ProcInfo("silent", 1, now - 30, ["claude"], proc_cwd="/nowhere")
    old = ProcInfo("silent", 2, now - NO_TRANSCRIPT_SECS - 60, ["claude"], proc_cwd="/nowhere")
    c = Collector([SilentProvider([fresh, old])], Memory(), hours=24, scan_procs=False)
    runs = c.collect()
    assert [r.proc.pid for r in runs] == [1]


class RecordingSession(Session):
    def __init__(self, path):
        self.records = []
        super().__init__(path)

    def handle(self, obj):
        self.records.append(obj)


class SwitchProvider(Provider):
    """A provider whose data and failures each test controls."""

    def __init__(self, name, home, paths=(), procs_now=(), fail=(), quota=None):
        self.name = name
        self.label = name.title()
        self.home_dir = Path(home)
        self.paths = list(paths)
        self.procs_now = list(procs_now)
        self.fail = set(fail)
        self.quota_value = quota
        self.match_path = None

    def home(self):
        return self.home_dir

    def available(self):
        return True

    def procs(self, _snap):
        if "procs" in self.fail:
            raise RuntimeError("SECRET procs at /private/session.jsonl")
        return self.procs_now

    def match(self, _pi, _claimed, _peers=()):
        return self.match_path

    def history(self, _since):
        if "history" in self.fail:
            raise RuntimeError("SECRET history at /private/session.jsonl")
        return self.paths

    def quota(self, _sessions):
        if "quota" in self.fail:
            raise RuntimeError("SECRET quota at /private/session.jsonl")
        return self.quota_value


def test_a_failing_provider_does_not_stop_the_cycle_and_recovers(tmp_path):
    healthy_path = tmp_path / "healthy.jsonl"
    healthy_path.write_text('{"value": 1}\n', encoding="utf-8")
    bad = SwitchProvider("bad", tmp_path, fail={"procs"})
    good = SwitchProvider("good", tmp_path, paths=[healthy_path])
    bad.session_cls = good.session_cls = RecordingSession
    collector = Collector([bad, good], Memory(tmp_path / "sessions.json"), hours=24, scan_procs=False)

    runs = collector.collect()
    assert [run.provider for run in runs] == ["good"]
    assert [run.label for run in runs] == ["healthy"]
    assert collector.errors == {"bad": "RuntimeError"}
    assert "SECRET" not in collector.errors["bad"]

    bad.fail.clear()
    assert [run.provider for run in collector.collect()] == ["good"]
    assert collector.errors == {}


def test_processes_capability_controls_snapshot_names_and_live_runs(tmp_path, monkeypatch):
    requested = []

    def fake_snapshot(names):
        requested.append(tuple(names))
        return []

    monkeypatch.setattr("bitacora.collector.procs.snapshot", fake_snapshot)
    now = time.time()
    scanning = SwitchProvider("codex", tmp_path,
                              procs_now=[ProcInfo("codex", 42, now - 30, ["codexbin"])])
    scanning.process_names = ("codexbin",)
    scanning.capabilities = Capabilities(processes=True)
    skipped = SwitchProvider("claude", tmp_path,
                             procs_now=[ProcInfo("claude", 43, now - 30, ["claudebin"])])
    skipped.process_names = ("claudebin",)
    skipped.capabilities = Capabilities(processes=False)

    def must_not_scan(_snap):
        pytest.fail("a provider without processes must not be asked for them")

    skipped.procs = must_not_scan
    collector = Collector([scanning, skipped], Memory(tmp_path / "sessions.json"), hours=24)

    runs = collector.collect()
    assert requested == [("codexbin",)]  # the disabled provider's names never reach the snapshot
    assert [(run.key, run.provider) for run in runs] == [("codex:pid42", "codex")]
    assert runs[0].live
    assert collector.errors == {}


def test_history_failure_keeps_live_runs_and_claimed_sessions(tmp_path):
    live_path = tmp_path / "live.jsonl"
    live_path.write_text('{"value": 1}\n', encoding="utf-8")
    provider = SwitchProvider("codex", tmp_path,
                              procs_now=[ProcInfo("codex", 42, time.time() - 30, ["codex"])],
                              fail={"history"})
    provider.session_cls = RecordingSession
    provider.match_path = live_path
    collector = Collector([provider], Memory(tmp_path / "sessions.json"), hours=24, scan_procs=False)

    runs = collector.collect()
    assert [run.proc.pid for run in runs] == [42]
    assert collector.sessions[live_path].records == [{"value": 1}]
    assert collector.errors == {"codex": "RuntimeError"}


def test_quota_failure_keeps_the_last_known_quota(tmp_path):
    quota = {"used_percent": 10.0, "as_of": 1.0}
    provider = SwitchProvider("codex", tmp_path, quota=quota)
    collector = Collector([provider], Memory(tmp_path / "sessions.json"), hours=24, scan_procs=False)
    collector.collect()
    assert collector.quotas == {"codex": quota}

    provider.fail.add("quota")
    collector.collect()
    assert collector.quotas == {"codex": quota}
    assert collector.errors == {"codex": "RuntimeError"}


def session_cls_for(provider_name):
    """A recording session that identifies its provider, as real provider sessions do."""
    return type(f"{provider_name.title()}RecordingSession", (RecordingSession,), {"provider": provider_name})


def test_history_failure_keeps_the_last_good_runs(tmp_path):
    history_path = tmp_path / "history.jsonl"
    history_path.write_text('{"value": 1}\n', encoding="utf-8")
    other_path = tmp_path / "other.jsonl"
    other_path.write_text('{"value": 2}\n', encoding="utf-8")
    flaky = SwitchProvider("codex", tmp_path, paths=[history_path])
    steady = SwitchProvider("claude", tmp_path, paths=[other_path])
    flaky.session_cls = session_cls_for("codex")
    steady.session_cls = session_cls_for("claude")
    collector = Collector([flaky, steady], Memory(tmp_path / "sessions.json"), hours=24, scan_procs=False)

    assert {(run.provider, run.label) for run in collector.collect()} == {("codex", "history"),
                                                                         ("claude", "other")}
    assert collector.errors == {}

    flaky.fail.add("history")
    runs = collector.collect()
    # The failed provider keeps its last good run and session; the healthy provider is untouched.
    assert {(run.provider, run.label) for run in runs} == {("codex", "history"), ("claude", "other")}
    assert history_path in collector.sessions and other_path in collector.sessions
    assert collector.errors == {"codex": "RuntimeError"}


def test_a_failing_process_scan_keeps_the_last_live_run_without_duplicates(tmp_path):
    live_path = tmp_path / "live.jsonl"
    live_path.write_text('{"value": 1}\n', encoding="utf-8")
    provider = SwitchProvider("codex", tmp_path,
                              procs_now=[ProcInfo("codex", 42, time.time() - 30, ["codex"])])
    provider.session_cls = session_cls_for("codex")
    provider.match_path = live_path
    collector = Collector([provider], Memory(tmp_path / "sessions.json"), hours=24, scan_procs=False)

    first = collector.collect()
    assert [run.key for run in first] == ["codex:pid42"] and first[0].live

    provider.fail.add("history")  # a fresh live run and the cached one share a key: keep one row
    second = collector.collect()
    assert [run.key for run in second] == ["codex:pid42"] and second[0].live
    assert second[0] is not first[0]

    provider.fail.add("procs")  # nothing fresh this pass: the last good live run is retained
    third = collector.collect()
    assert [run.key for run in third] == ["codex:pid42"] and third[0].live
    assert third[0] is first[0]
    assert live_path in collector.sessions
    assert collector.errors == {"codex": "RuntimeError"}


def test_quota_failure_does_not_change_history_retention(tmp_path):
    history_path = tmp_path / "history.jsonl"
    history_path.write_text('{"value": 1}\n', encoding="utf-8")
    quota = {"used_percent": 10.0, "as_of": 1.0}
    provider = SwitchProvider("codex", tmp_path, paths=[history_path], quota=quota)
    provider.session_cls = session_cls_for("codex")
    collector = Collector([provider], Memory(tmp_path / "sessions.json"), hours=24, scan_procs=False)
    assert [run.label for run in collector.collect()] == ["history"]

    provider.paths.clear()  # the file aged out of the history window
    provider.fail.add("quota")
    assert collector.collect() == []
    assert collector.sessions == {}  # a quota error alone does not keep stale sessions
    assert collector.quotas == {"codex": quota}
    assert collector.errors == {"codex": "RuntimeError"}


def test_process_pid_reuse_invalidates_cached_transcript(tmp_path):
    from bitacora.model import ProcInfo, Session

    class ProviderWithReusablePid(Provider):
        name = "codex"
        label = "Codex"
        marker = "codex"
        session_cls = Session

        def __init__(self, paths):
            self.paths = paths
            self.procs_now = []
            self.matches = 0

        def home(self):
            return tmp_path

        def available(self):
            return True

        def procs(self, _snap):
            return self.procs_now

        def match(self, pi, _claimed, _peers=()):
            self.matches += 1
            return self.paths[pi.create]

        def history(self, _since):
            return []

    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    first_path.touch()
    second_path.touch()
    provider = ProviderWithReusablePid({100.0: first_path, 200.0: second_path})
    memory = Memory(tmp_path / "sessions.json")
    collector = Collector([provider], memory, hours=24, scan_procs=False)
    provider.procs_now = [ProcInfo("codex", 77, 100.0, ["codex"], log="first.log")]
    collector.collect()
    assert collector.pid_map == {("codex", 77, 100.0): first_path}

    provider.procs_now = [ProcInfo("codex", 77, 200.0, ["codex"], log="second.log")]
    collector.collect()
    assert provider.matches == 2
    assert collector.pid_map == {("codex", 77, 200.0): second_path}
