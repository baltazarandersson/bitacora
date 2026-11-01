"""OpenCode's SQLite store: read-only, two session tables only, runs for root sessions, degraded on bad rows."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

from provider_kit import assert_provider_contract

from bitacora import demo, sources
from bitacora.collector import Collector
from bitacora.model import Run
from bitacora.procs import Proc
from bitacora.providers import opencode
from bitacora.providers.base import Support
from bitacora.providers.claude import ClaudeProvider
from bitacora.providers.opencode import OpenCodeProvider, OpenCodeSession
from bitacora.runtime import prepare
from bitacora.store import Memory

FIXTURES = Path(__file__).parent / "fixtures" / "opencode"
SESSION_TABLES = {"session_v2", "session_message"}
SCHEMA_TABLES = {"sqlite_master", "sqlite_schema", "sqlite_temp_master"}
NOW_MS = 1_800_000_000_000


def build_db(path: Path, *, wal: bool = True, drop_seq: bool = False, scenarios: bool = False) -> Path:
    """A synthetic OpenCode database from the fixture SQL, closed so that no sidecar files remain.

    `scenarios` adds one root session per mapping rule (scenarios.sql); the default is the slice-C dataset.
    """
    conn = sqlite3.connect(path)
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript((FIXTURES / "schema.sql").read_text(encoding="utf-8"))
        conn.executescript((FIXTURES / "seed.sql").read_text(encoding="utf-8"))
        if scenarios:
            conn.executescript((FIXTURES / "scenarios.sql").read_text(encoding="utf-8"))
        if drop_seq:
            conn.execute("ALTER TABLE session_message DROP COLUMN seq")
        conn.commit()
    finally:
        conn.close()
    return path


def collector_for(tmp_path: Path, hours: float = 24) -> Collector:
    return Collector([OpenCodeProvider()], Memory(tmp_path / "sessions.json"), hours=hours, scan_procs=False)


def reader_for(provider: OpenCodeProvider, session_id: str):
    ref = next(ref for ref in provider.discover(0) if ref.locator[1] == session_id)
    reader = provider.open(ref)
    return reader


def test_provider_passes_the_contract_on_a_seeded_database(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db")))
    assert_provider_contract(OpenCodeProvider(), tmp_path)


def test_provider_passes_the_contract_on_the_scenario_database(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db", scenarios=True)))
    assert_provider_contract(OpenCodeProvider(), tmp_path)


def test_root_sessions_are_runs_and_children_archived_and_old_ones_are_not(tmp_path, monkeypatch):
    db = build_db(tmp_path / "opencode.db")
    monkeypatch.setenv("OPENCODE_DB", str(db))
    runs = collector_for(tmp_path).collect()

    assert sorted(run.session.session_id for run in runs) == ["ses_alpha", "ses_beta"]
    for run in runs:
        assert run.key.startswith("opencode:") and str(db) not in run.key
        assert run.session.path is None
        assert run.session.usage_basis == "reported"
    alpha = next(run.session for run in runs if run.session.session_id == "ses_alpha")
    assert alpha.cwd == "/work/demo-app" and alpha.title == "Refactor the demo app"
    assert alpha.model == "model-alpha"
    assert alpha.tokens == 1000 + 200 + 50 + 300 + 40
    assert alpha.start and alpha.last_ts and alpha.last_ts > alpha.start
    beta = next(run.session for run in runs if run.session.session_id == "ses_beta")
    assert beta.title == "" and beta.model is None and beta.tokens == 30


def test_forks_are_listed_as_roots(tmp_path, monkeypatch):
    db = build_db(tmp_path / "opencode.db")
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO session_v2 (id, parent_id, fork_session_id, directory, version, time_created, "
                     "time_updated) VALUES ('ses_fork', NULL, 'ses_alpha', '/work/demo-app', '2.2.4', "
                     "CAST(strftime('%s', 'now') AS INTEGER) * 1000, CAST(strftime('%s', 'now') AS INTEGER) * 1000)")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("OPENCODE_DB", str(db))
    ids = sorted(run.session.session_id for run in collector_for(tmp_path).collect())
    assert ids == ["ses_alpha", "ses_beta", "ses_fork"]


def test_same_session_id_in_two_databases_gives_two_runs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    folder = data / "opencode"
    folder.mkdir(parents=True)
    build_db(folder / "opencode.db")
    build_db(folder / "opencode-beta.db")
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))

    runs = collector_for(tmp_path).collect()
    assert len(runs) == 4
    alpha_keys = {run.key for run in runs if run.session.session_id == "ses_alpha"}
    assert len(alpha_keys) == 2


def test_reading_leaves_the_database_bytes_and_sidecars_untouched(tmp_path, monkeypatch):
    db = build_db(tmp_path / "opencode.db")
    monkeypatch.setenv("OPENCODE_DB", str(db))
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    collector_for(tmp_path).collect()

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not Path(f"{db}-wal").exists() and not Path(f"{db}-shm").exists()


def test_reader_sees_rows_committed_while_a_writer_is_open(tmp_path, monkeypatch):
    db = build_db(tmp_path / "opencode.db")
    monkeypatch.setenv("OPENCODE_DB", str(db))
    provider = OpenCodeProvider()
    reader = reader_for(provider, "ses_alpha")
    reader.update()
    before = reader.session().records_total

    writer = sqlite3.connect(db)
    try:
        writer.execute("INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data) "
                       "VALUES ('msg_new', 'ses_alpha', 'assistant', 8, "
                       "CAST(strftime('%s', 'now') AS INTEGER) * 1000, "
                       "CAST(strftime('%s', 'now') AS INTEGER) * 1000, '{\"text\":\"Late\"}')")
        writer.commit()
        assert Path(f"{db}-wal").exists()
        assert reader.update() is True
    finally:
        writer.close()
    assert reader.session().records_total == before + 1
    assert reader.health().detail == "discarded"  # the seeded invalid row, not the new one


def test_row_rewritten_in_place_rebuilds_without_double_counting(tmp_path, monkeypatch):
    db = build_db(tmp_path / "opencode.db")
    monkeypatch.setenv("OPENCODE_DB", str(db))
    reader = reader_for(OpenCodeProvider(), "ses_alpha")
    reader.update()
    session = reader.session()
    generation, counts = session.gen, (session.records_total, session.discarded, session.unknown_records)

    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE session_message SET data = '{\"text\":\"Done again\"}', "
                     "time_updated = time_updated + 1 WHERE id = 'msg_a2'")
        conn.commit()
    finally:
        conn.close()
    assert reader.update() is True

    assert session.gen != generation
    assert (session.records_total, session.discarded, session.unknown_records) == counts
    assert session.session_id == "ses_alpha" and session.tokens == 1590


def test_invalid_json_degrades_and_unknown_types_are_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db")))
    reader = reader_for(OpenCodeProvider(), "ses_alpha")
    reader.update()

    session = reader.session()
    health = reader.health()
    assert health.state == "degraded" and health.detail == "discarded"
    assert (session.records_total, session.discarded, session.unknown_records) == (7, 1, 1)
    assert session.quarantine[-1][1] == "json"
    assert reader.checkpoint() == {"records": 7, "discarded": 1, "unknown": 1, "rows": 7}


def test_missing_seq_column_is_schema_degraded_without_reading_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db", drop_seq=True)))
    reader = reader_for(OpenCodeProvider(), "ses_alpha")
    reader.update()

    health = reader.health()
    assert health.state == "degraded" and health.detail == "schema"
    assert reader.session().records_total == 0 and reader.session().events_total == 0


def test_missing_database_is_unavailable_without_runs_or_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "absent" / "opencode.db"))
    provider = OpenCodeProvider()
    assert provider.available() is False
    collector = collector_for(tmp_path)
    assert collector.collect() == []
    assert collector.errors == {}
    assert collector.health["opencode"].state == "unavailable"


def test_memory_database_means_no_database(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", ":memory:")
    assert OpenCodeProvider().databases() == [] and OpenCodeProvider().available() is False


def test_locked_database_reports_error_and_collect_survives(tmp_path, monkeypatch):
    db = build_db(tmp_path / "opencode.db", wal=False)
    monkeypatch.setenv("OPENCODE_DB", str(db))
    provider = OpenCodeProvider()
    reader = reader_for(provider, "ses_alpha")
    reader.update()

    writer = sqlite3.connect(db, isolation_level=None)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("UPDATE session_message SET data = '{}' WHERE id = 'msg_a1'")  # creates the rollback journal
        assert Path(f"{db}-journal").exists()
        reader.update()
        health = reader.health()
        assert health.state == "error" and health.detail == "locked"

        collector = collector_for(tmp_path)
        collector.collect()
        assert collector.health["opencode"].state == "error"
    finally:
        writer.execute("ROLLBACK")
        writer.close()

    reader.update()
    assert reader.health().state == "degraded"


def test_only_the_two_session_tables_are_ever_read(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db")))
    read_tables: set[str] = set()
    opened = sources.connect_readonly

    def recording(db):
        conn = opened(db)

        def authorize(action, table, column, dbname, source):
            if action == sqlite3.SQLITE_READ:
                read_tables.add(table)
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        return conn

    monkeypatch.setattr(sources, "connect_readonly", recording)
    monkeypatch.setattr(opencode, "connect_readonly", recording)
    collector_for(tmp_path).collect()

    assert SESSION_TABLES <= read_tables
    assert read_tables <= SESSION_TABLES | SCHEMA_TABLES
    assert "credential" not in read_tables and "kv" not in read_tables


def fed(*records: tuple[str, dict], cwd: str | None = "/work/app") -> OpenCodeSession:
    """A session fed one record per (type, data) pair in order, the way the reader feeds rows."""
    session = OpenCodeSession()
    session.cwd = cwd
    for seq, (typ, data) in enumerate(records, start=1):
        session.ingest({"type": typ, "seq": seq, "ts": NOW_MS + seq * 1000, "data": data})
    return session


def tool(name: str, status: str = "completed", inp: dict | None = None, *, tid: str = "",
         metadata: dict | None = None, content: str = "") -> dict:
    return {"type": "tool", "id": tid or f"call_{name}", "name": name,
            "state": {"status": status, "input": inp or {}, "metadata": metadata or {}, "content": content}}


def test_shell_exit_codes_and_missing_exit_stay_honest():
    s = fed(("assistant", {"finish": "tool-calls", "content": [
        tool("shell", inp={"command": "ok"}, metadata={"exit": 0}, tid="a"),
        tool("shell", inp={"command": "bad"}, metadata={"exit": 2}, tid="b"),
        tool("shell", inp={"command": "silent"}, metadata={}, tid="c"),
    ]}))
    cmds = [e for e in s.events if e.kind == "cmd"]
    assert [(e.text, e.ok, e.code) for e in cmds] == [("ok", True, "0"), ("bad", False, "2"), ("silent", True, None)]
    assert s.n_cmds == 3 and s.n_fail == 1


def test_read_grep_and_glob_are_command_events_with_their_search_kinds():
    s = fed(("assistant", {"finish": "tool-calls", "content": [
        tool("read", inp={"path": "/work/app/src/a.py"}, tid="r"),
        tool("grep", inp={"pattern": "TODO", "path": "/work/app/src"}, tid="g"),
        tool("glob", inp={"pattern": "**/*.md"}, tid="l"),
    ]}))
    assert [(e.kind, e.text, e.sub) for e in s.events] == [
        ("cmd", "src/a.py", "read"), ("cmd", "TODO src", "search"), ("cmd", "**/*.md", "list_files")]


def test_edit_counts_come_from_metadata_and_write_counts_its_lines():
    s = fed(("assistant", {"finish": "tool-calls", "content": [
        tool("edit", inp={"path": "/work/app/src/a.py"}, tid="e", metadata={"files": [
            {"file": "/work/app/src/a.py", "status": "modified", "additions": 4, "deletions": 2}]}),
        tool("write", inp={"path": "/work/app/new.txt", "content": "one\ntwo\n"}, tid="w1"),
        tool("write", inp={"path": "/work/app/new.txt", "content": "three"}, tid="w2"),
    ]}))
    assert s.files["src/a.py"][0] == "update" and s.files["src/a.py"][3:5] == [4, 2]
    # The first write creates the file; the second rewrites it. OpenCode gives no diff, so minus stays 0.
    assert s.files["new.txt"][0] == "update" and s.files["new.txt"][1] == 2 and s.files["new.txt"][3:5] == [3, 0]
    assert [e.sub for e in s.events if e.kind == "file"] == ["update", "add", "update"]


def test_subagents_and_other_tools_are_tool_events_not_unknown_records():
    s = fed(("assistant", {"finish": "tool-calls", "content": [
        tool("subagent", inp={"description": "search the code", "agent": "explore"}, tid="s"),
        tool("webfetch", inp={"url": "https://example.test/docs"}, tid="f"),
        tool("websearch", inp={"query": "opencode sqlite"}, tid="q"),
        tool("skill", inp={"id": "release"}, tid="k"),
        tool("execute", inp={"code": "1+1"}, tid="x"),
        tool("custom_tool", inp={"a": 1}, tid="c", status="error", content="boom"),
    ]}))
    shown = [(e.kind, e.text, e.sub) for e in s.events]
    assert shown == [
        ("tool", "agent: search the code", "subagent"), ("tool", "https://example.test/docs", "webfetch"),
        ("tool", "opencode sqlite", "websearch"), ("tool", "release", "skill"),
        ("tool", 'execute({"code": "1+1"})', "execute"), ("err", 'custom_tool({"a": 1})', "custom_tool"),
    ]
    assert s.unknown_records == 0


def test_reasoning_is_thinking_and_text_parts_are_messages():
    s = fed(("user", {"text": "Explain"}), ("assistant", {"finish": "stop", "content": [
        {"type": "reasoning", "text": "private steps"},
        {"type": "text", "text": "Here is the answer."}]}))
    assert [e.kind for e in s.events] == ["turn", "user", "think", "msg", "turn"]
    assert [text for _, text in s.messages] == ["Explain", "Here is the answer."]
    assert s.last_agent_message == "Here is the answer."


def test_turn_state_follows_the_last_assistant_finish():
    user = ("user", {"text": "Go"})
    assert fed(user).state == "running"
    assert fed(user, ("assistant", {"finish": "tool-calls", "content": []})).state == "running"
    pending = ("assistant", {"finish": "stop", "content": [tool("shell", status="running", tid="p")]})
    assert fed(user, pending).state == "running"
    done = fed(user, ("assistant", {"finish": "stop", "content": [{"type": "text", "text": "Done"}]}))
    assert done.state == "complete" and done.last_agent_message == "Done"
    failed = fed(user, ("assistant", {"finish": "error", "content": [],
                                      "error": {"type": "server_error", "message": "Boom", "status": 500}}))
    assert failed.state == "failed" and failed.limit is None and failed.last_event.kind == "err"
    limited = fed(user, ("assistant", {"finish": "error", "content": [],
                                       "error": {"type": "usage_limit", "status": 429}}))
    assert limited.state == "limited" and limited.limit[1] == "usage_limit" and limited.limit_info is None


def test_idle_outcome_decides_the_turn_last():
    answered = (("user", {"text": "Go"}),
                ("assistant", {"finish": "stop", "content": [{"type": "text", "text": "Done"}]}))
    succeeded = fed(*answered, ("idle", {"outcome": "succeeded"}))
    assert succeeded.state == "complete"
    assert [e.sub for e in succeeded.events if e.kind == "turn"] == ["started", "complete"]  # idle adds no second one
    limited = fed(*answered, ("idle", {"outcome": "failed", "error": {"type": "rate_limit", "status": 429}}))
    assert limited.state == "limited" and limited.limit is not None
    failed = fed(*answered, ("idle", {"outcome": "failed", "error": {"type": "server_error", "status": 500}}))
    assert failed.state == "failed" and failed.limit is None
    unknown = fed(*answered, ("idle", {"outcome": None}))
    assert unknown.state == "complete"
    assert fed(("user", {"text": "Go"}), ("idle", {"outcome": "succeeded"})).state == "complete"


def test_compaction_and_short_synthetic_records_are_notes_and_known_types_are_quiet():
    s = fed(("compaction", {"status": "completed"}), ("synthetic", {"description": "Skill loaded"}),
            ("synthetic", {"description": "x" * 201}), ("system", {"text": "env"}), ("shell", {"command": "ls"}),
            ("skill", {}), ("agent-switched", {}), ("model-selected", {}), ("location-switched", {}),
            ("future-type", {}))
    assert [(e.kind, e.text) for e in s.events] == [("note", "context compacted"), ("note", "Skill loaded")]
    assert s.unknown_records == 1


def test_question_part_is_open_while_pending_and_closed_once_completed():
    asked = {"questions": [{"question": "Which branch?"}]}
    user = ("user", {"text": "Go"})
    waiting = fed(user, ("assistant", {"finish": "tool-calls", "content": [
        tool("question", status="pending", inp=asked, tid="q1")]}))
    assert waiting.open_questions == {"q1": "Which branch?"} and waiting.state == "running"
    answered = fed(user, ("assistant", {"finish": "tool-calls", "content": [
        tool("question", status="completed", inp=asked, tid="q1")]}))
    assert answered.open_questions == {}
    assert [e.kind for e in answered.events if e.kind == "ask"] == ["ask"]


def test_context_is_the_last_answer_and_the_window_stays_unknown():
    first = {"finish": "stop", "content": [], "tokens": {"input": 10, "cache": {"read": 1, "write": 1}}}
    last = {"finish": "stop", "content": [], "tokens": {"input": 300, "output": 5, "cache": {"read": 40, "write": 7}}}
    s = fed(("user", {"text": "Go"}), ("assistant", first), ("assistant", last))
    assert s.context_tokens == 347
    assert s.ctx_window is None and s.context_window_assumed is False
    assert "context_window" not in OpenCodeProvider().run_meta(s)


def test_effort_is_the_last_answers_variant_from_the_transcript():
    s = fed(("assistant", {"finish": "stop", "content": [], "model": {"variant": "deep"}}))
    assert (s.effort, s.effort_source) == ("deep", "transcript")
    s = fed(("assistant", {"finish": "stop", "content": [], "model": {"variant": "deep"}}),
            ("assistant", {"finish": "stop", "content": [], "model": {"id": "model-x"}}))
    assert (s.effort, s.effort_source) == (None, "unknown")


def test_busy_session_stays_live_for_thirty_minutes_without_a_process():
    now = time.time()
    s = fed(("assistant", {"finish": "tool-calls", "content": []}))
    s.busy = True
    s.last_ts = now - 10 * 60
    assert s.recently_active(now) is True
    s.last_ts = now - 31 * 60
    assert s.recently_active(now) is False
    s.busy = False
    s.last_ts = now - 10 * 60
    assert s.recently_active(now) is False
    s.last_ts = now - 60  # the normal rule still applies to a running session without the busy row
    assert s.recently_active(now) is True


def test_resume_is_partial_and_quotes_the_folder_for_each_shell():
    provider = OpenCodeProvider()
    assert provider.capabilities.resume is Support.PARTIAL
    s = fed(("user", {"text": "Go"}))
    s.session_id, s.cwd = "ses42", "/work/my app's"
    run = Run("key", "opencode", s, None, "label")
    assert provider.resume(run) == {"argv": ["opencode", "--session", "ses42"], "cwd": "/work/my app's"}
    assert provider.resume_cmd(run, "sh") == "cd -- '/work/my app'\"'\"'s' && opencode --session ses42"
    assert provider.resume_cmd(run, "powershell") == \
        "Set-Location -LiteralPath '/work/my app''s'; opencode --session ses42"
    s.session_id = "ses42; rm -rf /"
    assert provider.resume(run) is None
    s.session_id, s.cwd = "ses42", "/work/line\nbreak"
    assert provider.resume(run) is None
    s.cwd = None
    assert provider.resume(run) is None


def test_the_reader_passes_seq_and_millisecond_time_to_ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db", scenarios=True)))
    seen: list[dict] = []
    original = OpenCodeSession.ingest

    def recording(self, obj, size=0):
        seen.append(obj)
        return original(self, obj, size)

    monkeypatch.setattr(OpenCodeSession, "ingest", recording)
    reader_for(OpenCodeProvider(), "ses_work").update()
    user = next(obj for obj in seen if obj["type"] == "user")
    assert set(user) == {"type", "seq", "ts", "data"}
    assert user["seq"] == 1 and user["ts"] > 0 and user["data"] == {"text": "Fix the parser so the tests pass"}


def test_scenario_sessions_map_to_run_statuses(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db", scenarios=True)))
    runs = {run.session.session_id: run for run in collector_for(tmp_path).collect()}
    expected = {"ses_work": "your_turn", "ses_limited": "limited", "ses_question": "asking",
                "ses_streaming": "running", "ses_busy": "running", "ses_stale": "quiet",
                "ses_compacted": "your_turn", "ses_failed": "failed"}
    assert {sid: runs[sid].status for sid in expected} == expected


def test_running_status_comes_from_the_transcript_not_a_process(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db", scenarios=True)))
    runs = {run.session.session_id: run for run in collector_for(tmp_path).collect()}
    busy = runs["ses_busy"]
    assert busy.proc is None and busy.status == "running" and busy.status_source == "transcript"
    assert busy.live is True
    stale = runs["ses_stale"]
    assert stale.status_source is None and stale.live is False


def test_a_complete_turn_maps_totals_context_files_and_effort(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db", scenarios=True)))
    runs = {run.session.session_id: run for run in collector_for(tmp_path).collect()}
    run = runs["ses_work"]
    s = run.session
    assert s.title == "Fix the parser" and s.model == "model-beta"
    assert (s.effort, s.effort_source) == ("deep", "transcript")
    assert s.tokens == 860
    assert s.context_tokens == 465  # the last answer: 340 input + 120 read + 5 write
    assert run.context_window is None and run.context_window_assumed is False
    assert s.n_cmds == 6 and s.n_fail == 1  # three shells, read, grep, glob; one shell exited 2
    assert s.files["src/parser.py"][0] == "update" and s.files["src/parser.py"][3:5] == [4, 2]
    assert s.files["tests/test_parser.py"][0] == "add" and s.files["tests/test_parser.py"][3] == 2


def test_a_question_disappears_once_its_part_completes(tmp_path, monkeypatch):
    db = build_db(tmp_path / "opencode.db", scenarios=True)
    monkeypatch.setenv("OPENCODE_DB", str(db))
    reader = reader_for(OpenCodeProvider(), "ses_question")
    reader.update()
    assert list(reader.session().open_questions.values()) == ["Which branch should I target?"]

    completed = {"finish": "tool-calls", "content": [{"type": "tool", "id": "call_q1", "name": "question",
                 "executed": True, "state": {"status": "completed", "input": {"questions": [
                     {"question": "Which branch should I target?"}]}}}]}
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE session_message SET data = ?, time_updated = time_updated + 1 WHERE id = 'q_2'",
                     (json.dumps(completed),))
        conn.commit()
    finally:
        conn.close()
    assert reader.update() is True
    assert reader.session().open_questions == {}


def test_compaction_note_follows_the_current_language():
    from rich.console import Console

    from bitacora import i18n
    from bitacora.model import Event
    from bitacora.ui.render import render_event

    event = Event(0.0, "note", "context compacted", sub="compacted")
    console = Console(width=80, record=True)
    i18n.set_lang("es")
    try:
        console.print(render_event(event, verbose=True))
    finally:
        i18n.set_lang("en")
    assert "contexto compactado" in console.export_text()


def test_a_failed_answer_without_tokens_keeps_the_last_known_context():
    from bitacora.providers.opencode import OpenCodeSession

    s = OpenCodeSession()
    s.ingest({"type": "assistant", "seq": 1, "ts": 1_000,
              "data": {"tokens": {"input": 900, "cache": {"read": 100}}, "model": {"variant": "high"},
                       "finish": "tool-calls", "content": []}})
    s.ingest({"type": "assistant", "seq": 2, "ts": 2_000,
              "data": {"finish": "error", "error": {"type": "provider.transport"}, "content": []}})
    assert s.ctx_used == 1000 and s.effort == "high"


def test_demo_schema_has_the_fixture_columns_for_the_two_tables_the_reader_uses():
    def columns(sql: str) -> dict:
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(sql)
            return {table: [row[1:] for row in conn.execute(f"PRAGMA table_info({table})")]
                    for table in sorted(SESSION_TABLES)}
        finally:
            conn.close()

    assert columns(demo.OPENCODE_SCHEMA) == columns((FIXTURES / "schema.sql").read_text(encoding="utf-8"))


def test_demo_store_is_the_database_the_provider_reads_and_it_is_closed_cleanly(demo_env):
    db = demo_env["homes"]["opencode"]
    assert Path(os.environ["OPENCODE_DB"]) == db and db.is_file()  # conftest points OpenCode at the demo, never at ~
    assert demo_env["root"] in db.parents
    runs = Collector([OpenCodeProvider()], Memory(), hours=24, scan_procs=False).collect()
    assert len(runs) == 3  # the subagent of the finished session is not a run
    assert all(run.proc is None and run.meta["processes"] is False for run in runs)
    assert not list(db.parent.glob("opencode.db-wal"))


def test_demo_runtime_points_opencode_at_the_demo_store_only(demo_env):
    before = os.environ["OPENCODE_DB"]
    with prepare(SimpleNamespace(demo=True, lang="en", hours=None)) as runtime:
        provider = next(p for p in runtime.providers if p.name == "opencode")
        assert Path(os.environ["OPENCODE_DB"]) == runtime.demo_root / "opencode" / "opencode.db"
        assert provider.available()
        assert len(Collector([provider], Memory(), hours=24, scan_procs=False).collect()) == 3
    assert os.environ["OPENCODE_DB"] == before


def test_service_is_read_from_the_command_line_of_an_opencode_process():
    provider = OpenCodeProvider()
    serve = Proc(10, "opencode.exe", [r"C:\Program Files\OpenCode\opencode.exe", "serve", "--service"], 1.0)
    assert provider.service_running([serve]) is True
    assert provider.service_running([Proc(11, "opencode", ["opencode-cli", "serve", "--service"], 1.0)]) is True
    assert provider.service_running([Proc(12, "opencode", ["opencode", "serve"], 1.0)]) is False
    assert provider.service_running([Proc(13, "opencode", ["opencode", "--session", "x"], 1.0)]) is False
    assert provider.service_running([]) is False
    assert ClaudeProvider().service_running([serve]) is None  # the default: not applicable to the provider


def test_collector_reports_the_shared_service_for_opencode_runs_from_one_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(build_db(tmp_path / "opencode.db", scenarios=True)))
    requested, snapshot = [], [Proc(10, "opencode", ["opencode", "serve", "--service"], 1.0)]
    monkeypatch.setattr("bitacora.collector.procs.snapshot", lambda names: requested.append(names) or snapshot)
    runs = Collector([OpenCodeProvider()], Memory(), hours=24, scan_procs=True).collect()
    assert requested == [("opencode",)]  # named by the provider's process prefix, nothing else
    assert runs and all(run.meta["service"] is True for run in runs)
    snapshot.clear()
    runs = Collector([OpenCodeProvider()], Memory(), hours=24, scan_procs=True).collect()
    assert runs and all(run.meta["service"] is False for run in runs)
    runs = Collector([OpenCodeProvider()], Memory(), hours=24, scan_procs=False).collect()
    assert runs and all("service" not in run.meta for run in runs)  # no snapshot, no claim


def test_a_session_table_without_discovery_columns_is_named_not_crashed(tmp_path, monkeypatch):
    db = build_db(tmp_path / "opencode.db", wal=False)
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE session_v2 DROP COLUMN time_archived")
    monkeypatch.setenv("OPENCODE_DB", str(db))
    collector = Collector([OpenCodeProvider()], Memory(tmp_path / "m.json"), hours=24, scan_procs=False)
    assert collector.collect() == []
    assert collector.errors == {"opencode": "SchemaChanged"}
    assert collector.health["opencode"].state == "error"


def test_a_failed_turn_is_not_kept_live_by_a_busy_row():
    now = time.time()
    s = fed(("idle", {"outcome": "failed", "error": {"type": "provider.free-limit", "status": 429}}))
    s.busy, s.last_ts = True, now - 60
    assert s.state == "limited" and s.recently_active(now) is False


def test_the_demo_animator_never_recreates_a_removed_database(tmp_path):
    data = demo.generate(tmp_path, time.time())
    db = Path(data["homes"]["opencode"])
    db.unlink()
    try:
        demo._touch_opencode(db, data["live"][2][1], time.time(), 1)
    except sqlite3.Error:
        pass
    assert not db.exists()
