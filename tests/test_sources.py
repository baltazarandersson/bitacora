"""The provider-reader contract: opaque refs, file readers, checkpoints and health."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from bitacora.model import Event, ProcInfo, Session
from bitacora.providers.base import Capabilities, Provider, Support
from bitacora.sources import FileReader, SessionRef, ref_key


class ToySession(Session):
    provider = "toy"

    def handle(self, o):
        if "text" in o:
            self.messages.append((None, o["text"]))
            self.add(Event(None, "note", "", o["text"]))
        elif o.get("kind") == "future":
            self.note_unknown()


class StubProvider(Provider):
    name = "stub"
    label = "Stub"
    session_cls = ToySession
    capabilities = Capabilities(
        processes=True, history=True, resume=Support.FULL, messages=Support.FULL, commands=Support.FULL,
        files=Support.FULL, questions=Support.FULL, usage=Support.FULL, context=Support.FULL,
        quota=Support.FULL, titles=Support.NONE, subagents=Support.NONE,
    )

    def __init__(self, paths=()):
        self.paths = list(paths)

    def home(self) -> Path:
        return Path(".")

    def available(self) -> bool:
        return True

    def history(self, since):
        return iter(self.paths)


def write_lines(path: Path, lines) -> None:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def toy_ref(path: Path, provider: str = "toy") -> SessionRef:
    return SessionRef(provider, ref_key(provider, path), path)


def test_ref_key_is_stable_opaque_and_provider_scoped(tmp_path):
    path = tmp_path / "session.jsonl"
    key = ref_key("toy", path)
    assert key == ref_key("toy", Path(str(path)))
    assert key.startswith("toy:") and len(key.split(":", 1)[1]) == 16
    assert str(path) not in key and path.name not in key
    assert key != ref_key("other", path)
    assert key != ref_key("toy", tmp_path / "other.jsonl")


def test_provider_defaults_discover_open_and_match(tmp_path):
    path = tmp_path / "toy.jsonl"
    write_lines(path, [{"text": "hello"}])
    provider = StubProvider([path])

    refs = list(provider.discover(0))
    assert refs == [provider.ref_for(path)]
    assert refs[0].kind == "file" and refs[0].locator == path

    reader = provider.open(refs[0])
    assert isinstance(reader, FileReader) and reader.ref == refs[0]
    reader.update()
    assert reader.session().locator == path

    pi = ProcInfo("stub", 1, 1.0, ["stub"])
    assert provider.match_ref(pi, set()) is None  # no match() override

    class Matching(StubProvider):
        def match(self, pi, claimed, peers=()):
            return path

    matched = Matching([path]).match_ref(pi, set())
    assert matched == provider.ref_for(path)


def test_file_reader_is_idempotent_and_checkpoint_is_json_compatible(tmp_path):
    path = tmp_path / "toy.jsonl"
    write_lines(path, [{"text": "hello"}, {"text": "world"}])
    reader = FileReader(toy_ref(path), ToySession(path))

    assert reader.update() is True
    session = reader.session()
    offset = session.offset
    assert session.events_total == 2
    assert session.messages == [(None, "hello"), (None, "world")]

    # An ordinary poll with no rewrite reads nothing new.
    assert reader.update() is False
    assert session.events_total == 2
    assert session.messages == [(None, "hello"), (None, "world")]
    assert session.offset == offset

    checkpoint = reader.checkpoint()
    assert checkpoint == {"offset": offset, "records": 2, "discarded": 0, "unknown": 0}
    assert json.loads(json.dumps(checkpoint)) == checkpoint
    assert str(path) not in json.dumps(checkpoint)

    health = reader.health()
    assert health.state == "ok" and health.detail == ""
    assert (health.records, health.parsed, health.discarded, health.unknown) == (2, 2, 0, 0)
    assert health.records == session.events_total
    assert health.last_good and health.last_good > 0

    # A reset stream is re-read from the start without duplicating normalized data.
    session.reset()
    assert session.locator == path  # a reset stream keeps the locator
    assert reader.update() is True
    assert session.events_total == 2
    assert session.messages == [(None, "hello"), (None, "world")]
    assert session.offset == offset

    checkpoint = reader.checkpoint()
    assert checkpoint == {"offset": offset, "records": 2, "discarded": 0, "unknown": 0}
    assert checkpoint["records"] == session.events_total
    assert json.loads(json.dumps(checkpoint)) == checkpoint
    assert str(path) not in json.dumps(checkpoint)
    assert reader.health().state == "ok"


def test_discarded_records_degrade_health_and_unknown_types_are_counted(tmp_path):
    path = tmp_path / "toy.jsonl"
    path.write_text('{"text": "hello"}\n{"kind": "future"}\nnot json\n', encoding="utf-8")
    reader = FileReader(toy_ref(path), ToySession(path))
    reader.update()

    health = reader.health()
    assert health.state == "degraded" and health.detail == "discarded"
    assert (health.records, health.parsed, health.discarded, health.unknown) == (3, 2, 1, 1)
    assert reader.checkpoint()["unknown"] == 1


def test_missing_file_is_unavailable_and_last_good_survives(tmp_path):
    path = tmp_path / "toy.jsonl"
    write_lines(path, [{"text": "hello"}])
    reader = FileReader(toy_ref(path), ToySession(path))
    reader.update()
    good = reader.health().last_good
    assert good and reader.health().state == "ok"

    path.unlink()
    reader.update()
    health = reader.health()
    assert health.state == "unavailable" and health.detail == "missing"
    assert health.last_good == good

    write_lines(path, [{"text": "again"}])
    reader.update()
    assert reader.health().state == "ok"
    assert reader.session().read_error is None


def test_read_error_is_reported_without_a_path_and_recovers(tmp_path):
    target = tmp_path / "session.jsonl"
    target.mkdir()  # opening a directory fails with an OSError on every supported platform
    reader = FileReader(toy_ref(target), ToySession(target))
    reader.update()

    health = reader.health()
    assert health.state == "error" and health.detail == "read_error"
    assert str(target) not in health.detail
    assert reader.session().read_error is not None

    target.rmdir()
    write_lines(target, [{"text": "recovered"}])
    reader.update()
    assert reader.health().state == "ok"
    assert reader.session().read_error is None


FULL_CAPABILITIES = {
    "processes": True, "history": True, "resume": Support.FULL, "messages": Support.FULL,
    "commands": Support.FULL, "files": Support.FULL, "questions": Support.FULL, "usage": Support.FULL,
    "context": Support.FULL, "quota": Support.FULL, "titles": Support.NONE, "subagents": Support.NONE,
}


def test_capabilities_require_every_field():
    with pytest.raises(TypeError):
        Capabilities()
    assert len(FULL_CAPABILITIES) == 12
    caps = Capabilities(**FULL_CAPABILITIES)
    for name in FULL_CAPABILITIES:
        partial = {key: value for key, value in FULL_CAPABILITIES.items() if key != name}
        with pytest.raises(TypeError):
            Capabilities(**partial)
    assert caps.processes and caps.history


def test_capabilities_normalize_bools_and_strings_to_support():
    caps = Capabilities(**FULL_CAPABILITIES)
    assert all(getattr(caps, name) is Support.FULL
               for name in ("resume", "messages", "commands", "files", "questions", "usage", "context", "quota"))
    assert caps.titles is Support.NONE and caps.subagents is Support.NONE

    legacy = replace(caps, processes=False, history=False, resume=False, quota=False, titles=True)
    assert legacy.resume is Support.NONE and not legacy.resume
    assert legacy.quota is Support.NONE and not legacy.quota
    assert legacy.titles is Support.FULL and legacy.titles
    assert not legacy.processes and not legacy.history

    assert Support.PARTIAL and not Support.NONE
    assert replace(caps, messages="partial").messages is Support.PARTIAL

    with pytest.raises(TypeError):
        replace(caps, processes="yes")
    with pytest.raises(TypeError):
        replace(caps, messages=1)


def test_connect_readonly_leaves_no_sidecars_and_refuses_writes(tmp_path):
    import sqlite3

    from bitacora.sources import connect_readonly

    db = tmp_path / "wal.db"
    seed = sqlite3.connect(db)
    seed.execute("PRAGMA journal_mode=WAL")
    seed.execute("CREATE TABLE items (value TEXT)")
    seed.execute("INSERT INTO items VALUES ('kept')")
    seed.commit()
    seed.close()

    conn = connect_readonly(db)
    try:
        assert conn.execute("SELECT value FROM items").fetchall() == [("kept",)]
        with pytest.raises(sqlite3.Error):
            conn.execute("INSERT INTO items VALUES ('new')")
    finally:
        conn.close()
    assert not (tmp_path / "wal.db-wal").exists() and not (tmp_path / "wal.db-shm").exists()


def test_connect_readonly_uses_locking_while_a_rollback_journal_exists(tmp_path):
    import sqlite3

    from bitacora.sources import connect_readonly

    db = tmp_path / "rollback.db"
    seed = sqlite3.connect(db)
    seed.execute("CREATE TABLE items (value TEXT)")
    seed.commit()
    seed.close()

    writer = sqlite3.connect(db, isolation_level=None)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("INSERT INTO items VALUES ('pending')")
        with pytest.raises(sqlite3.OperationalError) as caught:
            connect_readonly(db).execute("SELECT value FROM items").fetchall()
        assert "locked" in str(caught.value).lower()
    finally:
        writer.execute("ROLLBACK")
        writer.close()
