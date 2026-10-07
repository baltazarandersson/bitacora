from __future__ import annotations

import time

import pytest

from bitacora import demo, i18n


@pytest.fixture(autouse=True)
def english():
    i18n.set_lang("en")
    yield


@pytest.fixture
def demo_env(tmp_path, monkeypatch):
    """Demo homes for Codex and Claude Code plus an isolated Bitácora home."""
    now = time.time()
    data = demo.generate(tmp_path / "demo", now)
    monkeypatch.setenv("CODEX_HOME", str(data["codex_home"]))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(data["claude_home"]))
    monkeypatch.setenv("BITACORA_HOME", str(tmp_path / "home"))
    data["now"] = now
    return data
