import json
from types import SimpleNamespace

from bitacora import diagnostics, i18n
from bitacora.desktop import READ_LIMIT, DesktopService
from bitacora.model import MAX_QUARANTINE, Event, Run, Session
from bitacora.worker import MESSAGES, TAIL, Packer


def empty_homes(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))


def test_diagnostics_are_local_json_without_session_data(tmp_path, monkeypatch):
    empty_homes(tmp_path, monkeypatch)
    data = diagnostics.collect(passes=1)
    text = json.dumps(data, allow_nan=False)

    assert data["version"] and data["python"] and data["passes"] == 1
    assert {provider["name"] for provider in data["providers"]} == {"codex", "claude"}
    assert all(provider["available"] is False for provider in data["providers"])
    assert data["scan"]["runs"] == 0 and data["scan"]["sessions"] == 0
    assert data["limits"]["read_limit"] == READ_LIMIT
    assert str(tmp_path) not in text  # no provider paths or session content


def test_quarantine_summary_counts_metadata_only(tmp_path):
    first = Session(tmp_path / "first.jsonl")
    first.records_total, first.discarded = 7, 3
    first.title = "SECRETO"
    first.messages.append((1.0, "SECRETO"))
    first.quarantine.extend([(2, "json", "JSONDecodeError", 128), (5, "handle", "ValueError", 64)])
    second = Session(tmp_path / "second.jsonl")
    second.records_total, second.discarded = 4, 1
    second.quarantine.append((1, "handle", "ValueError", 32))

    summary = diagnostics._quarantine_summary([first, second])

    assert summary == {
        "sessions": 2,
        "records_total": 11,
        "discarded": 4,
        "kinds": {"handle": 2, "json": 1},
        "exceptions": {"JSONDecodeError": 1, "ValueError": 2},
        "sample_limit": MAX_QUARANTINE,
    }
    text = json.dumps(summary, allow_nan=False)
    assert str(tmp_path) not in text and "SECRETO" not in text


def test_diagnostics_quarantine_is_zero_without_sessions(tmp_path, monkeypatch):
    empty_homes(tmp_path, monkeypatch)
    data = diagnostics.collect(passes=1)

    assert data["quarantine"] == {"sessions": 0, "records_total": 0, "discarded": 0,
                                  "kinds": {}, "exceptions": {}, "sample_limit": MAX_QUARANTINE}
    assert str(tmp_path) not in json.dumps(data, allow_nan=False)


def test_process_prefixes_skip_providers_without_process_capability():
    providers = [
        SimpleNamespace(name="scan", process_names=("codex", "codex-helper"),
                        capabilities=SimpleNamespace(processes=True)),
        SimpleNamespace(name="logonly", process_names=("claude", "claude-helper"),
                        capabilities=SimpleNamespace(processes=False)),
    ]

    assert diagnostics._process_prefixes(providers) == ("codex", "codex-helper")


def test_diagnostics_memory_is_opt_in(tmp_path, monkeypatch):
    empty_homes(tmp_path, monkeypatch)
    assert "memory" not in diagnostics.collect(passes=1)
    data = diagnostics.collect(passes=1, memory=True)
    assert data["memory"]["tracemalloc_peak_mb"] >= 0


def test_cli_diagnostics_prints_json(tmp_path, monkeypatch, capsys):
    empty_homes(tmp_path, monkeypatch)
    from bitacora.cli import main

    main(["--diagnostics"])
    data = json.loads(capsys.readouterr().out)
    assert data["passes"] == 3 and data["scan"]["runs"] == 0


def test_packer_payload_is_bounded(tmp_path):
    session = Session(tmp_path / "session.jsonl")
    session.messages.extend((float(index), f"m{index}") for index in range(5000))
    for index in range(5000):
        session.add(Event(float(index), "note", "", "x"))
    payload = Packer().pack([Run("k", "codex", session, None, "x")], {})
    sent = payload["runs"][0]["session"]

    assert len(sent["messages"]) == MESSAGES
    assert sent["messages_total"] == 5000
    assert len(sent["events"]) <= TAIL


def test_desktop_file_reads_are_bounded(tmp_path):
    path = tmp_path / "big.log"
    path.write_text("x" * (READ_LIMIT * 2), encoding="utf-8")
    stub = SimpleNamespace(_path=lambda value: str(value) if value else None)

    tail = DesktopService._read(stub, path, tail=True)
    assert tail["available"] and len(tail["text"]) <= READ_LIMIT + 200
    assert i18n.t("desktop_file_tail") in tail["text"]

    head = DesktopService._read(stub, path)
    assert head["available"] and len(head["text"]) <= READ_LIMIT + 200
    assert i18n.t("desktop_file_head") in head["text"]
