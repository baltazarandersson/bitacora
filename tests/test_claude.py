import json
import time
from types import SimpleNamespace

from bitacora.model import Run
from bitacora.procs import Proc
from bitacora.providers.claude import ClaudeProvider, ClaudeSession, is_claude_code, parse_claude_args, slug

SID = "5f0c2a1e-0000-4000-8000-000000000006"


def proc(cmdline, name=None, pid=1, ppid=0):
    exe_name = cmdline[0].replace("\\", "/").rsplit("/", 1)[-1]  # Windows paths too, on any OS
    return Proc(pid, name or exe_name.lower(), cmdline, time.time(), ppid)


def test_parse_args():
    info = parse_claude_args(["claude", "-p", "--model", "opus", "--permission-mode", "acceptEdits"])
    assert info == {"headless": True, "model": "opus", "sandbox": "acceptEdits"}
    assert parse_claude_args(["claude", f"--resume={SID}"])["session_id"] == SID
    assert parse_claude_args(["claude", "--resume", SID, "--effort", "high"])["session_id"] == SID
    assert parse_claude_args(["claude", "-r"]).get("session_id") is None  # picker, no id yet
    assert "session_id" not in parse_claude_args(["claude", f"--resume={SID}", "--fork-session"])
    assert parse_claude_args(["claude", "--dangerously-skip-permissions"])["sandbox"] == "bypassPermissions"


def test_which_processes_are_claude_code():
    assert is_claude_code(proc(["C:\\Users\\me\\AppData\\Roaming\\Claude\\claude-code\\2.1\\claude.exe", "-p"]))
    assert is_claude_code(proc(["/home/me/.local/bin/claude"]))
    assert is_claude_code(proc(["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], name="node"))
    # The desktop app shell and its helpers are not Claude Code.
    assert not is_claude_code(proc(["C:\\Program Files\\WindowsApps\\Claude_2.0_x64\\app\\Claude.exe"]))
    assert not is_claude_code(proc(["C:\\Users\\me\\AppData\\Local\\AnthropicClaude\\app-1\\claude.exe"]))
    assert not is_claude_code(proc(["/x/claude", "--type=renderer"]))
    assert not is_claude_code(proc(["node", "server.js"], name="node"))


def test_wrapper_and_child_count_once(monkeypatch):
    prov = ClaudeProvider()
    snap = [proc(["node", "/n/@anthropic-ai/claude-code/cli.js"], name="node", pid=10),
            proc(["/n/@anthropic-ai/claude-code/bin/claude", "-p"], pid=11, ppid=10)]
    monkeypatch.setattr(Proc, "cwd", lambda self: "/repo")
    found = prov.procs(snap)
    assert [p.pid for p in found] == [11] and found[0].headless


def test_slug():
    assert slug("C:\\Users\\me\\my_app") == "C--Users-me-my-app"
    assert slug("/home/me/.claude/worktrees/x") == "-home-me--claude-worktrees-x"


def load(demo_env, sid) -> ClaudeSession:
    path = next(demo_env["claude_home"].rglob(f"{sid}.jsonl"))
    s = ClaudeSession(path)
    s.update()
    return s


def test_print_session(demo_env):
    s = load(demo_env, SID)
    assert s.session_id == SID
    assert s.origin == "print" and s.headless
    assert s.model == "claude-opus-5-5"
    assert s.state == "running"
    assert s.n_cmds == 2 and s.n_fail == 0
    assert list(s.files) == ["CHANGELOG.md"]
    kind, n, _, plus, minus = s.files["CHANGELOG.md"]
    assert (kind, plus, minus) == ("update", 5, 1)
    kinds = [e.kind for e in s.events]
    assert kinds == ["turn", "user", "think", "cmd", "cmd", "think", "file", "msg"]
    cmd = s.events[3]
    assert cmd.text == "git log --oneline v2.3.0..HEAD" and abs(cmd.dur - 1.0) < 0.01 and cmd.ok
    assert s.events[4].sub == "read"
    # usage: each assistant message counted once
    assert s.tokens == 6 * (4 + 40_000 + 600 + 300)
    assert s.ctx_used == 40_604


def test_desktop_session_title_and_end_turn(demo_env):
    s = load(demo_env, "5f0c2a1e-0000-4000-8000-000000000007")
    assert s.title == "Review PR 42: rate limiter"
    assert s.origin == "desktop" and not s.headless
    assert s.state == "complete"
    assert s.last_agent_message.startswith("Two issues")
    assert s.events[-1].kind == "turn" and s.events[-1].sub == "complete"


def write(tmp_path, lines) -> ClaudeSession:
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    s = ClaudeSession(path)
    s.update()
    return s


def u(content, **kw):
    return {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "sessionId": "s", "cwd": "/r",
            "entrypoint": "cli", "message": {"role": "user", "content": content}, **kw}


def a(block, stop="tool_use", mid="m1", **kw):
    return {"type": "assistant", "timestamp": "2026-01-01T00:00:01Z", "sessionId": "s", "cwd": "/r",
            "message": {"id": mid, "model": "claude-x", "content": [block], "stop_reason": stop,
                        "usage": {"input_tokens": 10, "output_tokens": 5}}, **kw}


def test_title_priority_and_noise(tmp_path):
    s = write(tmp_path, [
        {"type": "ai-title", "aiTitle": "auto"},
        u("real prompt"),
        u("Base directory for this skill", isMeta=True),
        u("<task-notification>done</task-notification>"),
        a({"type": "text", "text": "side"}, isSidechain=True),
        {"type": "custom-title", "customTitle": "mine"},
        {"type": "ai-title", "aiTitle": "later auto"},
    ])
    assert s.title == "mine"
    assert s.first_prompt == "real prompt"
    assert [e.kind for e in s.events] == ["turn", "user", "note"]
    assert s.events[-1].text == "task-notification"


def test_questions_interrupts_and_failures(tmp_path):
    s = write(tmp_path, [
        u("go"),
        a({"type": "tool_use", "id": "t1", "name": "AskUserQuestion",
           "input": {"questions": [{"question": "Which DB?"}]}}),
    ])
    assert list(s.open_questions.values()) == ["Which DB?"]
    s = write(tmp_path, [
        u("go"),
        a({"type": "tool_use", "id": "t1", "name": "AskUserQuestion", "input": {"questions": []}}),
        u([{"type": "tool_result", "tool_use_id": "t1", "content": "Postgres"}]),
        a({"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "make"}}, mid="m2"),
        u([{"type": "tool_result", "tool_use_id": "t2", "content": "boom", "is_error": True}]),
        a({"type": "tool_use", "id": "t3", "name": "Edit", "input": {"file_path": "/r/a.py",
                                                                      "old_string": "x", "new_string": "y"}}, mid="m3"),
        u([{"type": "tool_result", "tool_use_id": "t3", "content": "not found", "is_error": True}]),
        u([{"type": "text", "text": "[Request interrupted by user for tool use]"}]),
    ])
    assert s.open_questions == {}
    assert s.n_cmds == 2 and s.n_fail == 2
    assert s.files == {}  # a failed edit changes nothing
    assert s.state == "aborted"
    assert s.tokens == 45


def test_in_flight_tool_shows_until_result(tmp_path):
    s = write(tmp_path, [u("go"), a({"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "sleep 9"}})])
    assert [e.text for e in s.in_flight.values()] == ["sleep 9"]
    assert s.n_cmds == 0


def test_match_prefers_id_then_new_file_in_cwd(demo_env):
    prov = ClaudeProvider()
    s = load(demo_env, SID)
    pi = SimpleNamespace(session_id=SID, cwd=None, create=0)
    assert prov.match(pi, set()) == s.path
    # A forked or new session: the file created after the process started, in the cwd's folder.
    pi = SimpleNamespace(session_id=None, cwd=s.cwd, create=s.start - 5)
    assert prov.match(pi, set()) == s.path
    pi = SimpleNamespace(session_id=None, cwd=s.cwd, create=time.time() + 60)
    assert prov.match(pi, set()) is None


def test_resume_cmd(demo_env):
    s = load(demo_env, SID)
    cmd = ClaudeProvider().resume_cmd(Run("k", "claude", s, None, "x"))
    assert cmd == f'cd "{s.cwd}"; claude --resume {SID}'
