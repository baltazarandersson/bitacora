"""Provider sources: stable session references, readers and health.

File and SQLite readers are instantiated today. The contract stays source-kind agnostic so directory and
composite readers can be added without touching the collector. Reference keys are opaque digests and never
carry a locator. File run keys keep the historical `provider:session_id` / `provider:path` shape; non-file
run keys use `ref.key`; diagnostics expose no locators.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from bitacora.model import Session

READER_STATES = ("verified", "ok", "degraded", "unavailable", "error")
HEALTH_RANK = {"verified": 0, "ok": 1, "degraded": 2, "unavailable": 3, "error": 4}


def locator_text(locator: object) -> str:
    """A normalized string for hashing. It is never exposed to users or diagnostics."""
    try:
        if isinstance(locator, os.PathLike):
            return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(locator))))
    except (TypeError, ValueError):
        pass
    return str(locator)


def ref_key(provider: str, locator: object) -> str:
    """Stable, provider-scoped and opaque: a digest of the normalized locator, never the locator itself."""
    digest = hashlib.sha256(locator_text(locator).encode("utf-8", "surrogatepass")).hexdigest()
    return f"{provider}:{digest[:16]}"


@dataclass(frozen=True)
class SessionRef:
    provider: str
    key: str  # opaque digest; never a locator (non-file run keys use it)
    locator: object  # path, directory, DB row key or composite key
    kind: str = "file"  # file | dir | sqlite | composite


@dataclass(frozen=True)
class ReaderHealth:
    state: str  # verified | ok | degraded | unavailable | error
    records: int = 0
    parsed: int = 0
    discarded: int = 0
    unknown: int = 0
    last_good: float | None = None
    detail: str = ""  # short machine-readable code, never an exception message or a path


@runtime_checkable
class SessionReader(Protocol):
    ref: SessionRef

    def update(self) -> bool: ...

    def session(self) -> Session: ...

    def checkpoint(self) -> dict: ...

    def health(self) -> ReaderHealth: ...


@dataclass(frozen=True)
class ProviderHealth:
    state: str
    readers: int = 0
    records: int = 0
    parsed: int = 0
    discarded: int = 0
    unknown: int = 0
    details: tuple[str, ...] = ()  # sorted unique reader detail codes, never paths or messages
    last_good: float | None = None  # newest successful read across readers


class FileReader:
    """One existing `Session` behind the reader contract; no parsing lives here."""

    def __init__(self, ref: SessionRef, session: Session) -> None:
        self.ref = ref
        self._session = session
        self._last_good: float | None = None
        self._error: str | None = None
        session.locator = ref.locator

    def update(self) -> bool:
        changed = self._session.update()
        if self._session.read_error is not None:
            self._error = self._session.read_error
        elif self._present():
            self._error = None
            self._last_good = time.time()
        return changed

    def session(self) -> Session:
        return self._session

    def checkpoint(self) -> dict:
        session = self._session
        return {"offset": int(session.offset), "records": int(session.records_total),
                "discarded": int(session.discarded), "unknown": int(session.unknown_records)}

    def health(self) -> ReaderHealth:
        session = self._session
        if not self._present():
            state, detail = "unavailable", "missing"
        elif self._error is not None:
            state, detail = "error", "read_error"
        elif session.discarded:
            state, detail = "degraded", "discarded"
        else:
            state, detail = "ok", ""
        return ReaderHealth(state=state, records=session.records_total,
                            parsed=max(0, session.records_total - session.discarded),
                            discarded=session.discarded, unknown=session.unknown_records,
                            last_good=self._last_good, detail=detail)

    def _present(self) -> bool:
        try:
            return Path(self.ref.locator).exists()
        except (OSError, TypeError):
            return False


def connect_readonly(db: Path) -> sqlite3.Connection:
    """Open a database for reading without writing into its folder.

    Opening a WAL database read-only creates `-wal` and `-shm` when they are missing, so a database with no `-wal`
    is opened immutable. A `-journal` means a rollback-mode writer is active: that case keeps normal locking, so
    the read waits for the lock or reports it instead of reading a half-written file.
    """
    path = Path(os.path.abspath(db))
    immutable = not (Path(f"{path}-wal").exists() or Path(f"{path}-journal").exists())
    query = "mode=ro&immutable=1" if immutable else "mode=ro"
    conn = sqlite3.connect(f"{path.as_uri()}?{query}", uri=True, timeout=0.5)
    try:
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


class SqliteReader:
    """A read-only SQLite source behind the reader contract, for providers whose sessions live in tables.

    A locator is `(database path, row key)`. A subclass names its tables: `header_table` has one row per session
    keyed by `header_key`; `stream_table` holds the session's rows, each with `seq`, `time_created`, `time_updated`,
    `type` and JSON `data`, keyed by `stream_key`. The base owns connections, the column guard, incremental ingest,
    quarantine and health. A subclass maps the header row in `fill_header`. Each stream row reaches `Session.ingest`
    as `{"type", "seq", "ts", "data"}`, where `ts` is the stored `time_created` (epoch milliseconds for OpenCode).
    """

    kind = "sqlite"
    header_table = ""
    header_key = "id"
    stream_table = ""
    stream_key = "session_id"
    required: dict[str, tuple[str, ...]] = {}  # table -> columns read; checked before any query

    def __init__(self, ref: SessionRef, session: Session) -> None:
        self.ref = ref
        self._session = session
        self._db = Path(ref.locator[0])
        self._row_key = ref.locator[1]
        self._last_good: float | None = None
        self._error: str | None = None  # "locked" | "read_error": codes only, never the exception text
        self._schema_ok = True
        self._gone = False  # the session row is no longer in the database
        self._seen: dict[object, object] = {}  # seq -> time_updated of every row already ingested
        session.locator = ref.locator

    def fill_header(self, row: dict) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def update(self) -> bool:
        self._error = None
        if not self._present():
            return False
        try:
            with closing(connect_readonly(self._db)) as conn:
                conn.execute("BEGIN")  # one snapshot for the header and the rows
                try:
                    self._schema_ok = self._guard(conn)
                    if not self._schema_ok:
                        return False
                    changed = self._sync(conn)
                finally:
                    conn.rollback()
        except sqlite3.Error as exc:
            text = str(exc).lower()
            self._error = "locked" if "locked" in text or "busy" in text else "read_error"
            return False
        self._last_good = time.time()
        return changed

    def session(self) -> Session:
        return self._session

    def checkpoint(self) -> dict:
        s = self._session
        return {"records": int(s.records_total), "discarded": int(s.discarded), "unknown": int(s.unknown_records),
                "rows": len(self._seen)}

    def health(self) -> ReaderHealth:
        s = self._session
        if not self._present() or self._gone:
            state, detail = "unavailable", "missing"
        elif self._error is not None:
            state, detail = "error", self._error
        elif s.discarded:
            state, detail = "degraded", "discarded"
        elif not self._schema_ok:
            state, detail = "degraded", "schema"
        else:
            state, detail = "ok", ""
        return ReaderHealth(state=state, records=s.records_total, parsed=max(0, s.records_total - s.discarded),
                            discarded=s.discarded, unknown=s.unknown_records, last_good=self._last_good,
                            detail=detail)

    def _present(self) -> bool:
        try:
            return self._db.exists()
        except OSError:
            return False

    def _guard(self, conn: sqlite3.Connection) -> bool:
        """Every required column must exist before its table is queried; a missing one reads nothing."""
        for table, columns in self.required.items():
            present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not present.issuperset(columns):
                return False
        return True

    def _sync(self, conn: sqlite3.Connection) -> bool:
        s = self._session
        columns = self.required[self.header_table]
        row = conn.execute(f"SELECT {', '.join(columns)} FROM {self.header_table} WHERE {self.header_key} = ?",
                           (self._row_key,)).fetchone()
        if row is None:
            self._gone = True
            return False
        self._gone = False
        header = dict(zip(columns, row, strict=True))
        before = (s.session_id, s.title, s.cwd, s.model, s.tokens, s.start, s.last_ts)
        current = dict(conn.execute(f"SELECT seq, time_updated FROM {self.stream_table} "
                                    f"WHERE {self.stream_key} = ?", (self._row_key,)).fetchall())
        changed = False
        if any(seq not in current or current[seq] != stamp for seq, stamp in self._seen.items()):
            # A row vanished or was rewritten in place: rebuild the whole stream in order.
            s.reset()
            self._seen = {}
            changed = True
            wanted = sorted(current)
        else:
            wanted = sorted(seq for seq in current if seq not in self._seen)
        # After any reset, which clears the header fields, and before the rows, which need the folder and title.
        self.fill_header(header)
        if wanted:
            new = set(wanted)
            rows = conn.execute(f"SELECT seq, time_updated, type, time_created, data FROM {self.stream_table} "
                                f"WHERE {self.stream_key} = ? AND seq >= ? ORDER BY seq",
                                (self._row_key, wanted[0])).fetchall()
            for seq, stamp, row_type, created, raw in rows:
                if seq in new:
                    self._ingest_row(seq, row_type, created, raw)
                    self._seen[seq] = stamp
            changed = True
        return changed or before != (s.session_id, s.title, s.cwd, s.model, s.tokens, s.start, s.last_ts)

    def _ingest_row(self, seq: object, row_type: object, created: object, raw: object) -> None:
        """One stored row: valid JSON reaches the session; anything else is quarantined like a bad JSONL line."""
        s = self._session
        size = len(raw) if isinstance(raw, (str, bytes)) else 0
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            s.records_total += 1
            s.discarded += 1
            s.quarantine.append((s.records_total, "json", type(exc).__name__, size))
            return
        s.ingest({"type": row_type, "seq": seq, "ts": created, "data": value}, size)
