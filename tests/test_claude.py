import json
import shlex
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
    assert s.origin == "sdk-cli" and s.origin_kind == "background" and s.headless
    assert s.model == "claude-opus-5-5"
    assert s.state == "running"
    assert s.n_cmds == 1 and s.n_fail == 0
    assert list(s.files) == ["CHANGELOG.md"]
    kind, n, _, plus, minus = s.files["CHANGELOG.md"]
    assert (kind, n, plus, minus) == ("update", 2, 8, 2)
    kinds = [e.kind for e in s.events]
    assert kinds == ["turn", "user", "think", "cmd", "tool", "think", "file", "file", "msg"]
    cmd = s.events[3]
    assert cmd.text == "git log --oneline v2.3.0..HEAD" and abs(cmd.dur - 1.0) < 0.01 and cmd.ok
    assert s.events[4].sub == "read"
    # usage: each assistant message counted once
    assert s.tokens == 7 * (4 + 40_000 + 600 + 300)
    assert s.ctx_used == 40_604 and s.context_window == 1_000_000


def test_desktop_session_title_and_end_turn(demo_env):
    s = load(demo_env, "5f0c2a1e-0000-4000-8000-00000000000b")
    assert s.title == "Fix flaky cart widget test"
    assert s.origin == "claude-desktop" and s.origin_kind == "desktop" and not s.headless
    assert s.state == "complete"
    assert s.last_agent_message.startswith("Found it")
    assert s.events[-1].kind == "turn" and s.events[-1].sub == "complete"
    assert Run("demo", "claude", s, None, s.label).status == "your_turn"


def test_desktop_tool_can_be_quiet_without_being_stopped(demo_env):
    s = load(demo_env, "5f0c2a1e-0000-4000-8000-000000000007")
    run = Run("demo", "claude", s, None, s.label)
    assert s.origin == "claude-desktop" and s.origin_kind == "desktop" and s.state == "running"
    assert s.in_flight and not s.recently_active(demo_env["now"])
    assert run.status == "quiet"


def test_cli_api_error_after_end_turn_is_failed(demo_env):
    s = load(demo_env, "5f0c2a1e-0000-4000-8000-000000000009")
    assert s.origin == "cli" and not s.headless
    assert s.state == "failed"
    assert Run("demo", "claude", s, None, s.label).status == "failed"


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
    assert s.n_cmds == 1 and s.n_fail == 1
    assert any(event.kind == "err" and event.sub == "Edit" for event in s.events)
    assert s.files == {}  # a failed edit changes nothing
    assert s.state == "stopped"
    assert s.tokens == 45


def test_api_error_records_end_the_turn_as_failed_or_limited(tmp_path):
    def api_error(text, error, status, quota_limits=None):
        record = {"type": "assistant", "timestamp": "2026-01-01T00:00:02Z", "sessionId": "s",
                  "isApiErrorMessage": True, "error": error, "apiErrorStatus": status,
                  "message": {"id": "m2", "role": "assistant", "model": "claude-x", "type": "message",
                              "content": [{"type": "text", "text": text}], "stop_reason": "synthetic_error",
                              "stop_sequence": None, "usage": {}, "container": None,
                              "context_management": None}}
        if quota_limits is not None:
            record["quotaLimits"] = quota_limits
        return record

    failed = api_error("The API request failed.", "api_error", 500)
    limited = api_error("API Error: 429", "rate_limit", 429,
                        {"status": "rejected", "resetsAt": 1_800_000_000, "rateLimitType": "five_hour"})

    failed_session = write(tmp_path, [u("go"), failed])
    limited_session = write(tmp_path, [u("go"), limited])

    assert failed_session.state == "failed"
    assert limited_session.state == "limited"
    assert limited_session.limit_info["type"] == "five_hour"


def test_max_tokens_stop_reason_completes_the_turn(tmp_path):
    session = write(tmp_path, [u("go"), a({"type": "text", "text": "Partial answer"}, stop="max_tokens")])
    assert session.state == "complete"


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
    prov = ClaudeProvider()
    s = load(demo_env, SID)
    run = Run("k", "claude", s, None, "x")
    spec = prov.resume(run)
    assert spec == {"argv": ["claude", "--resume", SID], "cwd": s.cwd}
    assert prov.resume_cmd(run, shell="sh") == (
        f"cd -- {shlex.quote(s.cwd)} && {shlex.join(spec['argv'])}")
    assert prov.resume_cmd(run, shell="powershell") == (
        f"Set-Location -LiteralPath '{s.cwd}'; claude --resume {SID}")


def test_usage_limit_shows_until_claude_answers_again(tmp_path):
    hit = {"type": "assistant", "timestamp": "2026-01-01T00:00:05Z", "isApiErrorMessage": True,
           "message": {"model": "<synthetic>", "content": [
               {"type": "text", "text": "You've hit your weekly limit · resets Sep 30, 1am (America/Montevideo)"}]}}
    s = write(tmp_path, [u("go"), hit])
    quota = ClaudeProvider().quota([s])
    assert quota["reporting"] == "limit-only" and quota["windows"] == []
    assert quota["limit"]["reset_text"] == "Sep 30, 1am" and quota["limit"]["source"] == "message"
    later = a({"type": "text", "text": "back"}, stop="end_turn")
    later["timestamp"] = "2026-01-01T09:00:00Z"
    s = write(tmp_path, [u("go"), hit, later])
    assert ClaudeProvider().quota([s]) == {"reporting": "limit-only", "windows": [], "limit": None}


def test_structured_limit_record_gives_window_and_reset(tmp_path):
    """Claude Code writes the limit itself (error + quotaLimits); the wording of the message is not needed."""
    reset = int(time.time()) + 3600
    hit = {"type": "assistant", "timestamp": "2026-01-01T00:00:05Z", "isApiErrorMessage": True,
           "error": "rate_limit", "apiErrorStatus": 429,
           "quotaLimits": {"status": "rejected", "resetsAt": reset, "rateLimitType": "seven_day"},
           "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "API Error: 429"}]}}
    s = write(tmp_path, [u("go"), hit])
    limit = ClaudeProvider().quota([s])["limit"]
    assert s.state == "limited"
    assert limit["window"] == "week" and limit["name"] == "weekly"
    assert limit["scope"] == "account" and limit["resets_at"] == reset
    assert limit["source"] == "recorded"
    # A reset time already in the past means the limit lifted, even if nothing was answered yet.
    hit["quotaLimits"]["resetsAt"] = int(time.time()) - 60
    s = write(tmp_path, [u("go"), hit])
    assert ClaudeProvider().quota([s])["limit"] is None


def test_opus_limit_survives_a_later_sonnet_success(tmp_path):
    hit = {"type": "assistant", "timestamp": "2026-01-01T00:00:05Z", "isApiErrorMessage": True,
           "error": "rate_limit", "quotaLimits": {"rateLimitType": "seven_day_opus"},
           "message": {"model": "claude-opus-5-5", "content": [{"type": "text", "text": "API Error: 429"}]}}
    opus = write(tmp_path, [u("go"), hit])
    sonnet_reply = a({"type": "text", "text": "back"}, stop="end_turn")
    sonnet_reply["timestamp"] = "2026-01-01T09:00:00Z"
    sonnet_reply["message"]["model"] = "claude-sonnet-4-5"
    sonnet = write(tmp_path, [u("go"), sonnet_reply])

    limit = ClaudeProvider().quota([opus, sonnet])["limit"]
    assert limit["window"] == "opus-week" and limit["name"] == "weekly Opus"
    assert limit["scope"] == "model:opus"

    opus_reply = a({"type": "text", "text": "back"}, stop="end_turn")
    opus_reply["timestamp"] = "2026-01-01T09:00:00Z"
    opus_reply["message"]["model"] = "claude-opus-5-5"
    recovered = write(tmp_path, [u("go"), hit, opus_reply])
    assert ClaudeProvider().quota([recovered])["limit"] is None


def test_newer_process_in_the_folder_owns_the_new_file(demo_env):
    """A process with no transcript yet must not take the file of a process that started after it."""
    prov = ClaudeProvider()
    s = load(demo_env, SID)
    born = ClaudeProvider.born(s.path)
    older = SimpleNamespace(pid=1, session_id=None, cwd=s.cwd, create=born - 1000)
    newer = SimpleNamespace(pid=2, session_id=None, cwd=s.cwd, create=born - 5)
    peers = [older, newer]
    assert prov.match(older, set(), peers) is None
    assert prov.match(newer, set(), peers) == s.path
