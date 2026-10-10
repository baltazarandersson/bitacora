import os

from bitacora.util import (
    count_lines,
    first_line,
    fmt_dur,
    fmt_tokens,
    label_from_out,
    label_from_prompt,
    norm_path,
    parse_iso,
    rel_to,
    same_path,
)


def test_parse_iso():
    assert parse_iso("2026-01-02T03:04:05Z") == parse_iso("2026-01-02T03:04:05+00:00")
    assert parse_iso(None) is None
    assert parse_iso("yesterday") is None
    assert parse_iso(123) is None


def test_fmt_dur():
    assert fmt_dur(None) == "-"
    assert fmt_dur(-1) == "-"
    assert fmt_dur(9) == "9s"
    assert fmt_dur(75) == "1m15s"
    assert fmt_dur(3 * 3600 + 120) == "3h02m"
    assert fmt_dur(50 * 3600) == "2d02h"


def test_fmt_tokens():
    assert fmt_tokens(0) == "-"
    assert fmt_tokens(950) == "950"
    assert fmt_tokens(12_300) == "12.3k"
    assert fmt_tokens(4_500_000) == "4.5M"


def test_first_line_and_labels():
    assert first_line("\n  hello\nworld") == "hello"
    assert first_line("x" * 10, 5) == "xxxx…"
    assert first_line(None) == ""
    assert label_from_prompt("# Task: fix the login bug\nmore") == "fix the login bug"
    assert label_from_prompt("Tarea: revisar") == "revisar"
    assert label_from_out("/tmp/b0-contracts.result.md") == "b0-contracts"
    assert label_from_out("/tmp/s1a-result.md") == "s1a"
    assert label_from_out("/tmp/notes.json") == "notes.json"
    assert label_from_out(None) is None


def test_paths():
    if os.name == "nt":
        assert norm_path("/c/Users/me/x.log") == "C:\\Users\\me\\x.log"
        assert same_path("C:/Users/Me", "c:\\users\\me")
    assert norm_path("file:///tmp/a") is not None
    assert not same_path(None, "x")
    assert rel_to("/repo/src/a.py", "/repo") == "src/a.py"
    assert rel_to("/other/a.py", "/repo") == "/other/a.py"


def test_count_lines():
    assert count_lines("") == 0
    assert count_lines("a") == 1
    assert count_lines("a\nb\n") == 2
    assert count_lines("a\nb") == 2


def test_relaunch_drops_pyinstaller_state(monkeypatch):
    from bitacora import launcher
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "C:/tmp/_MEI123")
    monkeypatch.setenv("_MEIPASS2", "C:/tmp/_MEI123")
    env = launcher.clean_env()
    assert not any(k.upper().startswith(("_PYI", "_MEI")) for k in env)
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
