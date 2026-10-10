import json
import time
from types import SimpleNamespace

import psutil
import pytest

from bitacora.collector import Collector
from bitacora.desktop import DesktopService
from bitacora.model import ProcInfo
from bitacora.providers import all_providers
from bitacora.runtime import Runtime
from bitacora.store import Config, Memory, State
from bitacora.worker import Source


class SyntheticSource:
    health = "ready"
    error = None

    def __init__(self, collector):
        self.collector = collector
        self.pending = True
        self.last_update = time.monotonic()
        self.mirror = SimpleNamespace(quotas={})
        self.stopped = False

    def start(self):
        pass

    def poll(self):
        if not self.pending:
            return None
        self.pending = False
        runs = self.collector.collect()
        self.mirror.quotas = self.collector.quotas
        return runs

    def set_hours(self, hours):
        self.collector.hours = hours
        self.pending = True

    def refresh(self):
        self.pending = True

    def stop(self):
        self.stopped = True


def build_service(demo_env, view="everything"):
    memory = Memory()
    for key, entry in demo_env["state"].items():
        memory.remember(key, entry)
    memory.save()
    providers = all_providers()
    source = SyntheticSource(Collector(providers, memory, 24, scan_procs=False))
    runtime = Runtime(Config(), State(), providers, "en", demo_env["codex_home"].parent)
    return DesktopService(runtime, view=view, source=source)


@pytest.fixture
def service(demo_env):
    instance = build_service(demo_env)
    yield instance
    instance.close()


def test_desktop_full_demo_data_and_serializable_contract(service):
    boot = service.bootstrap()
    assert boot["lang"] == "en" and not boot["first_run"]
    assert len(boot["themes"]) == 10 and len(boot["views"]) == 4
    snapshot = service.get_snapshot()
    assert len(snapshot["runs"]) == 16
    assert snapshot["runs"][0]["status"] == "asking"
    assert snapshot["counts"]["views"]["background"] == {"total": 9, "live": 3}
    five_hour = next(window for window in snapshot["quotas"]["codex"]["windows"] if window["id"] == "5h")
    assert five_hour["used_percent"] == 38
    auth = next(r for r in snapshot["runs"] if r["label"] == "auth-cookies")
    detail = service.get_detail(auth["key"])
    assert detail["events"] and detail["messages"] and detail["files"]
    assert detail["log"]["available"] and "pytest" in detail["log"]["text"]
    assert detail["result"]["text"] and detail["resume"]
    assert detail["log"]["path"].startswith("/demo/")
    assert not auth["actions"]["stop"]  # demo recency is not a real process identity
    json.dumps({"bootstrap": boot, "snapshot": snapshot, "detail": detail}, allow_nan=False)


def test_snapshot_counts_all_normalized_statuses_without_legacy_keys(service, monkeypatch):
    from bitacora.model import Run

    service.get_snapshot()
    status_keys = ("asking", "running", "starting", "your_turn", "done", "failed", "limited",
                   "stopped", "quiet", "unknown")
    assigned = service._runs[:len(status_keys)]
    statuses = {run.key: status for run, status in zip(assigned, status_keys, strict=True)}
    statuses.update({run.key: "unknown" for run in service._runs[len(status_keys):]})
    monkeypatch.setattr(Run, "status", property(lambda run: statuses[run.key]))
    service._previous = dict(statuses)
    service._source.pending = True

    snapshot = service.get_snapshot()
    counts = snapshot["counts"]
    assert set(counts) == {"total", "live", "views", *status_keys}
    assert {key: counts[key] for key in status_keys} == {
        **{key: 1 for key in status_keys[:-1]}, "unknown": 1 + len(service._runs) - len(status_keys)
    }
    assert counts["total"] == len(service._runs)
    assert not {"ask", "idle", "cut"} & counts.keys()


def test_status_source_is_in_summary_and_details(service, monkeypatch):
    from bitacora.i18n import t
    from bitacora.model import Run

    service.get_snapshot()
    run = next(run for run in service._runs if run.session is not None)
    original_status = Run.status
    monkeypatch.setattr(Run, "status", property(
        lambda candidate: "running" if candidate.key == run.key else original_status.fget(candidate)
    ))
    run.proc = None

    summary = service._summary(run)
    assert summary["status_source"] == "transcript"
    detail = service.get_detail(run.key)
    info = {field["label"]: field["value"] for field in detail["info"]}
    assert info[t("inf_status_source")] == t("status_source_transcript")

    run.proc = ProcInfo(run.provider, 4242, time.time(), [run.provider], proc_cwd=run.cwd)
    assert service._summary(run)["status_source"] == "process"
    detail = service.get_detail(run.key)
    info = {field["label"]: field["value"] for field in detail["info"]}
    assert info[t("inf_status_source")] == t("status_source_process")


@pytest.mark.parametrize(("before", "after", "kind"), [
    ("running", "asking", "asking"),
    ("starting", "asking", "asking"),
    ("running", "done", "done"),
    ("running", "your_turn", "done"),
    ("running", "failed", "stopped"),
    ("running", "limited", "stopped"),
    ("running", "stopped", "stopped"),
    ("asking", "done", "done"),
    ("asking", "stopped", "stopped"),
    ("starting", "done", "done"),
    ("starting", "stopped", "stopped"),
    ("running", "quiet", None),
])
def test_desktop_notifications_match_terminal_transitions(service, monkeypatch, before, after, kind):
    from bitacora.i18n import t
    from bitacora.model import Run

    service.get_snapshot()
    run = next(run for run in service._runs if run.session is not None)
    statuses = {run.key: before}
    original_status = Run.status
    monkeypatch.setattr(Run, "status", property(
        lambda candidate: statuses.get(candidate.key, original_status.fget(candidate))
    ))
    service._previous = {candidate.key: candidate.status for candidate in service._runs}
    service._notices.clear()

    def poll():
        return list(service._runs)

    service._source.poll = poll
    statuses[run.key] = after

    notices = service.get_snapshot()["notices"]
    assert [notice["kind"] for notice in notices] == ([kind] if kind else [])
    if kind:
        message = {"asking": "n_ask", "done": "n_done", "stopped": "n_stopped"}[kind]
        assert notices[0]["text"] == t(message, label=run.label)


def test_desktop_snapshot_sorting_and_invalid_sort_requests(service):
    default = service.get_snapshot()["runs"]
    ascending = service.get_snapshot("name", "asc")["runs"]
    descending = service.get_snapshot("name", "desc")["runs"]
    expected = sorted(default, key=lambda run: run["label"].casefold())
    assert [run["key"] for run in ascending] == [run["key"] for run in expected]
    assert [run["label"] for run in descending] == sorted(
        (run["label"] for run in default), key=str.casefold, reverse=True
    )
    assert [run["key"] for run in service.get_snapshot()["runs"]] == [run["key"] for run in default]

    source = service._source
    source.pending = True
    for key, direction in (("private", "asc"), ("name", "sideways"), (None, "asc")):
        response = service.get_snapshot(key, direction)
        assert "error" in response and "runs" not in response
        assert source.pending  # invalid requests do not poll or reveal a snapshot


def test_preferences_validate_before_persist_and_relabel(service):
    for patch in ({"pid": 123}, {"view": []}, {"hours": float("nan")}, {"notify": "yes"}, {"font_size": True}):
        assert "error" in service.save_preferences(patch)
    result = service.save_preferences({"lang": "es", "view": "live", "font_size": 18, "hours": 12})
    assert result["lang"] == "es" and result["preferences"]["font_size"] == 18
    assert result["strings"]["desktop_stop"] == "Detener"
    assert State().get("lang") == "es"
    assert len(service.get_snapshot()["runs"]) == 4


def test_theme_then_view_then_a_fresh_service_show_the_same_preferences(demo_env):
    service = build_service(demo_env, view=None)
    try:
        assert service.save_preferences({"theme": "nord"})["preferences"]["theme"] == "nord"
        saved = service.save_preferences({"view": "live"})["preferences"]
        assert saved["theme"] == "nord" and saved["view"] == "live"
        assert service.bootstrap()["preferences"]["theme"] == "nord"
    finally:
        service.close()
    fresh = build_service(demo_env, view=None)
    try:
        boot = fresh.bootstrap()["preferences"]
        assert boot["theme"] == "nord" and boot["view"] == "live"
    finally:
        fresh.close()


def test_view_then_theme_then_a_fresh_service_show_the_same_preferences(demo_env):
    service = build_service(demo_env, view=None)
    try:
        assert service.save_preferences({"view": "interactive"})["preferences"]["view"] == "interactive"
        saved = service.save_preferences({"theme": "gruvbox"})["preferences"]
        assert saved["theme"] == "gruvbox" and saved["view"] == "interactive"
    finally:
        service.close()
    fresh = build_service(demo_env, view=None)
    try:
        boot = fresh.bootstrap()["preferences"]
        assert boot["theme"] == "gruvbox" and boot["view"] == "interactive"
    finally:
        fresh.close()


def test_a_theme_saved_by_another_interface_is_shown_and_kept_by_a_view_save(service):
    other = State()  # the terminal app saved a theme while this window stayed open
    other.set("theme", "nord")
    assert other.save()
    saved = service.save_preferences({"view": "live"})["preferences"]
    assert saved["theme"] == "nord" and saved["view"] == "live"
    assert service.bootstrap()["preferences"]["theme"] == "nord"
    assert State().get("theme") == "nord" and State().get("view") == "live"


def test_a_view_saved_by_another_interface_is_not_reverted_by_a_theme_change(service):
    other = State()
    other.set("view", "interactive")
    assert other.save()
    saved = service.save_preferences({"theme": "gruvbox"})["preferences"]
    assert saved["view"] == "interactive" and saved["theme"] == "gruvbox"
    assert State().get("view") == "interactive" and State().get("theme") == "gruvbox"


@pytest.mark.parametrize("value", [None, True, "0.4", [], -1, 0.149, 0.851, float("nan"), float("inf")])
def test_list_height_rejects_invalid_values_without_persisting(service, value):
    before = dict(service.bootstrap()["preferences"])
    assert "error" in service.save_preferences({"list_height": value, "lang": "es"})
    assert service.bootstrap()["preferences"] == before
    assert State().get("list_height") is None


@pytest.mark.parametrize("value", [0.15, 0.4, 0.85])
def test_list_height_persists_and_reloads(service, value):
    assert service.save_preferences({"list_height": value})["preferences"]["list_height"] == value
    state = State()
    assert state.get("list_height") == value
    service._runtime.state = state
    loaded = DesktopService(service._runtime, source=service._source)
    try:
        assert loaded.bootstrap()["preferences"]["list_height"] == value
    finally:
        loaded.close()


def test_list_height_invalid_saved_value_uses_default(service):
    service._runtime.state.set("list_height", True)
    loaded = DesktopService(service._runtime, source=service._source)
    try:
        assert loaded.bootstrap()["preferences"]["list_height"] is None
    finally:
        loaded.close()


def test_unsaved_list_height_stays_automatic_when_other_settings_change(service):
    assert service.bootstrap()["preferences"]["list_height"] is None
    assert service.save_preferences({"font_size": 16})["preferences"]["list_height"] is None
    assert State().get("list_height") is None
    assert "error" in service.save_preferences({"list_height": None})


def test_hero_started_value_is_shared_local_time(service):
    from datetime import datetime

    snapshot = service.get_snapshot()
    runs = {run.key: run for run in service._runs}
    for row in snapshot["runs"]:
        start = runs[row["key"]].start
        assert row["start_ts"] == start
        assert row["started_text"] == (datetime.fromtimestamp(start).strftime("%d/%m %H:%M")
                                      if start is not None else "-")


def test_desktop_table_agent_is_short_and_detail_agent_is_full(service):
    from bitacora.sorting import RUN_COLUMNS
    from bitacora.ui.render import agent_table_text, agent_text

    snapshot = service.get_snapshot()
    runs = {run.key: run for run in service._runs}
    for row in snapshot["runs"]:
        assert row["agent"] == agent_table_text(runs[row["key"]]).plain
        assert row["agent_full"] == agent_text(runs[row["key"]]).plain
    for key in RUN_COLUMNS:
        for direction in ("asc", "desc"):
            assert "runs" in service.get_snapshot(key, direction)
    run = next(run for run in service._runs if run.label.startswith("Update CHANGELOG"))
    row = next(row for row in snapshot["runs"] if row["key"] == run.key)
    assert row["agent"] == "claude · Background"
    assert row["agent_full"] == "Claude Code · Background"
    assert row["origin_kind"] == "background" and row["origin_raw"] == "sdk-cli"
    assert row["usage_basis"] == "processed" and row["context_window"] == 1_000_000
    assert row["context_window_assumed"]
    info = {field["label"]: field["value"] for field in service.get_detail(run.key)["info"]}
    assert info["Agent"] == "Claude Code · Background"
    assert info["Origin (raw)"] == "sdk-cli"
    assert info["Effort source"] == "Unknown" and info["Access kind"] == "Other"
    assert "context window assumed from model" in info["Context used"]


def test_desktop_formatted_values_match_terminal_rounding_and_full_activity(service):
    from bitacora.model import Event
    from bitacora.ui.render import activity
    from bitacora.util import fmt_tokens

    service.get_snapshot()
    run = next(run for run in service._runs if run.label == "auth-cookies")
    run.session.tokens = 1250
    run.session.ctx_used, run.session.ctx_window = 25, 200
    command = "synthetic command with the full text preserved " * 4
    run.session.in_flight = {"synthetic": Event(time.time(), "cmd", command)}
    summary = service._summary(run)
    assert summary["tokens_text"] == fmt_tokens(1250) == "1.2k"
    assert summary["context_text"] == "12%"
    assert command.strip() in summary["activity"]
    assert summary["activity"] == activity(run.session, run, limit=None).plain


def test_a_saved_window_refreshes_the_list_and_survives_a_stale_copy(service):
    assert len(service.get_snapshot()["runs"]) == 16
    service.save_preferences({"hours": 6})
    shown = service.get_snapshot()["runs"]
    assert 0 < len(shown) < 16
    assert all(run["live"] or (run["last_ts"] or 0) >= time.time() - 6 * 3600 for run in shown)
    other = State()  # another interface saves 72 hours while this service still holds 6
    other.set("hours", 72)
    assert other.save()
    assert service.save_preferences({"hours": 6})["preferences"]["hours"] == 6
    assert State().get("hours") == 6  # the next launch reads the same window


def test_a_saved_window_narrows_the_snapshot_through_the_real_reader(demo_env):
    memory = Memory()
    for key, entry in demo_env["state"].items():
        memory.remember(key, entry)
    memory.save()
    providers = all_providers()
    runtime = Runtime(Config(hours=168), State(), providers, "en", demo_env["codex_home"].parent)
    service = DesktopService(runtime, view="everything", source=Source(168, scan_procs=False, process=False))
    try:
        deadline = time.monotonic() + 20
        while len(service.get_snapshot()["runs"]) < 16 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(service.get_snapshot()["runs"]) == 16
        service.save_preferences({"hours": 6})
        shown = service.get_snapshot()["runs"]  # answered before the reader has collected for 6 hours
        assert 0 < len(shown) < 16
        assert all(run["live"] or (run["last_ts"] or 0) >= time.time() - 6 * 3600 for run in shown)
    finally:
        service.close()


def test_actions_only_accept_current_run_keys_and_regular_documents(service, tmp_path, monkeypatch):
    assert not service.open_result(str(tmp_path / "arbitrary.txt"))["ok"]
    assert not service.open_log({"path": "arbitrary"})["ok"]
    service.get_snapshot()
    run = next(r for r in service._runs if r.label == "a11y-audit")
    executable = tmp_path / "fake.exe"
    executable.write_bytes(b"synthetic")
    run.out = str(executable)
    assert not service.open_result(run.key)["ok"]
    for extension in ("html", "svg", "py", "hta", "js", "url"):
        active = tmp_path / ("untrusted." + extension)
        active.write_bytes(b"synthetic active content")
        run.out = str(active)
        assert not service.open_result(run.key)["ok"]
    run.out = None
    assert not service.open_result(run.key)["ok"]
    assert not service.copy_resume("unknown-session")["ok"]


def test_stop_confirmation_binds_creation_time_and_is_single_use(service, monkeypatch):
    service.get_snapshot()
    run = next(r for r in service._runs if r.label == "auth-cookies")
    run.proc = ProcInfo("codex", 4242, 100.0, ["codex"], proc_cwd=run.cwd)
    import bitacora.desktop as desktop

    current = [100.0]
    monkeypatch.setattr(desktop.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: current[0]))
    calls = []

    def kill(pid, expected_create=None):
        if current[0] != expected_create:
            raise psutil.NoSuchProcess(pid)
        calls.append(pid)

    monkeypatch.setattr(desktop.procs, "kill_tree", kill)
    run.proc.session_id = "contradicts-transcript"
    assert not service.request_stop(run.key)["ok"]
    run.proc.session_id = run.session.session_id
    request = service.request_stop(run.key)
    assert request["ok"] and request["pid"] == 4242
    current[0] = 101.0  # the PID was reused while the dialog was open
    assert not service.confirm_stop(request["token"])["ok"]
    assert not calls
    current[0] = 100.0
    request = service.request_stop(run.key)
    assert service.confirm_stop(request["token"])["ok"]
    assert calls == [4242]
    assert not service.confirm_stop(request["token"])["ok"]


def test_reader_health_and_close_disable_actions(service):
    snapshot = service.get_snapshot()
    service._source.error = "synthetic error"
    service._source.health = "error"
    assert service.get_snapshot()["reader"]["state"] == "error"
    assert not service.request_stop(snapshot["runs"][0]["key"])["ok"]
    service.close()
    assert service._source.stopped
    assert "error" in service.get_detail(snapshot["runs"][0]["key"])
    assert "error" in service.save_preferences({"theme": "nord"})


def test_scene_reuses_existing_algorithm_and_bounds_dimensions(service):
    assert "error" in service.get_scene(10000)
    assert "error" in service.get_scene(True)
    scene = service.get_scene(100)
    assert scene["columns"] == 100 and len(scene["cells"]) == 16
    assert all(sum(len(run[0]) for run in row) == 100 for row in scene["cells"])
    assert any("▀" in run[0] for row in scene["cells"] for run in row)
    assert all(0 <= i < len(scene["palette"]) for row in scene["cells"] for run in row for i in run[1:3])
    assert sum(len(row) for row in scene["cells"]) < 100 * 16  # runs of equal style travel together
    json.dumps(scene)


def test_process_identity_is_checked_inside_kill_tree(monkeypatch):
    from bitacora import procs

    touched = []
    monkeypatch.setattr(procs.psutil, "Process", lambda pid: SimpleNamespace(
        create_time=lambda: 200, children=lambda recursive: touched.append(pid)))
    with pytest.raises(psutil.NoSuchProcess):
        procs.kill_tree(4242, expected_create=100)
    assert touched == []


def test_codex_no_cwd_is_not_a_time_only_match(demo_env):
    from bitacora.providers.codex import CodexProvider

    provider = CodexProvider()
    pi = ProcInfo("codex", 4242, demo_env["now"], ["codex", "exec"])
    assert provider.match(pi, set()) is None
