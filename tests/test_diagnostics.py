import json
from types import SimpleNamespace

from bitacora import diagnostics, i18n
from bitacora.desktop import READ_LIMIT, DesktopService
from bitacora.model import MAX_QUARANTINE, Event, Run, Session
from bitacora.worker import MESSAGES, TAIL, Packer


def empty_homes(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("BITACORA_HOME", str(tmp_path / "home"))  # the user's config.toml never applies here


def test_diagnostics_are_local_json_without_session_data(tmp_path, monkeypatch):
    empty_homes(tmp_path, monkeypatch)
    data = diagnostics.collect(passes=1)
    text = json.dumps(data, allow_nan=False)

    assert data["version"] and data["python"] and data["passes"] == 1
    assert {provider["name"] for provider in data["providers"]} == {"codex", "claude", "opencode"}
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


def test_diagnostics_serialize_support_values_and_provider_health(tmp_path, monkeypatch):
    empty_homes(tmp_path, monkeypatch)
    data = diagnostics.collect(passes=1)
    capabilities = {provider["name"]: provider["capabilities"] for provider in data["providers"]}

    for name in diagnostics.SUPPORT_FIELDS:
        for provider in ("codex", "claude"):
            assert capabilities[provider][name] in {"full", "partial", "none"}
    assert capabilities["codex"]["titles"] == "full" and capabilities["codex"]["quota"] == "full"
    assert capabilities["claude"]["titles"] == "none" and capabilities["claude"]["quota"] == "partial"
    assert capabilities["claude"]["subagents"] == "none"

    empty_health = {"state": "unavailable", "readers": 0, "records": 0, "parsed": 0,
                    "discarded": 0, "unknown": 0, "details": [], "last_good": None}
    assert data["health"] == {"codex": empty_health, "claude": empty_health, "opencode": empty_health}
    assert str(tmp_path) not in json.dumps(data, allow_nan=False)


def test_diagnostics_report_tier_and_versions_for_every_provider(tmp_path, monkeypatch):
    empty_homes(tmp_path, monkeypatch)
    data = diagnostics.collect(passes=1)
    tiers = {provider["name"]: (provider["tier"], provider["verified_versions"]) for provider in data["providers"]}

    assert tiers == {"codex": ("verified", []), "claude": ("verified", []), "opencode": ("best-effort", ["2.2.4"])}
    assert data["disabled"] == []


def test_diagnostics_leave_out_providers_switched_off_in_config(tmp_path, monkeypatch):
    empty_homes(tmp_path, monkeypatch)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "config.toml").write_text("[providers]\nopencode = false\n", encoding="utf-8")
    data = diagnostics.collect(passes=1)
    text = json.dumps(data, allow_nan=False)

    assert [provider["name"] for provider in data["providers"]] == ["codex", "claude"]
    assert set(data["health"]) == {"codex", "claude"}
    assert data["disabled"] == ["opencode"]
    assert "opencode" not in data["scan"]["by_provider"]
    assert str(tmp_path) not in text


def test_diagnostics_health_degrades_without_exporting_paths_or_content(tmp_path, monkeypatch):
    archived = tmp_path / "codex" / "archived_sessions"
    archived.mkdir(parents=True)
    (archived / "rollout-2026-01-01T00-00-00-synthetic.jsonl").write_text(
        '{"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta", '
        '"payload": {"id": "synthetic-id", "cwd": "/secret/project"}}\nnot json\n',
        encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    data = diagnostics.collect(passes=1)
    health = data["health"]["codex"]

    assert health["state"] == "degraded"
    assert (health["readers"], health["records"], health["parsed"], health["discarded"]) == (1, 2, 1, 1)
    assert health["details"] == ["discarded"]  # a code, never a path or an exception message
    assert isinstance(health["last_good"], float)
    assert data["quarantine"]["discarded"] == 1
    text = json.dumps(data, allow_nan=False)
    assert str(tmp_path) not in text
    assert "/secret/project" not in text and "synthetic-id" not in text


def test_health_summary_is_json_safe_with_details_and_last_good():
    from bitacora.sources import ProviderHealth

    summary = diagnostics._health_summary({
        "alpha": ProviderHealth("degraded", readers=2, records=3, parsed=2, discarded=1,
                                details=("discarded", "missing"), last_good=12.5),
        "beta": ProviderHealth("unavailable"),
    })

    assert summary["alpha"] == {"state": "degraded", "readers": 2, "records": 3, "parsed": 2,
                                "discarded": 1, "unknown": 0,
                                "details": ["discarded", "missing"], "last_good": 12.5}
    assert summary["beta"]["details"] == [] and summary["beta"]["last_good"] is None
    assert json.loads(json.dumps(summary, allow_nan=False)) == summary


def test_opencode_health_is_listed_without_paths_or_session_ids(tmp_path, monkeypatch):
    from test_opencode import build_db

    empty_homes(tmp_path, monkeypatch)
    db = build_db(tmp_path / "opencode.db")
    monkeypatch.setenv("OPENCODE_DB", str(db))
    data = diagnostics.collect(passes=1)
    text = json.dumps(data, allow_nan=False)

    opencode = next(provider for provider in data["providers"] if provider["name"] == "opencode")
    assert opencode["available"] is True
    assert data["health"]["opencode"]["state"] == "degraded"
    assert data["health"]["opencode"]["details"] == ["discarded"]
    assert data["health"]["opencode"]["discarded"] == 1
    assert data["scan"]["by_provider"]["opencode"] == 2
    assert str(tmp_path) not in text and "ses_alpha" not in text and "ses_beta" not in text
