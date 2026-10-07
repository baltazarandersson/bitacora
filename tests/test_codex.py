import json
from types import SimpleNamespace

from bitacora.model import Run
from bitacora.providers.codex import CodexProvider, CodexSession, origin_of, parse_codex_args

SID = "0199a1b2-0000-7000-8000-000000000001"


def test_parse_exec_args():
    argv = ["codex.exe", "exec", "-m", "gpt-x", "-c", 'model_reasoning_effort="high"', "-C", "/repo",
            "-s", "read-only", "-o", "/tmp/r.result.md", "-"]
    info = parse_codex_args(argv)
    assert info["headless"] is True
    assert info["model"] == "gpt-x"
    assert info["effort"] == "high"
    assert info["sandbox"] == "read-only"
    assert info["out"].endswith("r.result.md")
    assert "session_id" not in info


def test_parse_exec_resume_and_inline_flags():
    argv = ["codex", "exec", "resume", "--model=gpt-y", "-c", "sandbox_mode=workspace-write", SID, "-"]
    info = parse_codex_args(argv)
    assert info["session_id"] == SID
    assert info["model"] == "gpt-y"
    assert info["sandbox"] == "workspace-write"


def test_parse_interactive_and_non_sessions():
    assert parse_codex_args(["codex"]) == {"headless": False}
    assert parse_codex_args(["codex", "resume", SID])["session_id"] == SID
    assert parse_codex_args(["codex", "app-server", "--listen", "unix://"]) is None
    assert parse_codex_args(["codex", "login"]) is None
    assert parse_codex_args(["codex.exe", "--type=renderer"]) is None


def test_origin_of():
    assert origin_of({"source": "exec"}) == ("exec", True)
    assert origin_of({"originator": "Codex Desktop", "source": "vscode"}) == ("desktop", False)
    assert origin_of({"originator": "codex-tui", "source": "vscode"}) == ("tui", False)
    assert origin_of({"source": "vscode"}) == ("ide", False)
    assert origin_of({"source": {"subagent": "x"}}) == ("subagent", True)


def session_from(demo_env, sid) -> CodexSession:
    path = next(demo_env["codex_home"].rglob(f"*{sid}.jsonl"))
    s = CodexSession(path)
    s.update()
    return s


def test_live_rollout(demo_env):
    s = session_from(demo_env, SID)
    assert s.session_id == SID
    assert s.origin == "exec" and s.headless
    assert s.model == "gpt-6-luna" and s.effort == "high" and s.sandbox == "workspace-write"
    assert s.state == "running"
    assert s.n_cmds == 4 and s.n_fail == 1
    files = s.files
    assert set(files) == {"src/auth/session.py", "tests/test_session_cookies.py"}
    kind, n, _, plus, minus = files["src/auth/session.py"]
    assert (kind, n, plus, minus) == ("update", 2, 6, 1)
    assert files["tests/test_session_cookies.py"][0] == "add"
    assert s.tokens == 109_000 and s.ctx_used == 58_500 and s.ctx_window == 272_000
    assert s.rate_limits["primary"]["used_percent"] == 38.0
    assert s.first_prompt.startswith("Task: move session auth")
    assert s.recently_active(demo_env["now"])
    kinds = [e.kind for e in s.events]
    assert kinds[:3] == ["turn", "user", "think"]
    think = s.events[2]
    assert think.dur >= 5


def test_question_complete_and_abort(demo_env):
    ask = session_from(demo_env, "0199a1b2-0000-7000-8000-000000000002")
    assert list(ask.open_questions.values()) == [
        "Staging has 2 replicas. Upgrade in place, or blue/green with a new cluster?"]
    done = session_from(demo_env, "0199a1b2-0000-7000-8000-000000000003")
    assert done.state == "complete" and done.last_agent_message.startswith("# Checkout a11y audit")
    cut = session_from(demo_env, "0199a1b2-0000-7000-8000-000000000004")
    assert cut.state == "aborted"
    assert cut.events[-1].kind == "err" and cut.events[-1].sub == "aborted"


def test_incremental_reads_and_partial_lines(tmp_path):
    path = tmp_path / "rollout-2026-01-01T00-00-00-x.jsonl"
    meta = json.dumps({"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta",
                       "payload": {"id": "abc", "cwd": "/r", "source": "exec"}})
    msg = json.dumps({"timestamp": "2026-01-01T00:00:05Z", "type": "event_msg",
                      "payload": {"type": "item_completed",
                                  "item": {"type": "AgentMessage", "content": [{"type": "text", "text": "hi"}]}}})
    path.write_bytes((meta + "\n" + msg[:20]).encode())
    s = CodexSession(path)
    assert s.update()
    assert s.session_id == "abc" and not s.messages
    with open(path, "ab") as fh:
        fh.write((msg[20:] + "\n").encode())
    assert s.update()
    assert [m[1] for m in s.messages] == ["hi"]
    assert not s.update()  # nothing new
    path.write_bytes((meta + "\n").encode())  # rewritten shorter: start over
    s.update()
    assert s.messages == [] and s.session_id == "abc"


def test_garbage_lines_are_ignored(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_bytes(b'not json\n[1,2]\n{"type":"event_msg","payload":{"type":"item_completed","item":5}}\n')
    s = CodexSession(path)
    s.update()
    assert s.events_total == 0


def test_resume_cmd_has_no_invented_defaults(demo_env):
    prov = CodexProvider()
    s = session_from(demo_env, SID)
    run = Run("k", "codex", s, None, "x")
    assert prov.resume_cmd(run) == (f'codex exec resume -m gpt-6-luna -c model_reasoning_effort="high" '
                                    f'-c sandbox_mode="workspace-write" {SID} -')
    s.model = s.effort = s.sandbox = None
    assert prov.resume_cmd(run) == f"codex exec resume {SID} -"
    s.headless = False
    assert prov.resume_cmd(run) == f"codex resume {SID}"


def test_thread_titles_and_history(demo_env):
    prov = CodexProvider()
    assert prov.available()
    files = list(prov.history(demo_env["now"] - 24 * 3600))
    assert len(files) == 5
    desktop = session_from(demo_env, "0199a1b2-0000-7000-8000-000000000005")
    assert prov.title(desktop) == "Landing hero copy"
    assert desktop.origin == "desktop" and not desktop.headless
    assert prov.quota([session_from(demo_env, SID)])["secondary"]["window_minutes"] == 10080


def test_match_new_exec_by_cwd_and_time(demo_env):
    prov = CodexProvider()
    s = session_from(demo_env, SID)
    pi = SimpleNamespace(session_id=None, create=s.start + 1, cwd=s.cwd)
    assert prov.match(pi, set()) == s.path
    assert prov.match(pi, {s.path}) is None
    pi = SimpleNamespace(session_id="0199a1b2-0000-7000-8000-000000000003", create=0, cwd=None)
    assert prov.match(pi, set()).name.endswith("000000000003.jsonl")
