"""Where Bitácora keeps its files: config.toml (yours) and state.json (its own)."""
from __future__ import annotations

import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP = "bitacora"


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


@dataclass
class Config:
    lang: str | None = None
    hours: float = 24
    notify: bool = True
    views: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


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
    if isinstance(raw.get("hours"), (int, float)) and raw["hours"] > 0:
        cfg.hours = float(raw["hours"])
    if isinstance(raw.get("notify"), bool):
        cfg.notify = raw["notify"]
    views = raw.get("views")
    if isinstance(views, dict):
        cfg.views = {k: v for k, v in views.items() if isinstance(v, dict)}
    return cfg


class State:
    """UI state and what Bitácora remembers about each session (label, result and log paths)."""

    def __init__(self, path: Path | None = None):
        self.path = path or state_path()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("sessions", {})
        self.dirty = False

    @property
    def first_run(self) -> bool:
        return "view" not in self.data

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        if self.data.get(key) != value:
            self.data[key] = value
            self.dirty = True

    def session(self, key: str) -> dict:
        return self.data["sessions"].get(key, {})

    def remember(self, key: str, entry: dict) -> None:
        if self.data["sessions"].get(key) != entry:
            self.data["sessions"][key] = entry
            self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass
