-- Scenario sessions for the OpenCode provider tests, loaded on top of seed.sql: one per mapping rule.
-- Synthetic rows only, with the same offset-from-build-time convention.
-- Scenario sessions, one per mapping case. The clock is taken once; each row is an offset in milliseconds from it.
-- Message data carries no time fields, so only the row times matter here.
CREATE TEMP TABLE clock AS SELECT CAST(strftime('%s', 'now') AS INTEGER) * 1000 AS at_ms;

INSERT INTO session_v2 (id, parent_id, directory, title, version, model, cost, tokens_input, tokens_output,
    tokens_reasoning, tokens_cache_read, tokens_cache_write, time_created, time_updated, time_busy, time_idle,
    time_archived, idle_outcome)
VALUES
  -- A complete turn: tool calls (shell exit 0, shell exit 2, shell without exit, read, grep, glob, edit, write), stop, idle.
  ('ses_work', NULL, '/work/demo-app', 'Fix the parser', '2.2.4',
   '{"id":"model-beta","providerID":"demo-provider","variant":"deep"}', 0.31, 500, 120, 30, 200, 10,
   (SELECT at_ms FROM clock) - 400000, (SELECT at_ms FROM clock) - 100000,
   (SELECT at_ms FROM clock) - 380000, (SELECT at_ms FROM clock) - 100000, NULL, 'succeeded'),
  -- A turn that ends with a 429 from idle.
  ('ses_limited', NULL, '/work/logs-app', 'Summarize the logs', '2.2.4',
   '{"id":"model-gamma","providerID":"demo-provider"}', 0.04, 300, 40, 0, 0, 0,
   (SELECT at_ms FROM clock) - 300000, (SELECT at_ms FROM clock) - 280000,
   (SELECT at_ms FROM clock) - 295000, (SELECT at_ms FROM clock) - 280000, NULL, 'failed'),
  -- A pending question tool: the turn waits for an answer.
  ('ses_question', NULL, '/work/demo-app', 'Pick the release name', '2.2.4', NULL, 0, 90, 10, 0, 0, 0,
   (SELECT at_ms FROM clock) - 250000, (SELECT at_ms FROM clock) - 240000, NULL, NULL, NULL, NULL),
  -- A streaming assistant record: no finish and no completion time.
  ('ses_streaming', NULL, '/work/notes', 'Draft the changelog', '2.2.4', NULL, 0, 100, 10, 0, 0, 0,
   (SELECT at_ms FROM clock) - 60000, (SELECT at_ms FROM clock) - 40000, NULL, NULL, NULL, NULL),
  -- Busy with a long shell call: the row was last written 10 minutes ago.
  ('ses_busy', NULL, '/work/build-app', 'Run the long build', '2.2.4', NULL, 0, 50, 5, 0, 0, 0,
   (SELECT at_ms FROM clock) - 900000, (SELECT at_ms FROM clock) - 600000,
   (SELECT at_ms FROM clock) - 800000, NULL, NULL, NULL),
  -- Busy but last written 31 minutes ago: no longer assumed running.
  ('ses_stale', NULL, '/work/stale-app', 'Old build', '2.2.4', NULL, 0, 50, 5, 0, 0, 0,
   (SELECT at_ms FROM clock) - 1900000, (SELECT at_ms FROM clock) - 1860000,
   (SELECT at_ms FROM clock) - 1900000, NULL, NULL, NULL),
  -- Compaction, a synthetic record with a short description, and records that are known but not shown.
  ('ses_compacted', NULL, '/work/long-app', 'Long session', '2.2.4', NULL, 0, 4000, 300, 0, 0, 0,
   (SELECT at_ms FROM clock) - 500000, (SELECT at_ms FROM clock) - 120000, NULL,
   (SELECT at_ms FROM clock) - 120000, NULL, 'succeeded'),
  -- An assistant record that finished with an error.
  ('ses_failed', NULL, '/work/api-app', 'Call the API', '2.2.4', NULL, 0, 20, 0, 0, 0, 0,
   (SELECT at_ms FROM clock) - 150000, (SELECT at_ms FROM clock) - 140000, NULL, NULL, NULL, NULL);

INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data)
VALUES
  ('w_1', 'ses_work', 'user', 1, (SELECT at_ms FROM clock) - 400000, (SELECT at_ms FROM clock) - 400000,
   '{"text":"Fix the parser so the tests pass"}'),
  ('w_2', 'ses_work', 'assistant', 2, (SELECT at_ms FROM clock) - 390000, (SELECT at_ms FROM clock) - 390000,
   '{"agent":"build","model":{"id":"model-beta","providerID":"demo-provider","variant":"deep"},"finish":"tool-calls","tokens":{"input":200,"output":60,"reasoning":10,"cache":{"read":80,"write":5}},"content":[{"type":"reasoning","text":"The parser splits on commas only."},{"type":"tool","id":"call_01","name":"shell","executed":true,"state":{"status":"completed","input":{"command":"npm test","workdir":"/work/demo-app"},"metadata":{"exit":0},"content":"12 passed"}},{"type":"tool","id":"call_02","name":"shell","executed":true,"state":{"status":"completed","input":{"command":"pytest -q tests/test_parser.py"},"metadata":{"exit":2},"content":"1 failed"}},{"type":"tool","id":"call_03","name":"shell","executed":true,"state":{"status":"completed","input":{"command":"git status --short"},"metadata":{},"content":" M src/parser.py"}},{"type":"tool","id":"call_04","name":"read","executed":true,"state":{"status":"completed","input":{"path":"/work/demo-app/src/parser.py"},"metadata":{},"content":"def split(line):"}},{"type":"tool","id":"call_05","name":"grep","executed":true,"state":{"status":"completed","input":{"pattern":"split","path":"/work/demo-app/src"},"metadata":{},"content":"parser.py:1"}},{"type":"tool","id":"call_06","name":"glob","executed":true,"state":{"status":"completed","input":{"pattern":"**/*.py"},"metadata":{},"content":"src/parser.py"}},{"type":"tool","id":"call_07","name":"edit","executed":true,"state":{"status":"completed","input":{"path":"/work/demo-app/src/parser.py"},"metadata":{"files":[{"file":"/work/demo-app/src/parser.py","status":"modified","additions":4,"deletions":2}]},"content":"Edit applied"}}]}'),
  ('w_3', 'ses_work', 'assistant', 3, (SELECT at_ms FROM clock) - 330000, (SELECT at_ms FROM clock) - 330000,
   '{"agent":"build","model":{"id":"model-beta","providerID":"demo-provider","variant":"deep"},"finish":"tool-calls","tokens":{"input":320,"output":40,"reasoning":0,"cache":{"read":110,"write":5}},"content":[{"type":"tool","id":"call_08","name":"write","executed":true,"state":{"status":"completed","input":{"path":"/work/demo-app/tests/test_parser.py","content":"def test_split():\n    assert True\n"},"metadata":{},"content":"Wrote file"}}]}'),
  ('w_4', 'ses_work', 'assistant', 4, (SELECT at_ms FROM clock) - 200000, (SELECT at_ms FROM clock) - 200000,
   '{"agent":"build","model":{"id":"model-beta","providerID":"demo-provider","variant":"deep"},"finish":"stop","tokens":{"input":340,"output":50,"reasoning":0,"cache":{"read":120,"write":5}},"content":[{"type":"text","text":"The parser now splits quoted commas and the tests pass."}]}'),
  ('w_5', 'ses_work', 'idle', 5, (SELECT at_ms FROM clock) - 100000, (SELECT at_ms FROM clock) - 100000,
   '{"outcome":"succeeded"}'),
  ('l_1', 'ses_limited', 'user', 1, (SELECT at_ms FROM clock) - 300000, (SELECT at_ms FROM clock) - 300000,
   '{"text":"Summarize the logs"}'),
  ('l_2', 'ses_limited', 'assistant', 2, (SELECT at_ms FROM clock) - 290000, (SELECT at_ms FROM clock) - 290000,
   '{"finish":"stop","tokens":{"input":150,"output":30,"reasoning":0,"cache":{"read":0,"write":0}},"content":[{"type":"text","text":"Two errors and one retry in the logs."}]}'),
  ('l_3', 'ses_limited', 'idle', 3, (SELECT at_ms FROM clock) - 280000, (SELECT at_ms FROM clock) - 280000,
   '{"outcome":"failed","error":{"type":"rate_limit","status":429}}'),
  ('q_1', 'ses_question', 'user', 1, (SELECT at_ms FROM clock) - 250000, (SELECT at_ms FROM clock) - 250000,
   '{"text":"Pick a release name"}'),
  ('q_2', 'ses_question', 'assistant', 2, (SELECT at_ms FROM clock) - 240000, (SELECT at_ms FROM clock) - 240000,
   '{"finish":"tool-calls","tokens":{"input":90,"output":10,"reasoning":0,"cache":{"read":0,"write":0}},"content":[{"type":"tool","id":"call_q1","name":"question","executed":true,"state":{"status":"pending","input":{"questions":[{"question":"Which branch should I target?"}]}}}]}'),
  ('s_1', 'ses_streaming', 'user', 1, (SELECT at_ms FROM clock) - 60000, (SELECT at_ms FROM clock) - 60000,
   '{"text":"Draft the changelog entries"}'),
  ('s_2', 'ses_streaming', 'assistant', 2, (SELECT at_ms FROM clock) - 50000, (SELECT at_ms FROM clock) - 40000,
   '{"model":{"id":"model-delta","providerID":"demo-provider"},"tokens":{"input":100,"output":10,"reasoning":0,"cache":{"read":0,"write":0}},"content":[{"type":"text","text":"Drafting the entries"}]}'),
  ('b_1', 'ses_busy', 'user', 1, (SELECT at_ms FROM clock) - 800000, (SELECT at_ms FROM clock) - 800000,
   '{"text":"Run the long build"}'),
  ('b_2', 'ses_busy', 'assistant', 2, (SELECT at_ms FROM clock) - 700000, (SELECT at_ms FROM clock) - 600000,
   '{"finish":"tool-calls","tokens":{"input":50,"output":5,"reasoning":0,"cache":{"read":0,"write":0}},"content":[{"type":"tool","id":"call_b1","name":"shell","executed":true,"state":{"status":"running","input":{"command":"make all"},"metadata":{}}}]}'),
  ('x_1', 'ses_stale', 'user', 1, (SELECT at_ms FROM clock) - 1900000, (SELECT at_ms FROM clock) - 1900000,
   '{"text":"Start the old build"}'),
  ('x_2', 'ses_stale', 'assistant', 2, (SELECT at_ms FROM clock) - 1870000, (SELECT at_ms FROM clock) - 1860000,
   '{"finish":"tool-calls","tokens":{"input":50,"output":5,"reasoning":0,"cache":{"read":0,"write":0}},"content":[{"type":"tool","id":"call_x1","name":"shell","executed":true,"state":{"status":"running","input":{"command":"make old"},"metadata":{}}}]}'),
  ('c_1', 'ses_compacted', 'user', 1, (SELECT at_ms FROM clock) - 500000, (SELECT at_ms FROM clock) - 500000,
   '{"text":"Keep going with the migration"}'),
  ('c_2', 'ses_compacted', 'compaction', 2, (SELECT at_ms FROM clock) - 400000, (SELECT at_ms FROM clock) - 400000,
   '{"status":"completed","reason":"overflow","tokens":{"input":90000,"output":2000}}'),
  ('c_3', 'ses_compacted', 'synthetic', 3, (SELECT at_ms FROM clock) - 390000, (SELECT at_ms FROM clock) - 390000,
   '{"description":"Skill loaded: migrations"}'),
  ('c_4', 'ses_compacted', 'system', 4, (SELECT at_ms FROM clock) - 380000, (SELECT at_ms FROM clock) - 380000,
   '{"text":"Environment"}'),
  ('c_5', 'ses_compacted', 'shell', 5, (SELECT at_ms FROM clock) - 370000, (SELECT at_ms FROM clock) - 370000,
   '{"command":"ls"}'),
  ('c_6', 'ses_compacted', 'assistant', 6, (SELECT at_ms FROM clock) - 200000, (SELECT at_ms FROM clock) - 200000,
   '{"finish":"stop","tokens":{"input":4000,"output":300,"reasoning":0,"cache":{"read":0,"write":0}},"content":[{"type":"text","text":"Migration continues with the next table."}]}'),
  ('c_7', 'ses_compacted', 'idle', 7, (SELECT at_ms FROM clock) - 120000, (SELECT at_ms FROM clock) - 120000,
   '{"outcome":"succeeded"}'),
  ('f_1', 'ses_failed', 'user', 1, (SELECT at_ms FROM clock) - 150000, (SELECT at_ms FROM clock) - 150000,
   '{"text":"Call the API"}'),
  ('f_2', 'ses_failed', 'assistant', 2, (SELECT at_ms FROM clock) - 140000, (SELECT at_ms FROM clock) - 140000,
   '{"finish":"error","error":{"type":"server_error","message":"Upstream model failed","status":500},"content":[]}');

