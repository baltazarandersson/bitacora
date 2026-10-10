import json
import re

from bitacora import i18n
from bitacora.store import Memory, State, capped_hours, config_path, load_config, state_path


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


def test_history_window_is_capped_at_72_hours_when_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("BITACORA_HOME", str(tmp_path))
    assert capped_hours(168) == 72 and capped_hours(72) == 72 and capped_hours(36) == 36
    assert capped_hours(float("inf")) == 72
    assert all(capped_hours(bad) is None for bad in (0, -6, float("nan"), True, "6", None))
    path = tmp_path / "config.toml"
    path.write_text("hours = 168\n", encoding="utf-8")
    assert load_config(path).hours == 72
    path.write_text("hours = true\n", encoding="utf-8")  # a boolean is not a window
    assert load_config(path).hours == 24
    (tmp_path / "state.json").write_text(json.dumps({"hours": 168, "view": "live"}), encoding="utf-8")
    assert State().get("hours") == 72 and State().get("view") == "live"


def test_a_saved_value_is_written_even_when_this_copy_is_stale(tmp_path):
    path = tmp_path / "state.json"
    desktop = State(path)
    desktop.set("hours", 24)
    assert desktop.save()
    terminal = State(path)  # another interface saves a different window in the meantime
    terminal.set("hours", 72)
    assert terminal.save()
    desktop.set("hours", 24)  # equal to this copy, but not to the file
    assert desktop.save()
    assert State(path).get("hours") == 24


def test_state_roundtrip(tmp_path):
    path = tmp_path / "sub" / "state.json"
    st = State(path)
    assert st.first_run
    st.set("view", "live")
    st.save()
    again = State(path)
    assert not again.first_run and again.get("view") == "live"
    path.write_text("[]", encoding="utf-8")
    assert State(path).first_run


def test_memory_roundtrip_and_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("BITACORA_HOME", str(tmp_path))
    old = tmp_path / "state.json"
    old.write_text(json.dumps({"view": "live", "sessions": {"codex:1": {"label": "x"}}}), encoding="utf-8")
    mem = Memory()  # 1.0 kept sessions inside state.json
    assert mem.session("codex:1") == {"label": "x"}
    mem.remember("codex:2", {"label": "y"})
    mem.save()
    assert Memory().session("codex:2") == {"label": "y"}
    assert "sessions" not in State().data
    assert "sessions" not in json.loads(old.read_text(encoding="utf-8"))


def test_ui_saves_preserve_legacy_sessions_until_memory_is_durable(tmp_path):
    state_file = tmp_path / "state.json"
    memory_file = tmp_path / "sessions.json"
    legacy = {"codex:1": {"label": "old", "log": "synthetic.log"}}
    state_file.write_text(json.dumps({"view": "background", "sessions": legacy}), encoding="utf-8")

    first, second = State(state_file), State(state_file)
    first.set("view", "live")
    second.set("lang", "es")
    assert first.save() and second.save()
    before_migration = json.loads(state_file.read_text(encoding="utf-8"))
    assert before_migration["sessions"] == legacy
    assert before_migration["view"] == "live" and before_migration["lang"] == "es"

    memory = Memory(memory_file)
    assert memory.session("codex:1") == legacy["codex:1"]
    assert memory.save()
    assert json.loads(memory_file.read_text(encoding="utf-8")) == legacy
    assert "sessions" not in json.loads(state_file.read_text(encoding="utf-8"))


def test_failed_memory_migration_keeps_legacy_copy(tmp_path, monkeypatch):
    import bitacora.store as store

    state_file = tmp_path / "state.json"
    memory_file = tmp_path / "sessions.json"
    legacy = {"claude:1": {"label": "synthetic"}}
    state_file.write_text(json.dumps({"sessions": legacy}), encoding="utf-8")
    original = store._atomic_write

    def fail_memory(path, data):
        if path == memory_file:
            return False
        return original(path, data)

    monkeypatch.setattr(store, "_atomic_write", fail_memory)
    memory = Memory(memory_file)
    assert not memory.save()
    assert not memory_file.exists()
    assert json.loads(state_file.read_text(encoding="utf-8"))["sessions"] == legacy


def test_lock_acquisition_failure_returns_false_and_preserves_legacy(tmp_path, monkeypatch):
    import bitacora.store as store

    class DeniedLock:
        def __enter__(self):
            raise PermissionError("synthetic lock denial")

        def __exit__(self, *_args):
            return False

    state_file = tmp_path / "state.json"
    memory_file = tmp_path / "sessions.json"
    legacy = {"codex:1": {"label": "synthetic"}}
    original = json.dumps({"view": "background", "sessions": legacy})
    state_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(store, "_file_lock", lambda _path: DeniedLock())

    state = State(state_file)
    state.set("view", "live")
    assert state.save() is False
    memory = Memory(memory_file)
    assert memory.save() is False
    assert state_file.read_text(encoding="utf-8") == original
    assert not memory_file.exists()


def test_custom_memory_path_migrates_its_sibling_state(tmp_path):
    custom = tmp_path / "custom"
    custom.mkdir()
    legacy = {"codex:local": {"label": "synthetic"}}
    (custom / "state.json").write_text(json.dumps({"sessions": legacy}), encoding="utf-8")

    memory = Memory(custom / "sessions.json")
    assert memory.session("codex:local") == legacy["codex:local"]
    assert memory.save()
    assert json.loads((custom / "sessions.json").read_text(encoding="utf-8")) == legacy
    assert "sessions" not in json.loads((custom / "state.json").read_text(encoding="utf-8"))


def test_migration_replaces_invalid_target_entry_before_cleanup(tmp_path):
    state_file = tmp_path / "state.json"
    memory_file = tmp_path / "sessions.json"
    legacy = {"codex:1": {"label": "recoverable"}}
    state_file.write_text(json.dumps({"sessions": legacy}), encoding="utf-8")
    memory_file.write_text(json.dumps({"codex:1": "invalid"}), encoding="utf-8")

    memory = Memory(memory_file)
    assert memory.session("codex:1") == legacy["codex:1"]
    assert memory.save()
    assert json.loads(memory_file.read_text(encoding="utf-8")) == legacy
    assert "sessions" not in json.loads(state_file.read_text(encoding="utf-8"))


def test_fresh_state_and_memory_do_not_create_files(tmp_path):
    state_file = tmp_path / "state.json"
    memory_file = tmp_path / "sessions.json"
    assert State(state_file).save()
    assert Memory(memory_file).save()
    assert not state_file.exists() and not memory_file.exists()


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
    assert i18n.t("n_killed", pid=3, n=2) == "Se detuvo el PID 3 (2 procesos)."
    assert i18n.set_lang("xx") == "en"
    assert i18n.t("missing-key") == "missing-key"
