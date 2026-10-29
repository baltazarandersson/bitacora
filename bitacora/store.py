"""Where Bitácora keeps its files: config.toml (yours) and state.json (its own)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

APP = "bitacora"
MAX_HOURS = 72  # the longest history window either interface offers; longer saved or configured values are clamped


def _platform_dir(kind: str) -> Path:
    override = os.environ.get("BITACORA_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        env = "APPDATA" if kind == "config" else "LOCALAPPDATA"
        return Path(os.environ.get(env) or Path.home() / "AppData" / ("Roaming" if kind == "config" else "Local")) / APP
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP
    env, default = ("XDG_CONFIG_HOME", ".config") if kind == "config" else ("XDG_STATE_HOME", ".local/state")
    return Path(os.environ.get(env) or Path.home() / default) / APP


def config_path() -> Path:
    return _platform_dir("config") / "config.toml"


def state_path() -> Path:
    return _platform_dir("state") / "state.json"


def _read_dict(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _has_sessions(memory: dict, legacy: dict | None) -> bool:
    if not isinstance(legacy, dict):
        return False
    return all(not isinstance(entry, dict) or
               (isinstance(memory.get(key), dict) and all(field in memory[key] for field in entry))
               for key, entry in legacy.items())


def _atomic_write(path: Path, data: dict) -> bool:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(json.dumps(data, indent=1).encode("utf-8"))
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


@contextmanager
def _file_lock(path: Path):
    """Serialize read/merge/replace updates across UI and reader processes on all supported OSes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "a+b") as fh:
        if os.name == "nt":
            import msvcrt

            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"\0")
                fh.flush()
            fh.seek(0)
            deadline = time.monotonic() + 5.0
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("state file lock is busy") from exc
                    time.sleep(0.02)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            deadline = time.monotonic() + 5.0
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("state file lock is busy") from exc
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@dataclass
class Config:
    lang: str | None = None
    hours: float = 24
    notify: bool = True
    views: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def capped_hours(value):
    """A history window from config or state: None unless it is a positive number, otherwise capped at MAX_HOURS."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value:
        return None
    return min(value, MAX_HOURS)


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    cfg = Config()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except (OSError, tomllib.TOMLDecodeError) as e:
        cfg.errors.append(f"{path}: {e}")
        return cfg
    if isinstance(raw.get("lang"), str):
        cfg.lang = raw["lang"]
    hours = capped_hours(raw.get("hours"))
    if hours is not None:
        cfg.hours = float(hours)
    if isinstance(raw.get("notify"), bool):
        cfg.notify = raw["notify"]
    views = raw.get("views")
    if isinstance(views, dict):
        cfg.views = {k: v for k, v in views.items() if isinstance(v, dict)}
    return cfg


class _JsonFile:
    def __init__(self, path: Path):
        self.path = path
        self.data = _read_dict(self.path)
        self.dirty = False

    def save(self) -> bool:
        if not self.dirty:
            return True
        saved = _atomic_write(self.path, self.data)
        if saved:
            self.dirty = False
        return saved


class State(_JsonFile):
    """Your preferences from the setup screen. Only the UI writes it."""

    def __init__(self, path: Path | None = None):
        super().__init__(path or state_path())
        self._changed: set[str] = set()
        self._legacy_sessions = self.data.pop("sessions", None)
        if isinstance(self._legacy_sessions, dict):
            # Saving preferences before sessions.json is durable must preserve the recoverable old copy.
            self.dirty = True
        self._clamp()

    def _clamp(self) -> None:
        hours = capped_hours(self.data.get("hours"))
        if hours is not None:
            self.data["hours"] = hours  # a longer window saved by an older version is clamped when loaded

    @property
    def first_run(self) -> bool:
        return "view" not in self.data

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        # Every explicit choice is written. This copy can be older than the file when another interface saved
        # since it was read, so an equal value here says nothing about what is on disk.
        self.data[key] = value
        self.dirty = True
        self._changed.add(key)

    def refresh(self) -> set[str]:
        """Read the file again and return the keys another interface saved since this copy last looked.

        Only those keys move. Everything else keeps the value this copy already shows, so a launch flag (--view)
        survives until someone saves that setting, and saving one setting never writes back a stale copy of
        another. A copy with changes still waiting to be saved is left alone.
        """
        if self.dirty:
            return set()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()  # mid-replace or unreadable: keep what this copy shows rather than adopt nothing
        if not isinstance(raw, dict):
            return set()
        before = self.data
        self.data = {key: value for key, value in raw.items() if key != "sessions"}
        self._clamp()
        return {key for key in before.keys() | self.data.keys() if before.get(key) != self.data.get(key)}

    def save(self) -> bool:
        if not self.dirty:
            return True
        memory_path = self.path.with_name("sessions.json")
        try:
            with _file_lock(self.path):
                data = _read_dict(self.path)
                for key in self._changed:
                    if key in self.data:
                        data[key] = self.data[key]
                    else:
                        data.pop(key, None)
                memory = _read_dict(memory_path)
                required = data.get("sessions")
                if not isinstance(required, dict):
                    required = self._legacy_sessions
                memory_ready = memory_path.is_file() and _has_sessions(memory, required)
                if memory_ready:
                    data.pop("sessions", None)
                elif isinstance(data.get("sessions"), dict):
                    pass  # Keep the freshest on-disk legacy copy until migration is durable.
                elif isinstance(self._legacy_sessions, dict):
                    data["sessions"] = self._legacy_sessions
                if not _atomic_write(self.path, data):
                    return False
        except OSError:
            return False
        self.data = {key: value for key, value in data.items() if key != "sessions"}
        self._legacy_sessions = data.get("sessions")
        self._changed.clear()
        self.dirty = False
        return True


class Placement(_JsonFile):
    """Where the window was left (its size). Only the window writes it, apart from state.json."""

    def __init__(self, path: Path | None = None):
        super().__init__(path or state_path().with_name("window.json"))

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        if self.data.get(key) != value:
            self.data[key] = value
            self.dirty = True


class Memory(_JsonFile):
    """What Bitácora remembers about each session (label, result and log paths). Only the reader writes it."""

    def __init__(self, path: Path | None = None):
        path = path or state_path().with_name("sessions.json")
        super().__init__(path)
        self._changed: set[str] = set()
        self.legacy_path = path.with_name("state.json")
        self._legacy_sessions = _read_dict(self.legacy_path).get("sessions")
        if isinstance(self._legacy_sessions, dict):
            for key, entry in self._legacy_sessions.items():
                if not isinstance(entry, dict):
                    continue
                current = self.data.get(key)
                if not isinstance(current, dict):
                    self.data[key] = dict(entry)
                    self.dirty = True
                    self._changed.add(key)
                else:
                    for field, value in entry.items():
                        if field not in current:
                            current[field] = value
                            self.dirty = True
                            self._changed.add(key)

    def session(self, key: str) -> dict:
        entry = self.data.get(key)
        return entry if isinstance(entry, dict) else {}

    def remember(self, key: str, entry: dict) -> None:
        if self.data.get(key) != entry:
            self.data[key] = entry
            self.dirty = True
            self._changed.add(key)

    def save(self) -> bool:
        if self.dirty:
            try:
                with _file_lock(self.path):
                    data = _read_dict(self.path)
                    for key in self._changed:
                        if key in self.data:
                            data[key] = self.data[key]
                        else:
                            data.pop(key, None)
                    if not _atomic_write(self.path, data):
                        return False  # The old state.json remains the recoverable source until this succeeds.
            except OSError:
                return False
            self.data = data
            self._changed.clear()
            self.dirty = False

        if not isinstance(self._legacy_sessions, dict):
            return True
        # Only remove the old copy after every legacy key is present in a successfully written sessions.json.
        try:
            with _file_lock(self.legacy_path):
                legacy = _read_dict(self.legacy_path)
                old_sessions = legacy.get("sessions")
                if not _has_sessions(self.data, old_sessions):
                    return True
                legacy.pop("sessions", None)
                if not _atomic_write(self.legacy_path, legacy):
                    return False
        except OSError:
            return False
        self._legacy_sessions = None
        return True
