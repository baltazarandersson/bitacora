-- Synthetic OpenCode-shaped schema: the two tables the reader uses, plus decoys it must never query.
CREATE TABLE session_v2 (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    fork_session_id TEXT,
    directory TEXT NOT NULL,
    title TEXT,
    version TEXT NOT NULL,
    agent TEXT,
    model TEXT,
    cost REAL NOT NULL DEFAULT 0,
    tokens_input INTEGER NOT NULL DEFAULT 0,
    tokens_output INTEGER NOT NULL DEFAULT 0,
    tokens_reasoning INTEGER NOT NULL DEFAULT 0,
    tokens_cache_read INTEGER NOT NULL DEFAULT 0,
    tokens_cache_write INTEGER NOT NULL DEFAULT 0,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    time_busy INTEGER,
    time_idle INTEGER,
    time_archived INTEGER,
    idle_outcome TEXT
);

CREATE TABLE session_message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    type TEXT NOT NULL,
    seq INTEGER NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);

-- Decoys: any read of these tables is a bug.
CREATE TABLE credential (id TEXT PRIMARY KEY, secret TEXT NOT NULL);
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);
