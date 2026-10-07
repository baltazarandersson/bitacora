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
CLAUDE_MODEL = "claude-opus-5-5"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def demo_cwd(name: str) -> str:
    return f"/home/dev/{name}"


# ----------------------------------------------------------------------------- codex

class CodexWriter:
    def __init__(self, home: Path, sid: str, start: float, cwd: str, source: str = "exec",
                 originator: str = "codex_exec", effort: str = "high", sandbox: str = "workspace-write"):
        day = datetime.fromtimestamp(start)
        folder = home / "sessions" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"rollout-{day:%Y-%m-%dT%H-%M-%S}-{sid}.jsonl"
        self.path.write_text("", encoding="utf-8")
        self.cwd = cwd
        self.t = start
        self.rec("session_meta", {"id": sid, "timestamp": iso(start), "cwd": cwd, "source": source,
                                  "originator": originator})
        self.rec("turn_context", {"model": CODEX_MODEL, "effort": effort, "sandbox_policy": {"type": sandbox}})
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

    def ask(self, question: str, dt: float = 1.0) -> None:
        self.rec("response_item", {"type": "function_call", "name": "request_user_input", "call_id": "q1",
                                   "arguments": json.dumps({"questions": [{"question": question}]})}, dt)

    def complete(self, message: str, dt: float = 1.0) -> None:
        self.say(message, dt)
        self.ev({"type": "task_complete", "last_agent_message": message}, 0.2)

    def abort(self, reason: str = "interrupted", dt: float = 1.0) -> None:
        self.ev({"type": "turn_aborted", "reason": reason}, dt)


# ----------------------------------------------------------------------------- claude

class ClaudeWriter:
    def __init__(self, home: Path, sid: str, start: float, cwd: str, entrypoint: str = "sdk-cli",
                 title: str = ""):
        folder = home / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", cwd)
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{sid}.jsonl"
        self.path.write_text("", encoding="utf-8")
        self.sid, self.cwd, self.entry, self.t = sid, cwd, entrypoint, start
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
                  ctx: int = 40_000) -> None:
        o = self._base("assistant", dt)
        o["message"] = {"id": f"msg_{self.n}", "role": "assistant", "model": CLAUDE_MODEL, "content": [block],
                        "stop_reason": stop, "usage": {"input_tokens": 4, "cache_read_input_tokens": ctx,
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


# ----------------------------------------------------------------------------- scenario

RATE = {"primary": {"used_percent": 38.0, "window_minutes": 300},
        "secondary": {"used_percent": 61.0, "window_minutes": 10080}}


def generate(root: Path, now: float | None = None) -> dict:
    """Write the demo homes under `root`. Returns paths and the state entries to seed."""
    now = now or time.time()
    codex_home, claude_home, runs = root / "codex", root / "claude", root / "runs"
    for d in (codex_home, claude_home, runs):
        d.mkdir(parents=True, exist_ok=True)
    rate = {k: dict(v, resets_at=int(now + (2 * 3600 if k == "primary" else 3 * 86400)))
            for k, v in RATE.items()}
    state: dict = {}
    api, web, infra = demo_cwd("shop-api"), demo_cwd("shop-web"), demo_cwd("infra")

    # 1. A background run that is working right now.
    sid = "0199a1b2-0000-7000-8000-000000000001"
    w = CodexWriter(codex_home, sid, now - 7 * 60, api)
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
    live_codex = w

    # 2. A background run waiting for an answer.
    sid = "0199a1b2-0000-7000-8000-000000000002"
    w = CodexWriter(codex_home, sid, now - 12 * 60, infra, sandbox="read-only")
    w.prompt("Plan the Postgres 17 upgrade for the staging cluster.")
    w.think(20)
    w.cmd("cat terraform/db.tf", "engine_version = \"15.6\"", kind="read")
    w.usage(30_000, 28_000)
    w.ask("Staging has 2 replicas. Upgrade in place, or blue/green with a new cluster?", dt=30)
    w.t = now - 50
    w.ev({"type": "token_count", "info": None})
    state[f"codex:{sid}"] = {"label": "pg17-plan", "out": None, "log": None}

    # 3. A finished background run with its result file.
    sid = "0199a1b2-0000-7000-8000-000000000003"
    w = CodexWriter(codex_home, sid, now - 95 * 60, web, sandbox="read-only")
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

    # 4. A background run that was cut off.
    sid = "0199a1b2-0000-7000-8000-000000000004"
    w = CodexWriter(codex_home, sid, now - 5 * 3600, api)
    w.prompt("Find why test_orders is flaky on CI.")
    w.think(12)
    w.cmd("pytest tests/test_orders.py -x --count 20", "19 passed, 1 failed", code=1, secs=41)
    w.abort("interrupted", dt=60)
    state[f"codex:{sid}"] = {"label": "flaky-orders", "out": None, "log": None}

    # 5. An interactive Codex thread from the desktop app.
    sid = "0199a1b2-0000-7000-8000-000000000005"
    w = CodexWriter(codex_home, sid, now - 3 * 3600, web, source="vscode", originator="Codex Desktop",
                    effort="medium")
    w.prompt("Rewrite the landing hero copy, shorter and less salesy.")
    w.think(8)
    w.complete("Here are three options for the hero, from most to least direct…", dt=20)
    with open(codex_home / "session_index.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"id": sid, "thread_name": "Landing hero copy", "updated_at": iso(now)}) + "\n")

    # 6. A Claude Code run in print mode, working right now.
    c = ClaudeWriter(claude_home, "5f0c2a1e-0000-4000-8000-000000000006", now - 4 * 60, web)
    c.user("Update CHANGELOG.md for the 2.4.0 release from the merged PRs since 2.3.0.")
    c.think(4)
    c.tool("Bash", {"command": "git log --oneline v2.3.0..HEAD"}, "a1f3 Add dark mode\n9c2e Fix cart rounding")
    c.tool("Read", {"file_path": web + "/CHANGELOG.md"}, "# Changelog")
    c.think(11)
    c.tool("Edit", {"file_path": web + "/CHANGELOG.md",
                    "old_string": "# Changelog\n",
                    "new_string": "# Changelog\n\n## 2.4.0\n- Dark mode\n- Cart rounding fix\n"})
    c.t = now - 8
    c.say("Added the 2.4.0 section. Checking that every PR title is user facing.")
    live_claude = c

    # 7. An interactive Claude Code session from the desktop app.
    c = ClaudeWriter(claude_home, "5f0c2a1e-0000-4000-8000-000000000007", now - 2 * 3600, api,
                     entrypoint="claude-desktop", title="Review PR 42: rate limiter")
    c.user("Review PR 42, focus on the rate limiter edge cases.")
    c.think(9)
    c.tool("Bash", {"command": "gh pr diff 42"}, "+ bucket.refill(now)")
    c.tool("Grep", {"pattern": "refill", "path": "src"}, "src/limits/bucket.py")
    c.say("Two issues: refill can overflow capacity after a long idle period, and the lock is not held "
          "while reading `tokens`. Details below.", end=True, dt=30)

    # 8. An older terminal session.
    c = ClaudeWriter(claude_home, "5f0c2a1e-0000-4000-8000-000000000008", now - 7 * 3600, infra,
                     entrypoint="cli", title="Fix login redirect loop")
    c.user("Users get a redirect loop after login on Safari.")
    c.think(15)
    c.tool("Read", {"file_path": infra + "/nginx.conf"}, "...")
    c.tool("Bash", {"command": "curl -I https://staging.example.test/login"}, "HTTP/2 302", ok=True)
    c.say("The cookie is set with SameSite=Strict on a cross-site redirect. Switched it to Lax.", end=True)

    # File times follow the last record, as they would on a real machine.
    for f in list(codex_home.rglob("*.jsonl")) + list(claude_home.rglob("*.jsonl")):
        last = json.loads(f.read_text(encoding="utf-8").splitlines()[-1]).get("timestamp")
        if last:
            ts = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
            os.utime(f, (ts, ts))
    return {"codex_home": codex_home, "claude_home": claude_home, "state": state,
            "live": (live_codex, live_claude)}


class Animator(threading.Thread):
    """Keeps the two live demo sessions moving so the dashboard looks like a real day."""

    def __init__(self, live: tuple, every: float = 3.0):
        super().__init__(daemon=True)
        self.codex, self.claude = live
        self.every = every
        self.stop = threading.Event()

    def run(self) -> None:
        i = 0
        cfiles = ["src/auth/middleware.py", "src/auth/cookies.py", "docs/auth.md"]
        while not self.stop.wait(self.every):
            now = time.time()
            self.codex.t = now - 1
            self.claude.t = now - 1
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
