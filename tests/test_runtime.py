import os
from pathlib import Path
from types import SimpleNamespace

from bitacora.runtime import prepare


def test_demo_prepare_uses_each_provider_home_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", "sentinel-codex")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "sentinel-claude")
    args = SimpleNamespace(demo=True, lang="en", hours=None)
    with prepare(args) as runtime:
        assert runtime.demo_root is not None
        providers = [provider for provider in runtime.providers if provider.home_env]
        assert {provider.name for provider in providers} == {"codex", "claude", "opencode"}
        homes = {"codex": runtime.demo_root / "codex", "claude": runtime.demo_root / "claude",
                 "opencode": runtime.demo_root / "opencode" / "opencode.db"}  # OpenCode's home is its database
        for provider in providers:
            assert Path(os.environ[provider.home_env]) == homes[provider.name]
            assert provider.available()
        assert Path(os.environ["BITACORA_HOME"]) == runtime.demo_root / "home"
    assert os.environ["CODEX_HOME"] == "sentinel-codex"
    assert os.environ["CLAUDE_CONFIG_DIR"] == "sentinel-claude"


def test_demo_and_reader_apply_provider_switches_from_the_user_config(tmp_path, monkeypatch):
    from bitacora.store import load_config
    from bitacora.worker import configured_providers

    user_home = tmp_path / "user"
    user_home.mkdir()
    (user_home / "config.toml").write_text("[providers]\nopencode = false\n", encoding="utf-8")
    monkeypatch.setenv("BITACORA_HOME", str(user_home))
    args = SimpleNamespace(demo=True, lang="en", hours=None)
    with prepare(args) as runtime:
        assert [provider.name for provider in runtime.providers] == ["codex", "claude"]
        assert [provider.name for provider in configured_providers()] == ["codex", "claude"]
        assert load_config().providers == {"opencode": False}
    assert os.environ["BITACORA_HOME"] == str(user_home)
