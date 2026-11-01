-- Synthetic rows only. Times are relative to the moment the database is built, except the one old session.
-- Two root sessions, one subagent of the first, one archived session and one older than any window.
INSERT INTO session_v2 (id, parent_id, directory, title, version, model, cost, tokens_input, tokens_output,
    tokens_reasoning, tokens_cache_read, tokens_cache_write, time_created, time_updated, time_archived, idle_outcome)
VALUES
  ('ses_alpha', NULL, '/work/demo-app', 'Refactor the demo app', '2.2.4',
   '{"id":"model-alpha","providerID":"demo-provider","variant":"fast"}', 0.12, 1000, 200, 50, 300, 40,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 600000, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 60000,
   NULL, 'succeeded'),
  ('ses_beta', NULL, '/work/other-app', NULL, '2.2.4', NULL, 0, 10, 20, 0, 0, 0,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 300000, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 30000,
   NULL, NULL),
  ('ses_child', 'ses_alpha', '/work/demo-app', 'Subagent: search', '2.2.4', NULL, 0, 5, 5, 0, 0, 0,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 500000, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 50000,
   NULL, NULL),
  ('ses_archived', NULL, '/work/archived-app', 'Archived work', '2.2.4', NULL, 0, 1, 1, 0, 0, 0,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 900000, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 1000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 1000, NULL),
  ('ses_old', NULL, '/work/old-app', 'Long ago', '2.2.4', NULL, 0, 7, 7, 0, 0, 0, 1000, 1000, NULL, NULL);

-- Messages of several types, one row with invalid JSON and one with a type the reader does not know.
INSERT INTO session_message (id, session_id, type, seq, time_created, time_updated, data)
VALUES
  ('msg_a1', 'ses_alpha', 'user', 1, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 600000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 600000, '{"text":"Please refactor the demo"}'),
  ('msg_a2', 'ses_alpha', 'assistant', 2, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 590000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 590000, '{"text":"Done"}'),
  ('msg_a3', 'ses_alpha', 'system', 3, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 580000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 580000, '{"text":"Context"}'),
  ('msg_a4', 'ses_alpha', 'idle', 4, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 570000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 570000, '{}'),
  ('msg_a5', 'ses_alpha', 'compaction', 5, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 560000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 560000, '{}'),
  ('msg_a6', 'ses_alpha', 'future-type', 6, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 550000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 550000, '{}'),
  ('msg_a7', 'ses_alpha', 'assistant', 7, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 540000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 540000, '{"text": '),
  ('msg_b1', 'ses_beta', 'user', 1, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 300000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 300000, '{"text":"Hello"}'),
  ('msg_c1', 'ses_child', 'user', 1, CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 500000,
   CAST(strftime('%s', 'now') AS INTEGER) * 1000 - 500000, '{"text":"Search the code"}');

INSERT INTO credential (id, secret) VALUES ('cred_1', 'synthetic-secret-value');
INSERT INTO kv (key, value) VALUES ('theme', 'synthetic');
