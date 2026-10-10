"""Synthetic Codex and Claude Code sessions, written relative to "now".

Used by `bitacora --demo`, the tests and the README screenshots. Nothing here touches real data.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

CODEX_MODEL = "gpt-6-luna"
CODEX_SOL_MODEL = "gpt-6.1-sol"
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_SONNET_MODEL = "claude-sonnet-5-5"
CLAUDE_HAIKU_MODEL = "claude-haiku-5-5"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def clock(ts: float) -> str:
    """Local wall-clock time the way a limit message words it, e.g. "4:10 AM"."""
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def demo_cwd(name: str) -> str:
    return f"/home/dev/{name}"


# ----------------------------------------------------------------------------- codex

class CodexWriter:
    def __init__(self, home: Path, sid: str, start: float, cwd: str, source: str = "exec",
                 originator: str = "codex_exec", effort: str = "high", sandbox: str = "workspace-write",
                 model: str = CODEX_MODEL):
        day = datetime.fromtimestamp(start)
        folder = home / "sessions" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"rollout-{day:%Y-%m-%dT%H-%M-%S}-{sid}.jsonl"
        self.path.write_text("", encoding="utf-8")
        self.cwd = cwd
        self.t = start
        self.rec("session_meta", {"id": sid, "timestamp": iso(start), "cwd": cwd, "source": source,
                                  "originator": originator})
        self.rec("turn_context", {"model": model, "effort": effort, "sandbox_policy": {"type": sandbox}})
        self.tokens = 0

    def rec(self, typ: str, payload: dict, dt: float = 0.0) -> None:
        self.t += dt
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"timestamp": iso(self.t), "type": typ, "payload": payload}) + "\n")

    def ev(self, payload: dict, dt: float = 0.0) -> None:
        self.rec("event_msg", payload, dt)

    def item(self, item: dict, dt: float = 0.0) -> None:
        self.ev({"type": "item_completed", "item": item}, dt)

    def prompt(self, text: str, dt: float = 0.0) -> None:
        self.ev({"type": "task_started"}, dt)
        self.item({"type": "UserMessage", "content": [{"type": "text", "text": text}]}, 0.2)

    def think(self, dt: float, summary: str = "") -> None:
        self.item({"type": "Reasoning", "summary_text": [summary] if summary else []}, dt)

    def cmd(self, cmd: str, out: str = "", code: int = 0, secs: float = 0.4, kind: str = "", dt: float = 1.0) -> None:
        parsed = [{"type": kind, "cmd": cmd}] if kind else []
        self.item({"type": "CommandExecution", "command": ["bash", "-lc", cmd], "parsed_cmd": parsed,
                   "exit_code": code, "duration": {"secs": int(secs), "nanos": int((secs % 1) * 1e9)},
                   "aggregated_output": out}, dt)

    def edit(self, rel: str, diff: str, kind: str = "update", dt: float = 1.0) -> None:
        sep = "\\" if "\\" in self.cwd else "/"
        change = {"type": kind}
        if kind == "add":
            change["content"] = diff
        else:
            change["unified_diff"] = diff
        self.item({"type": "FileChange", "changes": {self.cwd + sep + rel.replace("/", sep): change}}, dt)

    def say(self, text: str, dt: float = 1.0) -> None:
        self.item({"type": "AgentMessage", "content": [{"type": "text", "text": text}]}, dt)

    def usage(self, add: int, ctx: int, dt: float = 0.1, rate: dict | None = None) -> None:
        self.tokens += add
        payload = {"type": "token_count", "info": {"total_token_usage": {"total_tokens": self.tokens},
                                                   "last_token_usage": {"input_tokens": ctx},
                                                   "model_context_window": 272_000}}
        if rate:
            payload["rate_limits"] = rate
        self.ev(payload, dt)

    def type_limit(self, rate_type: str, dt: float = 0.1) -> None:
        self.ev({"type": "token_count", "info": None,
                 "rate_limits": {"rate_limit_reached_type": rate_type}}, dt)

    def ask(self, question: str, dt: float = 1.0) -> None:
        self.rec("response_item", {"type": "function_call", "name": "request_user_input", "call_id": "q1",
                                   "arguments": json.dumps({"questions": [{"question": question}]})}, dt)

    def error(self, message: str, dt: float = 1.0) -> None:
        self.ev({"type": "error", "message": message}, dt)

    def complete(self, message: str, dt: float = 1.0) -> None:
        self.say(message, dt)
        self.ev({"type": "task_complete", "last_agent_message": message}, 0.2)

    def abort(self, reason: str = "interrupted", dt: float = 1.0) -> None:
        self.ev({"type": "turn_aborted", "reason": reason}, dt)


# ----------------------------------------------------------------------------- claude

class ClaudeWriter:
    def __init__(self, home: Path, sid: str, start: float, cwd: str, entrypoint: str = "sdk-cli",
                 title: str = "", model: str = CLAUDE_MODEL, ctx: int = 40_000):
        folder = home / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", cwd)
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{sid}.jsonl"
        self.path.write_text("", encoding="utf-8")
        self.sid, self.cwd, self.entry, self.t = sid, cwd, entrypoint, start
        # ctx is the prompt-cache read per reply: the stand-in for how much context the session carries.
        self.model, self.ctx = model, ctx
        self.n = 0
        if title:
            self.line({"type": "custom-title", "customTitle": title, "sessionId": sid})

    def line(self, obj: dict) -> None:
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(obj) + "\n")

    def _base(self, typ: str, dt: float) -> dict:
        self.t += dt
        self.n += 1
        return {"type": typ, "timestamp": iso(self.t), "sessionId": self.sid, "cwd": self.cwd,
                "entrypoint": self.entry, "uuid": f"u{self.n}", "isSidechain": False}

    def user(self, content, dt: float = 0.0) -> None:
        o = self._base("user", dt)
        o["message"] = {"role": "user", "content": content}
        self.line(o)

    def assistant(self, block: dict, stop: str | None = "tool_use", dt: float = 1.0, out_tokens: int = 300,
                  ctx: int | None = None) -> None:
        o = self._base("assistant", dt)
        o["message"] = {"id": f"msg_{self.n}", "role": "assistant", "model": self.model, "content": [block],
                        "stop_reason": stop, "usage": {"input_tokens": 4, "cache_read_input_tokens": ctx or self.ctx,
                                                       "cache_creation_input_tokens": 600,
                                                       "output_tokens": out_tokens}}
        self.line(o)

    def think(self, dt: float) -> None:
        self.assistant({"type": "thinking", "thinking": "", "signature": "x"}, dt=dt)

    def tool(self, name: str, inp: dict, result: str = "", ok: bool = True, dt: float = 1.0,
             secs: float = 1.0, extra: dict | None = None) -> None:
        tid = f"toolu_{self.n}"
        self.assistant({"type": "tool_use", "id": tid, "name": name, "input": inp}, dt=dt)
        o = self._base("user", secs)
        o["message"] = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                      "content": result, "is_error": not ok}]}
        if extra:
            o["toolUseResult"] = extra
        self.line(o)

    def say(self, text: str, end: bool = False, dt: float = 1.0) -> None:
        self.assistant({"type": "text", "text": text}, stop="end_turn" if end else None, dt=dt)

    def rate_limit(self, rate_type: str, resets_at: int, model: str = CLAUDE_SONNET_MODEL,
                   text: str = "API Error: 429") -> None:
        """A synthetic structured limit record, including the model-specific window Claude reports."""
        o = self._base("assistant", 0.1)
        o.update({"isApiErrorMessage": True, "error": "rate_limit", "apiErrorStatus": 429,
                  "quotaLimits": {"status": "rejected", "resetsAt": resets_at, "rateLimitType": rate_type},
                  "message": {"model": model, "content": [{"type": "text", "text": text}]}})
        self.line(o)

    def api_error(self, code: str, status: int, text: str) -> None:
        o = self._base("assistant", 0.1)
        o.update({"isApiErrorMessage": True, "error": code, "apiErrorStatus": status,
                  "message": {"id": f"msg_{self.n}", "role": "assistant", "model": self.model,
                              "type": "message", "content": [{"type": "text", "text": text}],
                              "stop_reason": "synthetic_error", "stop_sequence": None,
                              "container": None, "context_management": None, "usage": {}}})
        self.line(o)


# ----------------------------------------------------------------------------- scenario content

RATE = {"primary": {"used_percent": 38.0, "window_minutes": 300},
        "secondary": {"used_percent": 84.0, "window_minutes": 10080}}

BACKOFF_SRC = """import 'dart:math';

final _rng = Random();

/// Delay before attempt [attempt] (0-based): 1 s, 2 s, 4 s and so on, capped at 5 min, with +/-20% jitter.
Duration backoffDelay(int attempt, {Random? random}) {
  final base = min(300, 1 << attempt);
  final jitter = (random ?? _rng).nextDouble() * 0.4 - 0.2;
  return Duration(milliseconds: (base * 1000 * (1 + jitter)).round());
}
"""

SYNC_DIFF = ("@@ -56,3 +56,7 @@\n"
             "     final attempts = item.attempts + 1;\n"
             "-    retryAt = now.add(const Duration(seconds: 30));\n"
             "+    retryAt = now.add(backoffDelay(attempts));\n"
             "+    if (_isPermanent(status)) {\n"
             "+      await _drop(item);\n"
             "+      return;\n"
             "+    }\n"
             "     await _store.put(item);\n")

SYNC_RESULT = """# Offline sync retries

- Failed sends wait 1 s, 2 s, 4 s and so on, with ±20% jitter, capped at 5 minutes (`lib/sync/backoff.dart`).
- 4xx responses are dropped at once, except 408 and 429, which are still retried.
- `legacy_retry.dart` is removed. The queue file format did not change, so offline data written before still loads.

`flutter test test/sync` passes (11 tests) and `flutter analyze` reports no issues.
"""

DEDUPE_SRC = '''"""Backfill dedupe_key for one day of events, in batches. Safe to re-run."""
import hashlib

from etl.db import session_scope
from etl.models import Event

BATCH = 5000


def dedupe_day(day, dry_run=False):
    changed = 0
    with session_scope() as session:
        rows = session.query(Event).filter(Event.day == day).all()
        for row in rows:
            key = hashlib.sha256(row.payload).hexdigest()
            if row.dedupe_key != key:
                row.dedupe_key = key
                changed += 1
        if not dry_run:
            session.commit()
    return changed
'''

MIGRATION_SQL = """BEGIN;
CREATE TABLE order_lines (
  order_id bigint REFERENCES orders(id),
  line_no integer NOT NULL,
  sku text NOT NULL,
  cents integer NOT NULL,
  PRIMARY KEY (order_id, line_no)
);
INSERT INTO order_lines (order_id, line_no, sku, cents)
SELECT id, 1, 'legacy', subtotal_cents FROM orders;
COMMIT;
"""

SUMMARY_MD = """# 2.3 at a glance

## CLI
- `--compat` keeps the 2.1 flag names working.
- `sync --dry-run` is about three times faster on large queues.
- Exit codes are listed in the reference.

## Sync
- Sync resumes after the laptop sleeps.
- Failed sends retry with backoff.
- Conflicts ask before anything is overwritten.

## Web
- New checkout summary with item thumbnails.
- Coupons can be applied from the keyboard.
- The bundle is 40% smaller.

## Billing
- Annual plans with monthly invoices.
- Upgrades are prorated to the day.
- Invoices download as PDF.
"""


def _name_thread(home: Path, sid: str, name: str, now: float) -> None:
    with open(home / "session_index.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"id": sid, "thread_name": name, "updated_at": iso(now)}) + "\n")


# ----------------------------------------------------------------------------- scenes

def _codex_scenes(home: Path, runs: Path, now: float, state: dict) -> tuple[CodexWriter, CodexWriter]:
    """Background exec runs and interactive threads. Returns the live writer and the one waiting for an answer."""
    rate = {k: dict(v, resets_at=int(now + (2 * 3600 if k == "primary" else 3 * 86400))) for k, v in RATE.items()}
    api, web, infra = demo_cwd("shop-api"), demo_cwd("shop-web"), demo_cwd("infra")
    app, etl, docs = demo_cwd("mobile-app"), demo_cwd("etl-pipeline"), demo_cwd("docs-site")

    # 1. A background run that is working right now.
    sid = "0199a1b2-0000-7000-8000-000000000001"
    w = CodexWriter(home, sid, now - 7 * 60, api)
    w.prompt("Task: move session auth to signed cookies. Keep the public API unchanged and add tests.")
    w.think(6)
    w.cmd("rg -n \"session_token\" src", "src/auth/session.py:12\nsrc/auth/middleware.py:40", kind="search", dt=2)
    w.cmd("sed -n 1,80p src/auth/session.py", "...", kind="read")
    w.usage(48_000, 41_000, rate=rate)
    w.think(14)
    w.edit("src/auth/session.py",
           "@@ -10,7 +10,9 @@\n-    token = request.headers.get('X-Session')\n+    raw = request.cookies.get(COOKIE)\n"
           "+    token = unsign(raw) if raw else None\n+    if token is None:\n+        return None", dt=3)
    w.edit("tests/test_session_cookies.py", "def test_signed_cookie_roundtrip():\n    ...\n", kind="add")
    w.cmd("pytest -q tests/test_session_cookies.py", "F.\nFAILED test_expired_cookie - assert 401 == 200",
          code=1, secs=3.2)
    w.think(9)
    w.edit("src/auth/session.py", "@@ -31,3 +31,5 @@\n+    if expired(token):\n+        raise Unauthorized()", dt=2)
    w.cmd("pytest -q tests/test_session_cookies.py", "..\n2 passed in 1.84s", secs=2.4)
    w.usage(61_000, 58_500, rate=rate)
    w.t = now - 25
    w.say("Cookie signing is in place and the new tests pass. Now checking the middleware for the old header.")
    w.t = now - 4
    w.think(0)
    (runs / "auth-cookies.log").write_text(
        "exec codex --model gpt-6-luna -s workspace-write\n$ rg -n session_token src\n"
        "$ pytest -q tests/test_session_cookies.py\nFAILED test_expired_cookie\n$ pytest -q\n2 passed\n",
        encoding="utf-8")
    state[f"codex:{sid}"] = {"label": "auth-cookies", "out": str(runs / "auth-cookies.result.md"),
                             "log": str(runs / "auth-cookies.log")}
    live = w

    # 2. A background run waiting for an answer.
    sid = "0199a1b2-0000-7000-8000-000000000002"
    w = CodexWriter(home, sid, now - 12 * 60, infra, sandbox="read-only")
    w.prompt("Plan the Postgres 17 upgrade for the staging cluster.")
    w.think(20)
    w.cmd("cat terraform/db.tf", "engine_version = \"15.6\"", kind="read")
    w.usage(30_000, 28_000)
    w.ask("Staging has 2 replicas. Upgrade in place, or blue/green with a new cluster?", dt=30)
    w.t = now - 50
    w.ev({"type": "token_count", "info": None})
    waiting = w
    state[f"codex:{sid}"] = {"label": "pg17-plan", "out": None, "log": None}

    # 3. A finished background run with its result file.
    sid = "0199a1b2-0000-7000-8000-000000000003"
    w = CodexWriter(home, sid, now - 95 * 60, web, sandbox="read-only")
    w.prompt("Read-only audit: list accessibility issues on the checkout flow.")
    w.think(30)
    w.cmd("rg -n \"<img\" src/checkout", "src/checkout/Summary.tsx:22", kind="search")
    w.cmd("rg -n \"onClick\" src/checkout", "4 matches", kind="search")
    w.usage(210_000, 96_000)
    result = "# Checkout a11y audit\n\n1. `Summary.tsx`: product images have no alt text.\n" \
             "2. The coupon toggle is a `div` with onClick and no keyboard handler.\n" \
             "3. Error messages are not announced (missing `aria-live`).\n"
    w.complete(result, dt=240)
    (runs / "a11y-audit.result.md").write_text(result, encoding="utf-8")
    state[f"codex:{sid}"] = {"label": "a11y-audit", "out": str(runs / "a11y-audit.result.md"), "log": None}

    # 4. A background run that was cut short by hand.
    sid = "0199a1b2-0000-7000-8000-000000000004"
    w = CodexWriter(home, sid, now - 5 * 3600, api)
    w.prompt("Find why test_orders is flaky on CI.")
    w.think(12)
    w.cmd("pytest tests/test_orders.py -x --count 20", "19 passed, 1 failed", code=1, secs=41)
    w.usage(14_000, 12_000, rate={"primary": {"used_percent": 84.0, "window_minutes": 300,
                                                "resets_at": int(now - 60)}})
    w.abort("interrupted", dt=60)
    state[f"codex:{sid}"] = {"label": "flaky-orders", "out": None, "log": None}

    # 5. An interactive Codex thread from the desktop app.
    sid = "0199a1b2-0000-7000-8000-000000000005"
    w = CodexWriter(home, sid, now - 3 * 3600, web, source="vscode", originator="Codex Desktop",
                    effort="medium")
    w.prompt("Rewrite the landing hero copy, shorter and less salesy.")
    w.think(8)
    w.usage(9_000, 8_500)
    w.complete("Here are three options for the hero, from most to least direct…", dt=20)
    _name_thread(home, sid, "Landing hero copy", now)

    # 6. A background run on the mobile app: max effort, a failing test fixed, context nearly full.
    sid = "0199a1b2-0000-7000-8000-000000000006"
    w = CodexWriter(home, sid, now - 200 * 60, app, model=CODEX_SOL_MODEL, effort="max")
    w.prompt("Task: add exponential backoff with jitter to the offline sync queue. Stop retrying 4xx responses "
             "except 408 and 429. Keep the queue file format.")
    w.think(12)
    w.cmd("rg -n \"retry|attempt\" lib test",
          "lib/sync/queue.dart:58:      retryAt = now.add(const Duration(seconds: 30));\n"
          "lib/sync/queue.dart:71:      if (attempts > 5) drop();\n"
          "test/sync/queue_test.dart:34:  test('retries 500 with backoff', ...)", kind="search", dt=2)
    w.cmd("sed -n 1,90p lib/sync/queue.dart", "class SyncQueue {\n  final Store _store;\n  ...", kind="read")
    w.usage(70_000, 64_000)
    w.think(22)
    w.edit("lib/sync/backoff.dart", BACKOFF_SRC, kind="add", dt=3)
    w.edit("lib/sync/queue.dart", SYNC_DIFF, dt=2)
    w.edit("lib/sync/queue.dart", "@@ -71,1 +75,1 @@\n-      if (attempts > 5) drop();\n"
           "+      if (attempts >= _maxAttempts) drop();", dt=1)
    w.edit("lib/sync/legacy_retry.dart", "@@ -1,4 +0,0 @@\n-/// Fixed 30 s retry, replaced by backoff.dart.\n"
           "-class LegacyRetry {\n-  static const interval = Duration(seconds: 30);\n-}\n", kind="delete", dt=2)
    w.cmd("flutter test test/sync", "00:06 +10 -1: Some tests failed.\n\n"
          "  test/sync/queue_test.dart: retries 500 with backoff\n"
          "  Expected: <0:00:30.000000>\n  Actual: <0:00:01.000000>", code=1, secs=14.0, dt=2)
    w.think(30)
    w.edit("test/sync/queue_test.dart", "@@ -34,1 +34,1 @@\n-    expect(delay, const Duration(seconds: 30));\n"
           "+    expect(delay.inMilliseconds, inInclusiveRange(800, 1200));", dt=2)
    w.cmd("flutter test test/sync", "00:07 +11: All tests passed!", secs=12.0, dt=2)
    w.cmd("dart format --output=none --set-exit-if-changed lib test", "Changed lib/sync/backoff.dart",
          code=1, secs=1.2)
    w.cmd("dart format lib test", "Formatted 14 files (1 changed) in 0.8 seconds.", secs=0.9)
    w.cmd("flutter analyze", "Analyzing mobile-app...\nNo issues found! (ran in 6.2s)", secs=6.2)
    w.usage(180_000, 251_000)
    w.complete(SYNC_RESULT, dt=3)
    (runs / "offline-sync-retry.result.md").write_text(SYNC_RESULT, encoding="utf-8")
    state[f"codex:{sid}"] = {"label": "offline-sync-retry", "out": str(runs / "offline-sync-retry.result.md"),
                             "log": None}

    # 7. A background run ended with a usage-limit error in task_complete.
    sid = "0199a1b2-0000-7000-8000-000000000007"
    w = CodexWriter(home, sid, now - 17 * 3600 - 5 * 60, etl, effort="low")
    w.prompt("Task: backfill dedupe_key for events from the last 90 days, one day at a time. Dry-run first, "
             "and stop if a day has conflicts.")
    w.think(10)
    w.cmd("rg -n \"dedupe_key\" etl", "etl/models/event.py:14: dedupe_key = Column(String(64), index=True)\n"
          "etl/jobs/ingest.py:88: dedupe_key=sha256(payload)", kind="search", dt=2)
    w.cmd("cat etl/jobs/backfill.py", "...", kind="read")
    w.edit("etl/backfill/dedupe_day.py", DEDUPE_SRC, kind="add", dt=4)
    w.cmd("python -m etl.backfill.dedupe_day 2026-07-01 --dry-run",
          "2026-07-01: 48211 rows, 1904 would change, 0 conflicts", secs=3.1, dt=2)
    w.think(15)
    w.cmd("python -m etl.backfill.dedupe_day 2026-07-01", "2026-07-01: 1904 rows updated in 3.4s", secs=3.4, dt=2)
    w.edit("etl/backfill/dedupe_day.py", "@@ -12,3 +12,3 @@\n     with session_scope() as session:\n"
           "-        rows = session.query(Event).filter(Event.day == day).all()\n"
           "+        rows = session.query(Event).filter(Event.day == day).yield_per(BATCH)\n"
           "         for row in rows:\n", dt=3)
    w.cmd("pytest etl/tests/test_backfill.py -q", "..F\n\nFAILED etl/tests/test_backfill.py::"
          "test_dedupe_is_idempotent - AssertionError: 1904 != 0\n1 failed, 2 passed in 2.11s",
          code=1, secs=2.1, dt=2)
    w.think(20)
    w.edit("etl/backfill/dedupe_day.py", "@@ -14,1 +14,3 @@\n         for row in rows:\n"
           "+            if row.dedupe_key is not None:\n+                continue  # already done on an earlier run\n",
           dt=2)
    w.cmd("pytest etl/tests/test_backfill.py -q", "...\n3 passed in 2.04s", secs=2.0, dt=2)
    w.usage(90_000, 150_000)
    w.think(6)
    reset = now + 3.5 * 3600
    w.usage(18_000, 180_000, rate={"limit_id": "codex",
                                   "primary": {"used_percent": 100.0, "window_minutes": 300,
                                               "resets_at": int(reset)},
                                   "secondary": {"used_percent": 58.0, "window_minutes": 10080,
                                                 "resets_at": int(now + 3 * 86400)},
                                   "rate_limit_reached_type": "primary"}, dt=2)
    limit_message = f"You've hit your usage limit. Try again at {clock(reset)}."
    w.ev({"type": "task_complete", "last_agent_message": None, "turn_id": "synthetic-limit-turn",
          "time_to_first_token_ms": 18,
          "error": {"codex_error_info": "usage_limit", "message": limit_message}}, dt=1.5)
    w.type_limit("primary")
    (runs / "backfill-dedupe.log").write_text(
        "exec codex --model gpt-6-luna -c model_reasoning_effort=\"low\" -s workspace-write\n"
        "$ rg -n dedupe_key etl\n$ python -m etl.backfill.dedupe_day 2026-07-01 --dry-run\n"
        "$ python -m etl.backfill.dedupe_day 2026-07-01\n$ pytest etl/tests/test_backfill.py -q\n3 passed\n"
        f"error: usage limit reached, try again at {clock(reset)}\n", encoding="utf-8")
    state[f"codex:{sid}"] = {"label": "backfill-dedupe", "out": None,
                             "log": str(runs / "backfill-dedupe.log")}

    # 9. An open question with no new transcript record for more than two minutes.
    sid = "0199a1b2-0000-7000-8000-000000000009"
    w = CodexWriter(home, sid, now - 5 * 60, infra, source="vscode", originator="Codex Desktop")
    w.prompt("Choose the migration strategy for the staging database.")
    w.t = now - 3 * 60
    w.ask("Keep the existing cluster or cut over to a replacement?")

    # 10. A session file exists before its first turn record.
    CodexWriter(home, "0199a1b2-0000-7000-8000-000000000010", now - 4 * 60, docs)
    _name_thread(home, "0199a1b2-0000-7000-8000-000000000010", "Draft the API reference", now - 4 * 60)

    # 8. An interactive terminal thread on the docs site, finished.
    sid = "0199a1b2-0000-7000-8000-000000000008"
    w = CodexWriter(home, sid, now - 9 * 3600, docs, source="cli", originator="codex-tui", effort="high")
    w.prompt("Check every link in the docs for 404s and fix the ones that moved.")
    w.think(9)
    w.cmd("lychee --no-progress docs", "[404] https://docs.example.test/install/old-setup\n"
          "[404] https://docs.example.test/cli/#legacy-flag\n2 errors in 214 links", code=2, secs=7.8, dt=2)
    w.edit("docs/install.md", "@@ -12,1 +12,1 @@\n-See [the old setup guide](https://docs.example.test/install/old-setup).\n"
           "+See [the setup guide](https://docs.example.test/install/setup).", dt=3)
    w.edit("docs/cli/flags.md", "@@ -40,1 +40,1 @@\n-Legacy behaviour: see [--legacy-flag](/cli/#legacy-flag).\n"
           "+Legacy behaviour is gone: `--compat` replaces `--legacy-flag` since 2.0.", dt=2)
    w.usage(40_000, 52_000)
    w.say("Two links had moved: the old setup guide and an anchor on the flags page. Both now point at live content.",
          dt=2)
    w.cmd("lychee --no-progress docs", "0 errors in 214 links", secs=7.1, dt=2)
    w.complete("Fixed the two broken links in docs/install.md and docs/cli/flags.md. All 214 links resolve.", dt=2)
    _name_thread(home, sid, "Fix dead links in the docs", now)
    return live, waiting


def _claude_scenes(home: Path, now: float) -> ClaudeWriter:
    """Print runs and interactive sessions. Returns the live print run."""
    api, web, infra = demo_cwd("shop-api"), demo_cwd("shop-web"), demo_cwd("infra")
    app, etl, docs = demo_cwd("mobile-app"), demo_cwd("etl-pipeline"), demo_cwd("docs-site")

    # 1. A Claude Code run in print mode, working right now.
    c = ClaudeWriter(home, "5f0c2a1e-0000-4000-8000-000000000006", now - 4 * 60, web)
    c.user("Update CHANGELOG.md for the 2.4.0 release from the merged PRs since 2.3.0.")
    c.think(4)
    c.tool("Bash", {"command": "git log --oneline v2.3.0..HEAD"}, "a1f3 Add dark mode\n9c2e Fix cart rounding")
    c.tool("Read", {"file_path": web + "/CHANGELOG.md"}, "# Changelog")
    c.think(11)
    c.tool("Edit", {"file_path": web + "/CHANGELOG.md",
                    "old_string": "# Changelog\n\n## 2.3.0\n- Previous release\n",
                    "new_string": "# Changelog\n\n## 2.4.0\n- Dark mode\n- Cart rounding fix\n"})
    c.tool("Write", {"file_path": web + "/CHANGELOG.md",
                     "content": "# Changelog\n\n## 2.4.0\n- Dark mode\n- Cart rounding fix\n"},
           "File updated successfully.")
    c.t = now - 8
    c.say("Added the 2.4.0 section. Checking that every PR title is user facing.")
    live = c

    # 2. A desktop session whose tool call has been quiet for five minutes.
    c = ClaudeWriter(home, "5f0c2a1e-0000-4000-8000-000000000007", now - 5 * 60 - 10, api,
                     entrypoint="claude-desktop", title="Review PR 42: rate limiter")
    c.user("Review PR 42, focus on the rate limiter edge cases.")
    c.think(4)
    c.assistant({"type": "tool_use", "id": "toolu_quiet", "name": "Bash",
                 "input": {"command": "gh pr diff 42"}}, dt=1)

    # 3. An older terminal session. The weekly Sonnet limit was hit after it had finished.
    c = ClaudeWriter(home, "5f0c2a1e-0000-4000-8000-000000000008", now - 7 * 3600, infra,
                     entrypoint="cli", title="Fix login redirect loop")
    c.user("Users get a redirect loop after login on Safari.")
    c.think(15)
    c.tool("Read", {"file_path": infra + "/nginx.conf"}, "...")
    c.tool("Bash", {"command": "curl -I https://staging.example.test/login"}, "HTTP/2 302", ok=True)
    c.say("The cookie is set with SameSite=Strict on a cross-site redirect. Switched it to Lax.", end=True)
    # Different model pools recover independently; this synthetic Sonnet limit remains despite Opus success above.
    c.t = now - 2 * 3600 - 10
    c.rate_limit("seven_day_sonnet", int(now + 5 * 86400))

    # 4. A terminal Claude API error after an earlier answer completed.
    c = ClaudeWriter(home, "5f0c2a1e-0000-4000-8000-000000000009", now - 16.5 * 3600, etl,
                     entrypoint="cli", title="Split orders into order lines", model="claude-sonnet-4-5")
    c.user("Split the order table into order lines.")
    c.say("I found the current schema and migration plan.", end=True)
    c.user("Continue with the migration and check it.", dt=30)
    c.api_error("api_error", 500, "The API request failed before the migration could continue.")

    # 5. A short print run on another project, on the small model.
    c = ClaudeWriter(home, "5f0c2a1e-0000-4000-8000-00000000000a", now - 20 * 3600, docs,
                     title="Release notes summary", model=CLAUDE_HAIKU_MODEL, ctx=9_000)
    c.user("Summarize the 2.3 release notes into three bullets per area for the docs home page. "
           "Write the result to docs/releases/summary.md.")
    c.think(3)
    c.tool("Read", {"file_path": docs + "/releases/2.3.md"}, "# 2.3.0\n\n- Sync resumes after sleep\n- ...", secs=0.2)
    c.tool("Bash", {"command": "git log --oneline v2.2.0..v2.3.0 | wc -l"}, "47", secs=0.4)
    c.think(4)
    c.tool("Write", {"file_path": docs + "/releases/summary.md", "content": SUMMARY_MD},
           "File created successfully", extra={"type": "create"})
    c.say("Wrote docs/releases/summary.md: three bullets for each of the four areas (CLI, sync, web, billing).",
          end=True)

    # 6. A finished interactive Claude desktop turn.
    c = ClaudeWriter(home, "5f0c2a1e-0000-4000-8000-00000000000b", now - 11 * 3600, app,
                     entrypoint="claude-desktop",
                     title="Fix flaky cart widget test", ctx=118_000)
    c.user("The cart total widget test fails about one run in five on CI. Find out why and fix it.")
    c.think(12)
    c.tool("Bash", {"command": "for i in 1 2 3 4 5 6; do flutter test test/cart/total_test.dart || break; done"},
           "run 1 ok\nrun 2 ok\nrun 3 ok\nrun 4 ok\n00:03 +4 -1: shows the stored total\n"
           "  Expected: <1280>\n  Actual: <0>", ok=False, secs=41.0)
    c.think(9)
    c.tool("Read", {"file_path": app + "/lib/cart/total_widget.dart"},
           "class TotalWidget extends StatefulWidget {\n  ...\n  void initState() {\n    super.initState();\n"
           "    _load();\n  }", secs=0.2)
    c.think(18)
    c.tool("Edit", {"file_path": app + "/lib/cart/total_widget.dart", "old_string": "    _load();",
                    "new_string": "    _pending = _load();"}, "The file has been updated successfully.")
    c.think(11)
    c.tool("Edit", {"file_path": app + "/test/cart/total_test.dart", "old_string": "    await tester.pump();",
                    "new_string": "    await tester.pumpAndSettle();"}, "The file has been updated successfully.")
    c.tool("Bash", {"command": "for i in 1 2 3 4 5 6; do flutter test test/cart/total_test.dart || break; done"},
           "run 1 ok\nrun 2 ok\nrun 3 ok\nrun 4 ok\nrun 5 ok\nrun 6 ok", secs=38.0)
    c.tool("Bash", {"command": "flutter analyze"}, "No issues found!", secs=6.0)
    c.say("Found it: the widget read the cart before its first load finished, so about one run in five the test "
          "saw a zero. The widget now keeps the pending load, and the test pumps until the frame settles. "
          "Six runs in a row pass locally and the analyzer is clean.", end=True, dt=2)
    return live


# ----------------------------------------------------------------------------- scenario

def generate(root: Path, now: float | None = None) -> dict:
    """Write the demo homes under `root`. Returns paths and the state entries to seed."""
    now = now or time.time()
    codex_home, claude_home, runs = root / "codex", root / "claude", root / "runs"
    for d in (codex_home, claude_home, runs):
        d.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    live_codex, waiting_codex = _codex_scenes(codex_home, runs, now, state)
    live_claude = _claude_scenes(claude_home, now)

    # File times follow the last record, as they would on a real machine.
    for f in list(codex_home.rglob("*.jsonl")) + list(claude_home.rglob("*.jsonl")):
        last = json.loads(f.read_text(encoding="utf-8").splitlines()[-1]).get("timestamp")
        if last:
            ts = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
            os.utime(f, (ts, ts))
    return {"codex_home": codex_home, "claude_home": claude_home, "state": state,
            "live": (live_codex, live_claude), "waiting": waiting_codex}


class Animator(threading.Thread):
    """Keeps the two live demo sessions moving so the dashboard looks like a real day."""

    def __init__(self, live: tuple, every: float = 3.0, waiting: CodexWriter | None = None):
        super().__init__(daemon=True)
        self.codex, self.claude = live
        self.waiting = waiting
        self.every = every
        self.stop = threading.Event()

    def run(self) -> None:
        i = 0
        cfiles = ["src/auth/middleware.py", "src/auth/cookies.py", "docs/auth.md"]
        while not self.stop.wait(self.every):
            now = time.time()
            self.codex.t = now - 1
            self.claude.t = now - 1
            if self.waiting:
                self.waiting.t = now - 1
                self.waiting.ev({"type": "token_count", "info": None})
            step = i % 4
            if step == 0:
                self.codex.think(0.5)
                self.claude.think(0.5)
            elif step == 1:
                self.codex.cmd(f"rg -n \"X-Session\" {cfiles[i // 4 % 3]}", "1 match", kind="search", dt=0.5)
                self.claude.tool("Bash", {"command": "gh pr view --json title"}, "{\"title\": \"...\"}", dt=0.5)
            elif step == 2:
                self.codex.edit(cfiles[i // 4 % 3], "@@ -1 +1 @@\n-old\n+new", dt=0.5)
                self.codex.usage(9_000, 60_000 + 1_500 * (i % 40), dt=0.1)
            else:
                self.codex.say(f"Updated {cfiles[i // 4 % 3]}; running the suite again.", dt=0.5)
                self.claude.say("Reworded two entries so they describe the change for users.", dt=0.5)
            i += 1
