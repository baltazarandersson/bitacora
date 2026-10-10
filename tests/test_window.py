from types import SimpleNamespace

from bitacora import window


class FakeWindow:
    width, height = 900, 600

    def __init__(self, url="about:blank"):
        self.url = url
        self.title = None
        self.destroyed = False
        self.fullscreen = False

    def get_current_url(self):
        return self.url

    def set_title(self, value):
        self.title = value

    def destroy(self):
        self.destroyed = True

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen


def test_bridge_rejects_untrusted_navigation_and_private_method_names():
    calls = []
    service = SimpleNamespace(bootstrap=lambda: calls.append("called") or {})
    api = window.WindowBridge(service)
    api._window = FakeWindow("https://untrusted.example.test")
    assert api.bootstrap()["ok"] is False
    assert not api.quit()["ok"] and not api.toggle_fullscreen()["ok"]
    assert not calls and not api._window.destroyed
    api._window.url = "about:blank"
    assert api._call("_private_method")["ok"] is False
    assert not calls


def test_bridge_title_quit_and_fullscreen():
    calls = []

    def get_snapshot(sort_key=None, sort_direction=None):
        calls.append((sort_key, sort_direction))
        return {"counts": {"running": 3}}

    service = SimpleNamespace(get_snapshot=get_snapshot)
    api = window.WindowBridge(service)
    api._window = FakeWindow()
    api.get_snapshot("last", "desc")
    assert calls == [("last", "desc")]
    assert api.get_snapshot("private", "asc")["ok"] is False
    assert calls == [("last", "desc")]
    assert api._window.title == "Bitácora · 3 running"
    assert api.toggle_fullscreen()["ok"] and api._window.fullscreen
    assert api.quit()["ok"] and api._window.destroyed


def test_page_embeds_all_assets_and_no_external_server():
    page = window.page_html()
    assert 'href="app.css"' not in page and 'src="app.js"' not in page
    assert "connect-src 'none'" in page
    assert "textual-serve" not in page and "textual.js" not in page
    assert "pywebview.api" in page and 'id="scene"' in page
    assert "cdn" not in page.lower()
    assert 'data-sort="name"' in page and 'aria-sort="none"' in page
    assert 'id="btn-sort-reset"' not in page and 'id="sort-toolbar"' not in page
    assert "function cycleSort" in page


def test_smoke_uses_current_sort_reset_and_layout_contract():
    import inspect

    smoke_source = inspect.getsource(window.smoke)
    assert "sort-toolbar" not in smoke_source and "btn-sort-reset" not in smoke_source
    assert "KeyboardEvent" in smoke_source and "ctrlKey:true" in smoke_source
    assert "pageOverflow" in smoke_source and "visibleRows" in smoke_source


def test_smoke_allows_intentional_list_scroll_but_rejects_page_overflow():
    dom = {
        "pageOverflow": False,
        "listOverflow": True,
        "tableOverflow": True,
        "visibleRows": 4,
        "headerFit": True,
        "statusFit": True,
        "stopInWindow": True,
    }
    assert window._smoke_layout_problem(dom) is None
    assert window._smoke_layout_problem({**dom, "pageOverflow": True}) == "page overflow"


def test_smoke_rejects_missing_list_rows_or_off_window_stop():
    dom = {
        "pageOverflow": False,
        "visibleRows": 0,
        "headerFit": True,
        "statusFit": True,
        "stopInWindow": True,
    }
    assert window._smoke_layout_problem(dom) == "no visible list rows"
    dom["visibleRows"] = 3
    dom["stopInWindow"] = False
    assert window._smoke_layout_problem(dom, require_stop=True) == "Stop action is outside the window"


def test_hidden_smoke_load_failure_never_shows_window():
    class LoadedEvent:
        def __init__(self):
            self.timeouts = []

        def wait(self, timeout):
            self.timeouts.append(timeout)
            return False

    class SmokeWindow:
        def __init__(self):
            self.events = SimpleNamespace(loaded=LoadedEvent())
            self.show_calls = 0
            self.destroyed = False

        def show(self):
            self.show_calls += 1

        def destroy(self):
            self.destroyed = True

    smoke_window, report = SmokeWindow(), {}
    window.smoke(smoke_window, service=None, report=report)
    assert smoke_window.events.loaded.timeouts == [20]
    assert smoke_window.show_calls == 0 and smoke_window.destroyed
    assert report["ok"] is False and report["stage"] == "window load"
    assert report["error"] == "TimeoutError"


def test_smoke_native_host_is_renderable_without_visible_pixels_or_taskbar():
    initialized = []
    form = SimpleNamespace(Opacity=1, ShowInTaskbar=True)
    model = SimpleNamespace(hidden=True)
    initialize = window._invisible_smoke_initializer(
        lambda f, w: initialized.append((f, w, f.ShowInTaskbar)),
        lambda f: setattr(f, "Location", (-1100, -740)),
    )
    initialize(form, model)
    assert initialized == [(form, model, False)]
    assert form.Location == (-1100, -740) and not form.ShowInTaskbar
    assert model.hidden is False


def test_divider_beam_moves_by_transform_once_and_the_topbar_beam_is_gone():
    from importlib.resources import files

    css = files("bitacora").joinpath("desktop_assets", "app.css").read_text(encoding="utf-8")
    script = files("bitacora").joinpath("desktop_assets", "app.js").read_text(encoding="utf-8")
    assert "topbar::after" not in css and css.count("@keyframes beam") == 1
    beam = next(line for line in css.splitlines() if line.startswith("@keyframes beam"))
    assert "transform" in beam and "left" not in beam
    assert 'id="rule"' in window.page_html()
    assert "prefers-reduced-motion: reduce" in css
    assert "inkOn" not in script and "function ink(" in script and 'DEFAULT_THEME = "monokai"' in script


def test_desktop_contract_names_the_default_theme_and_the_saving_rule():
    from pathlib import Path

    contract = (Path(__file__).resolve().parents[1] / "docs" / "desktop-contract.md").read_text(encoding="utf-8")
    assert "monokai" in contract and "changed" in contract


def test_desktop_headings_follow_shared_column_order():
    import re

    from bitacora.sorting import RUN_COLUMNS

    page = window.page_html()
    header = re.search(r"<thead[^>]*>(.*?)</thead>", page, re.DOTALL).group(1)
    assert re.findall(r'data-sort="([a-z]+)"', header) == list(RUN_COLUMNS)


def test_placement_roundtrip_and_invalid_size(monkeypatch, tmp_path):
    from bitacora.store import Placement

    monkeypatch.setenv("BITACORA_HOME", str(tmp_path))
    assert window.MIN_SIZE == (800, 560)
    assert window.saved_size() == (window.WIDTH, window.HEIGHT)
    window.save_size(FakeWindow())
    assert window.saved_size() == (900, 600)
    state = Placement()
    state.set("width", 800)
    state.set("height", 560)
    assert state.save()
    assert window.saved_size() == (800, 560)
    state.set("width", 799)
    state.set("height", 560)
    assert state.save()
    assert window.saved_size() == (window.WIDTH, window.HEIGHT)
    state.set("width", 800)
    state.set("height", 559)
    assert state.save()
    assert window.saved_size() == (window.WIDTH, window.HEIGHT)


def test_theme_colors_keep_existing_palette():
    from bitacora.themes import DEFAULT_THEME

    assert window.theme_colors("nord")[0].startswith("#")
    assert window.theme_colors("unknown") == window.theme_colors(None) == window.theme_colors(DEFAULT_THEME)
    assert window.theme_colors(DEFAULT_THEME)[0] == "#272822"  # the default is monokai


def test_cached_icon_is_refreshed_from_packaged_lighthouse(monkeypatch, tmp_path):
    import base64
    from pathlib import Path

    monkeypatch.setattr(window.tempfile, "gettempdir", lambda: str(tmp_path))
    (tmp_path / "bitacora-icon.ico").write_bytes(b"outdated synthetic icon")
    path = window.icon_file()
    assert Path(path).read_bytes() == base64.b64decode(window.ICON_B64)


def test_cli_information_flags_do_not_need_gui(monkeypatch, capsys):
    import builtins

    import pytest

    from bitacora.cli import main

    original = builtins.__import__

    def without_gui(name, *args, **kwargs):
        if name == "webview" or name.startswith("webview."):
            raise ImportError("synthetic missing optional dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_gui)
    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0 and "1.1.0" in capsys.readouterr().out


def test_profile_cleanup_retries_transient_webview_lock(monkeypatch):
    original = window.shutil.rmtree
    calls = []

    def transient(path):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("synthetic WebView2 lock")
        return original(path)

    monkeypatch.setattr(window.shutil, "rmtree", transient)
    monkeypatch.setattr(window.time, "sleep", lambda value: None)
    from pathlib import Path

    with window.temporary_profile() as profile:
        path = Path(profile)
        (path / "EBWebView").mkdir()
        (path / "EBWebView" / "synthetic.lock").write_text("synthetic", encoding="utf-8")
        assert path.exists()
    assert not path.exists() and len(calls) == 2


def test_renderer_cleanup_only_selects_exact_profile(monkeypatch, tmp_path):
    import os

    import pytest

    if os.name != "nt":
        pytest.skip("WebView2 ownership is Windows-specific")
    import psutil

    profile = str(tmp_path / "unique-profile")

    class Process:
        def __init__(self, pid, name, folder=None, created=100.0, children=(), killed=None):
            self.pid = pid
            self.info = {"name": name}
            self._folder = folder
            self._created = created
            self._children = list(children)
            self._killed = killed

        def cmdline(self):
            args = [self.info["name"]]
            if self._folder:
                args.append("--user-data-dir=" + self._folder)
            return args

        def create_time(self):
            return self._created

        def children(self):
            return self._children

        def kill(self):
            if self._killed is not None:
                self._killed.append(self.pid)

    own = profile + "\\EBWebView"
    network = Process(2, "msedgewebview2.exe", created=101.0)
    other_profile = Process(3, "msedgewebview2.exe", own + "-other", created=102.0)
    root = Process(1, "msedgewebview2.exe", own, children=[network, other_profile])
    processes = [root, network, other_profile]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: processes)
    owned = window.owned_renderers(profile)
    assert owned == [(1, 100.0), (2, 101.0)]

    killed = []
    handles = {pid: Process(pid, "msedgewebview2.exe", created=created, killed=killed)
               for pid, created in owned}
    monkeypatch.setattr(psutil, "Process", lambda pid: handles[pid])
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: (procs, []))
    result = window.stop_owned_renderers(owned)
    assert killed == [2, 1] and result == {"stopped": 2, "failures": [], "reused": []}

    reused = Process(1, "msedgewebview2.exe", created=999.0, killed=killed)
    monkeypatch.setattr(psutil, "Process", lambda pid: reused)
    result = window.stop_owned_renderers([(1, 100.0)])
    assert killed == [2, 1] and result["reused"] == [1] and not result["failures"]


def make_cleanup_profile(monkeypatch, tmp_path, *, report_path=None, functional_ok=True):
    import os
    import secrets

    monkeypatch.setattr(window.tempfile, "gettempdir", lambda: str(tmp_path))
    profile = tmp_path / f"{window.PROFILE_PREFIX}test"
    profile.mkdir()
    owner = {
        "version": 1,
        "nonce": secrets.token_urlsafe(24),
        "profile": str(profile.resolve()),
        "temp_root": str(tmp_path.resolve()),
        "parent_pid": os.getpid(),
        "parent_create": window._process_create_time(os.getpid()),
        "report_path": window._resolved_report_path(str(report_path)) if report_path else None,
        "functional_ok": functional_ok if report_path else None,
    }
    window._write_owner(profile, owner)
    return profile, owner


def test_cleanup_profile_cli_waits_for_parent_and_finishes_pending_report(monkeypatch, tmp_path):
    import json

    report_path = tmp_path / "smoke-report.json"
    profile, owner = make_cleanup_profile(monkeypatch, tmp_path, report_path=report_path)
    (profile / "EBWebView").mkdir()
    report_path.write_text(json.dumps({
        "ok": False,
        "functional_ok": True,
        "cleanup": "pending",
        "cleanup_nonce": owner["nonce"],
        "rows": 8,
    }), encoding="utf-8")

    parent_checks = []
    parent_running = [True]

    def parent_alive(pid, created):
        parent_checks.append((pid, created))
        was_running = parent_running[0]
        parent_running[0] = False
        return was_running

    original_rmtree = window.shutil.rmtree
    removed_after_parent = []

    def checked_rmtree(path):
        assert not parent_running[0]
        removed_after_parent.append(path)
        original_rmtree(path)

    monkeypatch.setattr(window, "_parent_alive", parent_alive)
    monkeypatch.setattr(window.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(window.shutil, "rmtree", checked_rmtree)
    monkeypatch.setattr(window, "_launch_cleanup_helper",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("helper must not recurse")))

    argv = [str(profile), owner["nonce"], str(owner["parent_pid"]), repr(owner["parent_create"]),
            str(report_path)]
    assert window._cleanup_profile_cli(argv) == 0
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert removed_after_parent == [profile / "EBWebView"]
    assert len(parent_checks) >= 2 and not profile.exists()
    assert saved["cleanup"] == "complete" and saved["functional_ok"] and saved["ok"]
    assert saved["rows"] == 8 and "cleanup_nonce" not in saved


def test_cleanup_profile_cli_retries_locks_within_bounded_grace(monkeypatch, tmp_path):
    import json

    def run_case(folder, *, clears_after):
        folder.mkdir()
        report_path = folder / "smoke-report.json"
        with monkeypatch.context() as patch:
            profile, owner = make_cleanup_profile(patch, folder, report_path=report_path)
            webview_profile = profile / "EBWebView"
            webview_profile.mkdir()
            (webview_profile / "synthetic.lock").write_text("synthetic", encoding="utf-8")
            report_path.write_text(json.dumps({
                "ok": False,
                "functional_ok": True,
                "cleanup": "pending",
                "cleanup_nonce": owner["nonce"],
            }), encoding="utf-8")

            clock = {"now": 0.0}
            attempts = []
            patch.setattr(window.time, "monotonic", lambda: clock["now"])
            patch.setattr(window.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
            patch.setattr(window, "_parent_alive", lambda *_args: False)
            remove_profile = window._remove_profile_once

            def remove_once(profile_path, recorded_owner):
                attempts.append(clock["now"])
                if clears_after is None or clock["now"] < clears_after:
                    raise PermissionError(5, "synthetic WebView2 lock",
                                          str(webview_profile / "synthetic.lock"))
                return remove_profile(profile_path, recorded_owner)

            patch.setattr(window, "_remove_profile_once", remove_once)
            argv = [str(profile), owner["nonce"], str(owner["parent_pid"]),
                    repr(owner["parent_create"]), str(report_path)]
            exit_code = window._cleanup_profile_cli(argv)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            return exit_code, clock["now"], attempts, saved, profile

    recovered = run_case(tmp_path / "recovered", clears_after=20.0)
    assert recovered[0] == 0 and 15 < recovered[1] < window.PROFILE_REMOVE_WAIT
    assert recovered[3]["cleanup"] == "complete" and recovered[3]["ok"]
    assert not recovered[4].exists()

    persistent = run_case(tmp_path / "persistent", clears_after=None)
    assert persistent[0] == 1 and persistent[1] == window.PROFILE_REMOVE_WAIT
    assert persistent[3]["cleanup"] == "failed" and persistent[3]["cleanup_stage"] == "profile cleanup"
    assert persistent[3]["cleanup_error"] == "PermissionError" and persistent[3]["ok"] is False
    assert persistent[3]["cleanup_detail"]["filename"] == str(
        (persistent[4] / "EBWebView" / "synthetic.lock").relative_to(persistent[4]))
    assert persistent[3]["cleanup_detail"]["errno"] == 5


def test_cleanup_profile_rejects_outside_path_bad_nonce_and_marker_reparse(monkeypatch, tmp_path):
    import pytest

    profile, owner = make_cleanup_profile(monkeypatch, tmp_path)
    marker = profile / window.PROFILE_OWNER
    with pytest.raises(ValueError):
        window._read_owner(profile, "wrong-nonce", owner["parent_pid"], owner["parent_create"], None)
    outside = tmp_path.parent / f"{window.PROFILE_PREFIX}outside"
    outside.mkdir()
    with pytest.raises(ValueError):
        window._profile_target(outside)

    original_reparse = window._is_reparse
    monkeypatch.setattr(window, "_is_reparse", lambda path: path == marker or original_reparse(path))
    with pytest.raises(ValueError, match="marker"):
        window._read_owner(profile, owner["nonce"], owner["parent_pid"], owner["parent_create"], None)
    assert marker.exists() and profile.exists()


def test_parent_pid_reuse_is_treated_as_the_original_parent_exit(monkeypatch):
    import psutil

    class ReusedProcess:
        def create_time(self):
            return 200.0

    monkeypatch.setattr(psutil, "Process", lambda _pid: ReusedProcess())
    assert not window._parent_alive(1234, 100.0)


def test_cleanup_profile_rejects_nonfinite_parent_creation_time(monkeypatch, tmp_path):
    profile, owner = make_cleanup_profile(monkeypatch, tmp_path)
    for value in ("nan", "inf", "-inf"):
        argv = [str(profile), owner["nonce"], str(owner["parent_pid"]), value]
        assert window._cleanup_profile_cli(argv) == 2
        assert profile.exists()


def test_parent_alive_treats_nonfinite_creation_times_as_ambiguous(monkeypatch):
    import math

    import psutil

    class Process:
        def __init__(self, created):
            self.created = created

        def create_time(self):
            return self.created

    process = Process(100.0)
    monkeypatch.setattr(psutil, "Process", lambda _pid: process)
    assert all(window._parent_alive(1234, value) for value in (math.nan, math.inf, -math.inf))
    process.created = math.nan
    assert window._parent_alive(1234, 100.0)


def test_cleanup_helper_uses_private_source_or_frozen_entrypoint(monkeypatch, tmp_path):
    import sys

    profile = tmp_path / f"{window.PROFILE_PREFIX}cmd"
    owner = {"nonce": "synthetic-nonce", "parent_pid": 123, "parent_create": 456.0,
             "report_path": str(tmp_path / "report.json")}
    monkeypatch.setattr(sys, "executable", "python-test.exe")
    monkeypatch.delattr(sys, "frozen", raising=False)
    source = window._cleanup_helper_argv(profile, owner)
    assert source[:4] == ["python-test.exe", "-m", "bitacora.window", "--cleanup-profile"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    frozen = window._cleanup_helper_argv(profile, owner)
    assert frozen[:2] == ["python-test.exe", "--cleanup-profile"]
    assert frozen[-1] == owner["report_path"] and "--window" not in frozen


def test_frozen_cleanup_helper_resets_pyinstaller_environment(monkeypatch, tmp_path):
    import os
    import sys

    profile = tmp_path / f"{window.PROFILE_PREFIX}frozen"
    owner = {"nonce": "synthetic-nonce", "parent_pid": 123, "parent_create": 456.0,
             "report_path": None}
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "0")
    monkeypatch.setenv("BITACORA_TEST_SENTINEL", "preserved")
    captured = {}

    def capture(argv, **options):
        captured["argv"] = argv
        captured["options"] = options

    monkeypatch.setattr(window.subprocess, "Popen", capture)
    window._launch_cleanup_helper(profile, owner)
    assert captured["argv"] == window._cleanup_helper_argv(profile, owner)
    assert captured["options"]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert captured["options"]["env"]["BITACORA_TEST_SENTINEL"] == "preserved"
    assert captured["options"]["shell"] is False
    assert os.environ["PYINSTALLER_RESET_ENVIRONMENT"] == "0"


def test_private_cleanup_dispatches_before_loading_window_runtime(monkeypatch):
    monkeypatch.setattr(window, "_cleanup_profile_cli", lambda argv: 7 if argv == ["synthetic"] else 2)
    assert window.open_window(["--cleanup-profile", "synthetic"]) == 7


def test_profile_removal_waits_for_owned_webview_processes(monkeypatch, tmp_path):
    import pytest

    profile, owner = make_cleanup_profile(monkeypatch, tmp_path)
    (profile / "EBWebView").mkdir()
    (profile / "EBWebView" / "Cookies").write_text("synthetic", encoding="utf-8")
    alive, stopped = [(101, 1.0), (102, 2.0)], []
    monkeypatch.setattr(window, "owned_renderers", lambda _profile: list(alive))
    monkeypatch.setattr(window, "stop_owned_renderers", lambda owned: stopped.extend(pid for pid, _ in owned))

    with pytest.raises(window.ProfileBusy) as busy:
        window._remove_profile_once(profile, owner)
    assert busy.value.count == 2 and stopped == [101, 102]
    assert (profile / "EBWebView" / "Cookies").exists() and (profile / window.PROFILE_OWNER).exists()

    alive.clear()
    window._remove_profile_once(profile, owner)
    assert not profile.exists()


def test_cleanup_helper_waits_for_webview_then_reports_busy_profile(monkeypatch, tmp_path):
    import json

    def run_case(folder, *, exits_after):
        folder.mkdir()
        report_path = folder / "smoke-report.json"
        with monkeypatch.context() as patch:
            profile, owner = make_cleanup_profile(patch, folder, report_path=report_path)
            (profile / "EBWebView").mkdir()
            report_path.write_text(json.dumps({
                "ok": False, "functional_ok": True, "cleanup": "pending", "cleanup_nonce": owner["nonce"],
            }), encoding="utf-8")
            clock = {"now": 0.0}
            patch.setattr(window.time, "monotonic", lambda: clock["now"])
            patch.setattr(window.time, "sleep", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
            patch.setattr(window, "_parent_alive", lambda *_args: False)
            patch.setattr(window, "owned_renderers", lambda _profile: [(7, 1.0)] if clock["now"] < exits_after else [])
            patch.setattr(window, "stop_owned_renderers", lambda _owned: None)
            argv = [str(profile), owner["nonce"], str(owner["parent_pid"]), repr(owner["parent_create"]),
                    str(report_path)]
            exit_code = window._cleanup_profile_cli(argv)
            return exit_code, clock["now"], json.loads(report_path.read_text(encoding="utf-8")), profile

    recovered = run_case(tmp_path / "recovered", exits_after=20.0)
    assert recovered[0] == 0 and 15 < recovered[1] < window.PROFILE_REMOVE_WAIT
    assert recovered[2]["cleanup"] == "complete" and recovered[2]["ok"] and not recovered[3].exists()

    busy = run_case(tmp_path / "busy", exits_after=float("inf"))
    assert busy[0] == 1 and busy[1] == window.PROFILE_REMOVE_WAIT
    assert busy[2]["cleanup"] == "failed" and busy[2]["cleanup_error"] == "ProfileBusy" and busy[2]["ok"] is False
    assert busy[2]["cleanup_detail"] == {"webview_processes": 1}
    assert (busy[3] / "EBWebView").exists() and (busy[3] / window.PROFILE_OWNER).exists()
