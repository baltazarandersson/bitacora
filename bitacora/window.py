"""Local desktop presentation. No terminal server or console executable is required."""
from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from bitacora import __version__
from bitacora.i18n import t
from bitacora.icon_data import ICON_B64
from bitacora.sorting import DESKTOP_SORT_COLUMNS
from bitacora.store import Placement
from bitacora.themes import DEFAULT_THEME, descriptors

TITLE = "Bitácora"
WIDTH, HEIGHT = 1000, 640
MIN_SIZE = (800, 560)
PROFILE_PREFIX = "bitacora-window-"
PROFILE_OWNER = ".bitacora-window-owner.json"
PROFILE_CLEANUP_WAIT = 30.0
PROFILE_REMOVE_WAIT = 45.0
METHODS = ("bootstrap", "get_snapshot", "get_detail", "save_preferences", "open_result", "open_log",
           "copy_resume", "request_stop", "confirm_stop", "get_scene", "quit", "toggle_fullscreen")


class ProfileBusy(OSError):
    """WebView2 processes still use the profile, so none of its files are deleted yet."""

    def __init__(self, count: int):
        super().__init__("WebView2 processes still use the profile")
        self.count = count


@contextmanager
def temporary_profile(*, report: dict | None = None, report_path: str | None = None):
    root = _profile_root()
    profile = Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX, dir=root)).resolve()
    nonce = secrets.token_urlsafe(32)
    owner = {
        "version": 1,
        "nonce": nonce,
        "profile": str(profile),
        "temp_root": str(root),
        "parent_pid": os.getpid(),
        "parent_create": _process_create_time(os.getpid()),
        "report_path": _resolved_report_path(report_path),
        "functional_ok": None,
    }
    _write_owner(profile, owner)
    try:
        yield str(profile)
    finally:
        functional_ok = bool(report and report.get("ok"))
        owner["functional_ok"] = functional_ok if report_path else None
        try:
            _remove_profile(profile, owner)
        except (OSError, ValueError) as error:
            if report is not None and owner["report_path"]:
                report["functional_ok"] = functional_ok
                report["cleanup"] = "pending" if isinstance(error, OSError) else "failed"
                if isinstance(error, OSError):
                    report["cleanup_nonce"] = nonce
                report["cleanup_error"] = type(error).__name__
                report["ok"] = False
            if isinstance(error, OSError):
                try:
                    _write_owner(profile, owner)
                    _launch_cleanup_helper(profile, owner)
                except (OSError, ValueError) as launch_error:
                    if report is not None and owner["report_path"]:
                        report["cleanup"] = "failed"
                        report["cleanup_error"] = type(launch_error).__name__
                        report.pop("cleanup_nonce", None)
                    else:
                        logging.getLogger(__name__).warning(
                            "Could not start deferred cleanup for temporary WebView2 profile: %s", launch_error)
            elif report is None:
                logging.getLogger(__name__).warning("Unsafe temporary WebView2 profile was left in place: %s", error)
        else:
            if report is not None and owner["report_path"]:
                report["functional_ok"] = functional_ok
                report["cleanup"] = "complete"
                report["ok"] = functional_ok


def _profile_root() -> Path:
    root = Path(tempfile.gettempdir()).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("temporary directory is not a directory")
    return root


def _process_create_time(pid: int) -> float:
    import psutil

    return psutil.Process(pid).create_time()


def _resolved_report_path(path: str | None) -> str | None:
    return str(Path(path).resolve()) if path else None


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse_flag)


def _profile_target(path: str | Path) -> tuple[Path, Path]:
    raw = Path(path)
    root = _profile_root()
    if not raw.is_absolute() or not raw.name.startswith(PROFILE_PREFIX):
        raise ValueError("profile must be a named direct child of the temporary directory")
    if raw.parent.resolve(strict=True) != root or _is_reparse(raw):
        raise ValueError("profile is outside the temporary directory or is a reparse point")
    target = raw.resolve(strict=True)
    if target.parent != root or not target.is_dir():
        raise ValueError("profile is not a direct temporary-directory child")
    return target, root


def _validate_profile_tree(profile: Path) -> None:
    pending = [profile]
    while pending:
        current = pending.pop()
        if _is_reparse(current):
            raise ValueError("profile contains a symlink or reparse point")
        if current.is_dir():
            with os.scandir(current) as entries:
                for entry in entries:
                    child = Path(entry.path)
                    if _is_reparse(child):
                        raise ValueError("profile contains a symlink or reparse point")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(child)


def _stop_profile_webview(profile: Path) -> None:
    """Stop only WebView2 processes bound to this profile's user-data-dir; keep files while any survive."""
    owned = owned_renderers(profile)
    if owned:
        stop_owned_renderers(owned)
        owned = owned_renderers(profile)
    if owned:
        raise ProfileBusy(len(owned))


def _remove_profile_once(profile: str | Path, owner: dict) -> None:
    target, owner = _read_owner(profile, owner["nonce"], owner["parent_pid"], owner["parent_create"],
                                owner.get("report_path"))
    _stop_profile_webview(target)
    _validate_profile_tree(target)
    marker = _owner_path(target)
    children = [child.name for child in target.iterdir()]
    for name in children:
        # Recheck the owned root and tree immediately before every destructive operation.
        target, owner = _read_owner(profile, owner["nonce"], owner["parent_pid"], owner["parent_create"],
                                    owner.get("report_path"))
        _validate_profile_tree(target)
        marker = _owner_path(target)
        child = target / name
        if child == marker:
            continue
        if _is_reparse(child):
            raise ValueError("profile contains a symlink or reparse point")
        if not child.exists():
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    target, owner = _read_owner(profile, owner["nonce"], owner["parent_pid"], owner["parent_create"],
                                owner.get("report_path"))
    marker = _owner_path(target)
    marker.unlink()
    try:
        target.rmdir()
    except OSError:
        _write_owner(target, owner)
        raise


def _remove_profile(profile: str | Path, owner: dict, timeout: float = 8.0) -> None:
    if _profile_missing(profile):
        return
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            _remove_profile_once(profile, owner)
            return
        except OSError as error:
            last_error = error
            time.sleep(0.1)
    if last_error:
        raise last_error


def _profile_missing(profile: str | Path) -> bool:
    try:
        raw = Path(profile)
        return (raw.is_absolute() and raw.name.startswith(PROFILE_PREFIX) and not raw.exists()
                and not _is_reparse(raw) and raw.parent.resolve(strict=True) == _profile_root())
    except OSError:
        return False


def _owner_path(profile: Path) -> Path:
    return profile / PROFILE_OWNER


def _write_owner(profile: str | Path, owner: dict) -> None:
    target, root = _profile_target(profile)
    if owner.get("profile") != str(target) or owner.get("temp_root") != str(root):
        raise ValueError("profile owner identity does not match its directory")
    marker = _owner_path(target)
    if _is_reparse(marker):
        raise ValueError("profile owner marker is a reparse point")
    marker.write_bytes(json.dumps(owner, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _read_owner(profile: str | Path, nonce: str, parent_pid: int, parent_create: float,
                report_path: str | None) -> tuple[Path, dict]:
    target, root = _profile_target(profile)
    marker = _owner_path(target)
    if _is_reparse(marker) or not marker.is_file():
        raise ValueError("profile owner marker is missing or unsafe")
    data = json.loads(marker.read_text(encoding="utf-8"))
    owner_pid = data.get("parent_pid") if isinstance(data, dict) else None
    owner_create = data.get("parent_create") if isinstance(data, dict) else None
    if (not isinstance(parent_pid, int) or isinstance(parent_pid, bool) or parent_pid <= 0 or
            not _finite_positive(parent_create) or
            not isinstance(data, dict) or data.get("version") != 1 or
            not isinstance(nonce, str) or not nonce or data.get("nonce") != nonce or
            data.get("profile") != str(target) or data.get("temp_root") != str(root) or
            not isinstance(owner_pid, int) or isinstance(owner_pid, bool) or owner_pid <= 0 or
            owner_pid != parent_pid or not _finite_positive(owner_create) or
            abs(owner_create - parent_create) > 0.01 or
            (data.get("report_path") is not None and not isinstance(data.get("report_path"), str)) or
            data.get("report_path") != report_path):
        raise ValueError("profile owner marker does not match the cleanup request")
    return target, data


def _finite_positive(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value > 0
    except (OverflowError, TypeError):
        return False


def _cleanup_helper_argv(profile: Path, owner: dict) -> list[str]:
    command = [sys.executable]
    if getattr(sys, "frozen", False):
        command.append("--cleanup-profile")
    else:
        command.extend(("-m", "bitacora.window", "--cleanup-profile"))
    command.extend((str(profile), owner["nonce"], str(owner["parent_pid"]),
                    repr(owner["parent_create"])))
    if owner.get("report_path"):
        command.append(owner["report_path"])
    return command


def _launch_cleanup_helper(profile: Path, owner: dict) -> None:
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
               "stderr": subprocess.DEVNULL, "close_fds": True, "shell": False}
    if getattr(sys, "frozen", False):
        # This helper outlives the one-file application and needs its own extraction directory.
        options["env"] = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(_cleanup_helper_argv(profile, owner), **options)


def _parent_alive(pid: int, expected_create: float) -> bool:
    import psutil

    if not _finite_positive(expected_create):
        return True
    try:
        parent = psutil.Process(pid)
        actual_create = parent.create_time()
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True
    if not _finite_positive(actual_create):
        return True
    return abs(actual_create - expected_create) <= 0.01


def _write_report_atomically(path: str, value: dict) -> None:
    destination = Path(path)
    if _is_reparse(destination):
        raise ValueError("smoke report is a reparse point")
    temp = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        temp.write_bytes(json.dumps(value, ensure_ascii=True, indent=2).encode("utf-8"))
        os.replace(temp, destination)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _finish_smoke_report(owner: dict, *, complete: bool, error: str | None = None,
                        stage: str | None = None, detail: dict | None = None) -> None:
    report_path = owner.get("report_path")
    if not report_path:
        return
    report_file = Path(report_path)
    if (os.path.normcase(str(report_file.resolve())) != os.path.normcase(report_path) or
            _is_reparse(report_file) or not report_file.is_file()):
        raise ValueError("smoke report is missing or unsafe")
    report = json.loads(report_file.read_text(encoding="utf-8"))
    if (not isinstance(report, dict) or report.get("cleanup") != "pending" or
            report.get("cleanup_nonce") != owner["nonce"] or
            report.get("functional_ok") is not owner.get("functional_ok")):
        raise ValueError("smoke report does not match the pending profile cleanup")
    report["cleanup"] = "complete" if complete else "failed"
    report["ok"] = bool(owner.get("functional_ok")) if complete else False
    report.pop("cleanup_nonce", None)
    if complete:
        report.pop("cleanup_error", None)
        report.pop("cleanup_stage", None)
        report.pop("cleanup_detail", None)
    else:
        report["cleanup_error"] = error or "temporary profile cleanup failed"
        report["cleanup_stage"] = stage or "profile cleanup"
        if detail:
            report["cleanup_detail"] = detail
        else:
            report.pop("cleanup_detail", None)
    _write_report_atomically(report_path, report)


def _cleanup_error_detail(error: OSError, profile: str | Path) -> dict | None:
    if isinstance(error, ProfileBusy):
        return {"webview_processes": error.count}
    detail = {}
    for name in ("errno", "winerror"):
        value = getattr(error, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            detail[name] = value
    filename = getattr(error, "filename", None)
    if filename:
        filename = os.fsdecode(filename)
        try:
            relative = os.path.relpath(filename, profile)
        except (OSError, ValueError):
            relative = os.path.basename(filename)
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            relative = os.path.basename(filename)
        detail["filename"] = relative
    return detail or None


def _cleanup_profile_cli(argv: list[str]) -> int:
    if len(argv) not in (4, 5):
        return 2
    profile, nonce = argv[0], argv[1]
    try:
        parent_pid = int(argv[2])
        parent_create = float(argv[3])
        report_path = _resolved_report_path(argv[4]) if len(argv) == 5 else None
        target, owner = _read_owner(profile, nonce, parent_pid, parent_create, report_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 2

    deadline = time.monotonic() + PROFILE_CLEANUP_WAIT
    while _parent_alive(parent_pid, parent_create) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _parent_alive(parent_pid, parent_create):
        try:
            _finish_smoke_report(owner, complete=False, error="parent process did not exit",
                                 stage="parent exit timeout")
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        return 1
    if _profile_missing(profile):
        _finish_smoke_report(owner, complete=True)
        return 0

    deadline = time.monotonic() + PROFILE_REMOVE_WAIT
    last_error: OSError | ValueError | None = None
    while time.monotonic() < deadline:
        target = None
        try:
            target, owner = _read_owner(profile, nonce, parent_pid, parent_create, report_path)
            _remove_profile_once(target, owner)
            if target.exists():
                raise OSError("profile still exists after removal")
            _finish_smoke_report(owner, complete=True)
            return 0
        except (OSError, ValueError, json.JSONDecodeError) as error:
            last_error = error
            if _profile_missing(profile):
                _finish_smoke_report(owner, complete=True)
                return 0
            if isinstance(error, OSError) and target is not None and not target.exists():
                try:
                    _finish_smoke_report(owner, complete=True)
                    return 0
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    return 1
            time.sleep(0.25)
    try:
        _finish_smoke_report(owner, complete=False, error=type(last_error).__name__ if last_error else None,
                             stage="profile cleanup",
                             detail=_cleanup_error_detail(last_error, profile)
                             if isinstance(last_error, OSError) else None)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return 1


def owned_renderers(profile):
    """Record WebView2 processes and descendants tied to this launch's unique profile."""
    if os.name != "nt":
        return []
    import psutil

    expected = os.path.normcase(os.path.normpath(str(Path(profile) / "EBWebView")))
    roots = []
    # The WebView2 broker may parent its browser outside the Python host. The launch's unique user-data-dir
    # identifies ownership more reliably than the process tree; unrelated WebViews are never selected.
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info.get("name") or "").lower() != "msedgewebview2.exe":
                continue
            profile_arg = next((a.split("=", 1)[1] for a in process.cmdline()
                                if a.startswith("--user-data-dir=")), None)
            if profile_arg and os.path.normcase(os.path.normpath(profile_arg)) == expected:
                roots.append(process)
        except psutil.Error:
            continue

    owned = []
    seen = set()
    for root in roots:
        pending = [root]
        while pending:
            process = pending.pop(0)
            try:
                pid, created = process.pid, process.create_time()
                args = process.cmdline()
                nested_profile = next((a.split("=", 1)[1] for a in args
                                       if a.startswith("--user-data-dir=")), None)
                if nested_profile and os.path.normcase(os.path.normpath(nested_profile)) != expected:
                    continue
                if pid not in seen:
                    owned.append((pid, created))
                    seen.add(pid)
                pending.extend(process.children())
            except psutil.Error:
                continue
    return owned


def stop_owned_renderers(owned):
    """Stop only recorded process identities; never recurse into new or unverified children."""
    if os.name != "nt" or not owned:
        return {"stopped": 0, "failures": [], "reused": []}
    import psutil

    pending = []
    failures = []
    reused = []
    for pid, expected_create in reversed(owned):
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - expected_create) > 0.01:
                reused.append(pid)
                continue
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as error:
            failures.append({"pid": pid, "error": type(error).__name__})
            continue
        try:
            process.kill()
            pending.append(process)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as error:
            failures.append({"pid": pid, "error": type(error).__name__})

    _, alive = psutil.wait_procs(pending, timeout=3)
    for process in alive:
        failures.append({"pid": process.pid, "error": "TimeoutExpired"})
    return {"stopped": len(pending) - len(alive), "failures": failures, "reused": reused}


def icon_file() -> str | None:
    path = Path(tempfile.gettempdir()) / "bitacora-icon.ico"
    try:
        data = base64.b64decode(ICON_B64)
        if not path.exists() or path.read_bytes() != data:
            path.write_bytes(data)
        return str(path)
    except OSError:
        return None


def theme_colors(name: str | None) -> tuple[str, str, str]:
    themes = {x["id"]: x["colors"] for x in descriptors()}
    theme = themes.get(name) or themes[DEFAULT_THEME]
    return theme["background"], theme["foreground"], theme["accent"]


def page_html() -> str:
    from importlib.resources import files

    assets = files("bitacora").joinpath("desktop_assets")
    page = assets.joinpath("index.html").read_text(encoding="utf-8")
    css = assets.joinpath("app.css").read_text(encoding="utf-8")
    script = assets.joinpath("app.js").read_text(encoding="utf-8")
    page = page.replace('<link rel="stylesheet" href="app.css">', "<style>" + css + "</style>")
    page = page.replace('<script src="app.js"></script>', "<script>" + script + "</script>")
    policy = ("default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; "
              "img-src data:; font-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; "
              "form-action 'none'; frame-src 'none'")
    return re.sub(r'(?<=content=")default-src[^\"]+', policy, page, count=1)


def save_size(window) -> None:
    try:
        placement = Placement()
        placement.set("width", int(window.width))
        placement.set("height", int(window.height))
        placement.save()
    except (OSError, TypeError, ValueError):
        pass


def saved_size() -> tuple[int, int]:
    placement = Placement()
    try:
        width, height = int(placement.get("width")), int(placement.get("height"))
    except (TypeError, ValueError):
        return WIDTH, HEIGHT
    return (width, height) if MIN_SIZE[0] <= width <= 7680 and MIN_SIZE[1] <= height <= 4320 else (WIDTH, HEIGHT)


def tint_titlebar(window, bg: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(window.native.Handle.ToInt64())
        red, green, blue = (int(bg[i:i + 2], 16) for i in (1, 3, 5))
        color = ctypes.c_int(red | green << 8 | blue << 16)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))
        dark = ctypes.c_int(1 if 0.2126 * red + 0.7152 * green + 0.0722 * blue < 128 else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
    except Exception:
        pass


def application_identity():
    """Give repository and frozen launches the same Windows taskbar identity and lighthouse icon."""
    if os.name == "nt":
        import ctypes

        set_identity = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_identity.argtypes = [ctypes.c_wchar_p]
        set_identity.restype = ctypes.c_long
        set_identity("Bitacora.Desktop")


class WindowBridge:
    """Expose explicit methods, never a traversable Python API object."""

    def __init__(self, service):
        self._service = service
        self._window = None
        self._title = None

    def _trusted(self):
        if self._window is None:
            return False
        try:
            return self._window.get_current_url() in (None, "about:blank")
        except Exception:
            return False

    def _call(self, name, *args):
        if name not in METHODS or not self._trusted():
            return {"ok": False, "error": t("desktop_invalid_request")}
        try:
            return getattr(self._service, name)(*args)
        except (ValueError, TypeError, OSError):
            return {"ok": False, "error": t("desktop_invalid_request")}

    def bootstrap(self):
        return self._call("bootstrap")

    def get_snapshot(self, sort_key=None, sort_direction=None):
        if not self._trusted():
            return {"ok": False, "error": t("desktop_invalid_request")}
        if sort_key is None:
            if sort_direction is not None:
                return {"ok": False, "error": t("desktop_invalid_request")}
        elif (not isinstance(sort_key, str) or sort_key not in DESKTOP_SORT_COLUMNS
              or sort_direction not in ("asc", "desc")):
            return {"ok": False, "error": t("desktop_invalid_request")}
        data = self._call("get_snapshot", sort_key, sort_direction)
        if "counts" in data:
            running = data["counts"]["running"]
            title = t("win_title", n=running) if running else TITLE
            if title != self._title:
                self._title = title
                self._window.set_title(title)
        return data

    def get_detail(self, key):
        return self._call("get_detail", key)

    def save_preferences(self, patch):
        data = self._call("save_preferences", patch)
        if "preferences" in data:
            tint_titlebar(self._window, theme_colors(data["preferences"]["theme"])[0])
        return data

    def open_result(self, key):
        return self._call("open_result", key)

    def open_log(self, key):
        return self._call("open_log", key)

    def copy_resume(self, key):
        return self._call("copy_resume", key)

    def request_stop(self, key):
        return self._call("request_stop", key)

    def confirm_stop(self, token):
        return self._call("confirm_stop", token)

    def get_scene(self, columns):
        return self._call("get_scene", columns)

    def quit(self):
        if not self._trusted():
            return {"ok": False}
        self._window.destroy()
        return {"ok": True}

    def toggle_fullscreen(self):
        if not self._trusted():
            return {"ok": False}
        self._window.toggle_fullscreen()
        return {"ok": True}


def _smoke_layout_problem(dom, *, require_stop=False):
    if dom["pageOverflow"]:
        return "page overflow"
    if dom["visibleRows"] < 1:
        return "no visible list rows"
    if not dom["headerFit"] or not dom["statusFit"]:
        return "header/status text overflow"
    if require_stop and dom["stopInWindow"] is not True:
        return "Stop action is outside the window"
    return None


def _place_smoke_offscreen(form):
    from System.Drawing import Point
    from System.Windows.Forms import FormStartPosition, SystemInformation

    desktop = SystemInformation.VirtualScreen
    form.StartPosition = FormStartPosition.Manual
    form.Location = Point(desktop.Left - form.Width - 100, desktop.Top - form.Height - 100)


def _invisible_smoke_initializer(initialize, place=_place_smoke_offscreen):
    """Keep the smoke host outside all displays, without activation or a taskbar entry."""
    def invisible(form, window, *args, **kwargs):
        # Set this before WebView2 binds to the HWND: changing it later recreates the handle and aborts init.
        form.ShowInTaskbar = False
        initialize(form, window, *args, **kwargs)
        place(form)
        # WebView2 defers navigation if its WinForms parent is hidden. Render wholly outside the virtual desktop.
        window.hidden = False
    return invisible


@contextmanager
def _smoke_host(enabled):
    if not enabled or os.name != "nt":
        yield
        return
    from webview.platforms.winforms import BrowserView

    form = BrowserView.BrowserForm
    original = form.__init__
    form.__init__ = _invisible_smoke_initializer(original)
    try:
        yield
    finally:
        form.__init__ = original


def smoke(window, service, report):
    """Bounded synthetic check of the actual renderer and reader for packaged builds."""
    deadline = time.monotonic() + 30
    stage = "window load"

    def dom_state():
        script = """
            (() => {
              const visible = (el) => getComputedStyle(el).display !== 'none';
              const rows = Array.from(document.querySelectorAll('#runs-body tr'));
              const selected = rows.find((row) => row.getAttribute('aria-selected') === 'true');
              const list = document.getElementById('list-wrap');
              const listRect = list.getBoundingClientRect();
              const visibleRows = rows.filter((row) => {
                const rect = row.getBoundingClientRect();
                return getComputedStyle(row).display !== 'none' && rect.height > 0
                  && rect.bottom > listRect.top && rect.top < listRect.bottom
                  && rect.right > listRect.left && rect.left < listRect.right;
              }).length;
              const stop = document.querySelector('#sel-actions [data-act="stop"]');
              const selectedHero = !document.getElementById('selected').classList.contains('empty');
              const stopRect = stop?.getBoundingClientRect();
              const stopInWindow = !selectedHero ? null : !!stop && !stop.hidden
                && getComputedStyle(stop).display !== 'none' && stopRect.width > 0 && stopRect.height > 0
                && stopRect.left >= -1 && stopRect.top >= -1
                && stopRect.right <= innerWidth + 1 && stopRect.bottom <= innerHeight + 1;
              const headers = Array.from(document.querySelectorAll('#runs thead th')).filter(visible);
              return {
                ready: document.getElementById('boot').hidden,
                home: !document.getElementById('home').hidden,
                viewport: [innerWidth, innerHeight],
                pageOverflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)
                  > innerWidth + 1,
                listOverflow: list.scrollWidth > list.clientWidth + 1,
                tableOverflow: document.getElementById('runs').scrollWidth
                  > document.getElementById('runs').clientWidth + 1,
                rows: rows.length,
                visibleRows,
                stopInWindow,
                rowLabels: rows.map((row) => row.querySelector('.c-name')?.textContent || ''),
                selectedId: selected?.id || '',
                selectedLabel: selected?.querySelector('.c-name')?.textContent || '',
                selectedAge: selected?.querySelector('.c-last')?.textContent || '',
                tab: Array.from(document.querySelectorAll('#tabs [role=tab]'))
                  .find((tab) => tab.getAttribute('aria-selected') === 'true')?.id || '',
                infoVisible: !document.getElementById('pane-info').hidden,
                follow: document.getElementById('btn-follow').getAttribute('aria-pressed') === 'true',
                activeSort: document.querySelector('#runs th[aria-sort="ascending"] .sort-head')
                  ?.dataset.sort || '',
                moreVisible: visible(document.getElementById('btn-actions')),
                dialogOpen: !document.getElementById('overlay').hidden,
                dialogButtons: document.querySelectorAll('#dialog .dialog-buttons button').length,
                nameSort: document.querySelector('.sort-head[data-sort=name]')
                  .closest('th').getAttribute('aria-sort'),
                headerFit: headers.every((th) => {
                  const label = th.querySelector('.sort-head span:first-child');
                  return !label || label.scrollWidth <= label.clientWidth + 1;
                }),
                statusFit: rows.every((row) => {
                  const cell = row.querySelector('.c-status');
                  return !cell || cell.scrollWidth <= cell.clientWidth + 1;
                }),
              };
            })()
            """
        return window.evaluate_js(script)

    def wait_for(label, predicate, seconds=3.5):
        stop = min(deadline - 0.5, time.monotonic() + seconds)
        latest = None
        while time.monotonic() < stop:
            latest = dom_state()
            if latest and predicate(latest):
                return latest
            time.sleep(0.1)
        raise TimeoutError(label if latest is None else f"{label}: {latest}")

    def stable_session(dom, selected_id, selected_label):
        return (dom["selectedId"] == selected_id and dom["selectedLabel"] == selected_label
                and dom["tab"] == "tab-info" and dom["infoVisible"] and not dom["follow"])

    snapshot, dom = {}, None
    try:
        if not window.events.loaded.wait(20):
            raise TimeoutError("window load")
        healthy_since = None
        while time.monotonic() < deadline:
            snapshot = service.get_snapshot()
            dom = dom_state()
            if (snapshot["reader"]["state"] == "ready" and snapshot["runs"] and dom and dom["ready"]
                    and dom["rows"] == len(snapshot["runs"]) and not dom["home"]):
                issue = _smoke_layout_problem(dom)
                if issue:
                    raise ValueError(issue)
                # Cover the whole beam animation cycle; a single frame can miss transient page overflow.
                healthy_since = healthy_since or time.monotonic()
                if time.monotonic() - healthy_since < 3.5:
                    time.sleep(0.1)
                    continue
                break
            healthy_since = None
            time.sleep(0.1)
        else:
            raise TimeoutError("reader/frontend readiness")

        stage = "default order and visible headers"
        default_order = [str(run["label"]) for run in snapshot["runs"]]
        if dom["rowLabels"] != default_order:
            raise ValueError(f"default row order: {dom['rowLabels']}")

        stage = "selection, Details tab, and paused follow state"
        newest = max(range(len(snapshot["runs"])), key=lambda i: snapshot["runs"][i].get("last_ts") or 0)
        window.evaluate_js(f"document.querySelectorAll('#runs-body tr')[{newest}].click();"
                           "document.getElementById('tab-info').click();"
                           "document.getElementById('btn-follow').click();")
        dom = wait_for(stage, lambda d: d["selectedId"] and d["tab"] == "tab-info" and not d["follow"])
        selected_id, selected_label = dom["selectedId"], dom["selectedLabel"]
        age_before = dom["selectedAge"]
        issue = _smoke_layout_problem(dom, require_stop=True)
        if issue:
            raise ValueError(issue)

        stage = "ascending name sort"
        expected_asc = sorted(default_order, key=str.casefold)
        window.evaluate_js("document.querySelector('.sort-head[data-sort=name]').click()")
        dom = wait_for(stage, lambda d: d["nameSort"] == "ascending" and d["rowLabels"] == expected_asc
                       and stable_session(d, selected_id, selected_label))

        stage = "descending name sort"
        window.evaluate_js("document.querySelector('.sort-head[data-sort=name]').click()")
        dom = wait_for(stage, lambda d: d["nameSort"] == "descending"
                       and d["rowLabels"] == list(reversed(expected_asc))
                       and stable_session(d, selected_id, selected_label))

        stage = "sort reset"
        expected_default = [str(run["label"]) for run in service.get_snapshot()["runs"]]
        window.evaluate_js("document.dispatchEvent(new KeyboardEvent('keydown',"
                           "{key:'r',ctrlKey:true,bubbles:true}))")
        dom = wait_for(stage, lambda d: d["nameSort"] == "none"
                       and d["rowLabels"] == expected_default and stable_session(d, selected_id, selected_label))

        stage = "keyboard sort neighbours"
        for key, column in ((">", "status"), ("<", "pid")):
            expected = [str(run["label"]) for run in service.get_snapshot(column, "asc")["runs"]]
            window.evaluate_js("document.dispatchEvent(new KeyboardEvent('keydown',"
                               f"{{key:'{key}',bubbles:true}}))")
            dom = wait_for(stage, lambda d, col=column, labels=expected:
                           d["activeSort"] == col and d["rowLabels"] == labels
                           and stable_session(d, selected_id, selected_label))
        window.evaluate_js("document.dispatchEvent(new KeyboardEvent('keydown',"
                           "{key:'r',ctrlKey:true,bubbles:true}))")
        dom = wait_for("keyboard sort reset", lambda d: not d["activeSort"]
                       and stable_session(d, selected_id, selected_label))

        stage = "overflow action menu"
        window.resize(800, 560)
        dom = wait_for(stage, lambda d: d["moreVisible"])
        window.evaluate_js("document.getElementById('btn-actions').focus();"
                           "document.getElementById('btn-actions').click()")
        dom = wait_for(stage, lambda d: d["dialogOpen"] and d["dialogButtons"] == 4)
        window.evaluate_js("document.dispatchEvent(new KeyboardEvent('keydown',"
                           "{key:'Escape',bubbles:true}))")
        dom = wait_for(stage, lambda d: not d["dialogOpen"]
                       and stable_session(d, selected_id, selected_label))
        issue = _smoke_layout_problem(dom, require_stop=True)
        if issue:
            raise ValueError(issue)

        stage = "age refresh after paused follow"
        stop = min(deadline - 0.5, time.monotonic() + 4.0)
        while time.monotonic() < stop and dom["selectedAge"] == age_before:
            time.sleep(0.1)
            dom = dom_state()
        if dom["selectedAge"] == age_before or not stable_session(dom, selected_id, selected_label):
            raise TimeoutError(stage)

        report.update(ok=True, version=__version__, runs=len(snapshot["runs"]), dom=dom,
                      reader=snapshot["reader"]["state"], sort="ascending/descending/reset",
                      selection="stable", tab="stable", follow="paused", keyboard_sort="passed", action_menu="passed")
    except Exception as exc:
        report.update(ok=False, error=type(exc).__name__, stage=stage, detail=str(exc)[:300])
        report["readiness"] = {"reader": snapshot.get("reader"), "runs": len(snapshot.get("runs", [])),
                               "dom": dom}
    finally:
        window.destroy()


def open_window(argv: list[str] | argparse.Namespace) -> int:
    if not isinstance(argv, argparse.Namespace):
        argv = list(argv)
        if argv and argv[0] == "--cleanup-profile":
            return _cleanup_profile_cli(argv[1:])

    from bitacora.cli import build_parser
    from bitacora.desktop import DesktopService
    from bitacora.runtime import prepare

    if isinstance(argv, argparse.Namespace):
        args = argv
    else:
        parser = build_parser()
        parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
        parser.add_argument("--smoke-report", help=argparse.SUPPRESS)
        parser.add_argument("--smoke-size", choices=("800x560", "1000x640"),
                            default="1000x640", help=argparse.SUPPRESS)
        parser.add_argument("--smoke-font-size", type=int, choices=(14, 18, 22), default=14, help=argparse.SUPPRESS)
        args = parser.parse_args(argv)
    smoke_test = getattr(args, "smoke_test", False)
    if smoke_test:
        args.demo = True
        args.view = "everything"
    report = {}
    with prepare(args) as runtime:
        service = DesktopService(runtime, view=args.view, live_only=args.live, scene_time=args.time)
        if smoke_test:
            service.save_preferences({"font_size": args.smoke_font_size})
        try:
            import webview

            application_identity()

            with temporary_profile(report=report if smoke_test else None,
                                   report_path=getattr(args, "smoke_report", None) if smoke_test else None) as profile:
                width, height = saved_size()
                if smoke_test:
                    width, height = map(int, args.smoke_size.split("x"))
                bg = theme_colors(service.bootstrap()["preferences"]["theme"])[0]
                window = webview.create_window(TITLE, html=page_html(), width=width, height=height,
                                               min_size=MIN_SIZE, hidden=smoke_test, background_color=bg,
                                               focus=not smoke_test, frameless=smoke_test, shadow=not smoke_test)
                api = WindowBridge(service)
                api._window = window
                browser_process = []

                def remember_renderers():
                    for process in owned_renderers(profile):
                        if process not in browser_process:
                            browser_process.append(process)

                def loaded():
                    tint_titlebar(window, bg)
                    remember_renderers()

                def closing():
                    save_size(window)
                    remember_renderers()

                # expose() dispatches a name allowlist. js_api=api also enables private nested-attribute traversal.
                window.expose(*(getattr(api, method) for method in METHODS))
                window.events.closing += closing
                window.events.loaded += loaded
                timer = None
                if smoke_test:
                    timer = threading.Timer(35, window.destroy)
                    timer.daemon = True
                    timer.start()
                try:
                    with _smoke_host(smoke_test):
                        webview.start(lambda: smoke(window, service, report) if smoke_test else None,
                                      icon=icon_file(), storage_path=profile,
                                      gui="edgechromium" if os.name == "nt" else None)
                finally:
                    if timer:
                        timer.cancel()
                    # Only the browser process created for this exact window is stopped. WebView2 can outlive
                    # its host in one-file builds; capture profile-owned utility processes before they orphan.
                    remember_renderers()
                    if smoke_test:
                        report["renderer_owned_processes"] = len(browser_process)
                    if browser_process:
                        stopped = stop_owned_renderers(browser_process)
                        if smoke_test:
                            report["renderer_stopped"] = stopped["stopped"]
                            if stopped["failures"]:
                                report["renderer_stop_failures"] = stopped["failures"]
                            if stopped["reused"]:
                                report["renderer_reused"] = stopped["reused"]
        except ImportError:
            report.update(ok=False, error=t("desktop_window_missing"))
        except Exception as exc:
            report.update(ok=False, error=t("desktop_window_failed", error=type(exc).__name__))
            if smoke_test:
                import traceback
                report["diagnostic"] = traceback.format_exc()
        finally:
            service.close()
    if getattr(args, "smoke_report", None):
        Path(args.smoke_report).write_bytes(json.dumps(report, ensure_ascii=True, indent=2).encode("utf-8"))
    if smoke_test and report.get("cleanup") == "pending":
        return 0
    if report.get("ok") is False or (smoke_test and not report.get("ok")):
        if not smoke_test and os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, report["error"], TITLE, 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(open_window(sys.argv[1:]))
