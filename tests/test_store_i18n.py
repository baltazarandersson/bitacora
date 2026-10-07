import re

from bitacora import i18n
from bitacora.store import State, config_path, load_config, state_path


def test_paths_follow_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BITACORA_HOME", str(tmp_path))
    assert config_path() == tmp_path / "config.toml"
    assert state_path() == tmp_path / "state.json"


def test_config_defaults_and_values(tmp_path):
    path = tmp_path / "config.toml"
    assert load_config(path).hours == 24
    path.write_text('lang = "es"\nhours = 72\nnotify = false\n[views.codex]\nname = "Codex"\n'
                    'providers = ["codex"]\n', encoding="utf-8")
    cfg = load_config(path)
    assert (cfg.lang, cfg.hours, cfg.notify) == ("es", 72, False)
    assert cfg.views == {"codex": {"name": "Codex", "providers": ["codex"]}}


def test_broken_config_falls_back(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("hours = [", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.hours == 24 and cfg.errors


def test_state_roundtrip(tmp_path):
    path = tmp_path / "sub" / "state.json"
    st = State(path)
    assert st.first_run
    st.set("view", "live")
    st.remember("codex:1", {"label": "x"})
    st.save()
    again = State(path)
    assert not again.first_run
    assert again.session("codex:1") == {"label": "x"}
    path.write_text("[]", encoding="utf-8")
    assert State(path).first_run


def test_every_string_has_both_languages_with_same_placeholders():
    for key, (en, es) in i18n.S.items():
        assert en and es, key
        assert set(re.findall(r"{(\w+)}", en)) == set(re.findall(r"{(\w+)}", es)), key


def test_language_selection(monkeypatch):
    for var in ("BITACORA_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "es_UY.UTF-8")
    assert i18n.detect() == "es"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n.detect() == "en"
    assert i18n.set_lang("es") == "es"
    assert i18n.t("st_done") == "✓ terminada"
    assert i18n.t("n_killed", pid=3, n=2) == "Matado PID 3 (2 procesos)."
    assert i18n.set_lang("xx") == "en"
    assert i18n.t("missing-key") == "missing-key"
