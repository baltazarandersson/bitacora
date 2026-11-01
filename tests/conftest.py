from __future__ import annotations

import time

import pytest

from bitacora import demo, i18n
from bitacora.providers import all_providers


@pytest.fixture(autouse=True)
def english():
    i18n.set_lang("en")
    yield


@pytest.fixture(autouse=True)
def no_opencode_database(tmp_path, monkeypatch):
    """No test reads the real OpenCode database: the override names a file that never exists."""
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "unset-opencode" / "opencode.db"))


@pytest.fixture
def demo_env(tmp_path, monkeypatch):
    """Demo homes for Codex and Claude Code plus an isolated Bitácora home."""
    now = time.time()
    data = demo.generate(tmp_path / "demo", now)
    for provider in all_providers():
        if provider.home_env and provider.name in data["homes"]:
            monkeypatch.setenv(provider.home_env, str(data["homes"][provider.name]))
    monkeypatch.setenv("BITACORA_HOME", str(tmp_path / "home"))
    data["now"] = now
    return data
