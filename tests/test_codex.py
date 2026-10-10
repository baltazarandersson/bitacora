import json
import os
import time
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
    assert origin_of({"originator": "codex_exec"}) == ("exec", True)
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
    assert cut.state == "stopped"
    assert cut.events[-1].kind == "err" and cut.events[-1].sub == "aborted"
    stale_question = session_from(demo_env, "0199a1b2-0000-7000-8000-000000000009")
    stale_run = Run("ask", "codex", stale_question, None, "question")
    assert not stale_question.recently_active(demo_env["now"])
    assert stale_run.status == "asking" and stale_run.live
    no_turn = session_from(demo_env, "0199a1b2-0000-7000-8000-000000000010")
    assert Run("unknown", "codex", no_turn, None, "unknown").status == "unknown"
    limited = session_from(demo_env, "0199a1b2-0000-7000-8000-000000000007")
    assert limited.state == "limited" and Run("limited", "codex", limited, None, "limited").status == "limited"


def test_task_complete_error_marks_failed_or_limited(tmp_path):
    cases = [
        ({"message": "The request failed."}, "failed"),
        ({"message": "You've hit your usage limit."}, "limited"),
        ({"codex_error_info": "usage_limit", "message": "Request rejected", "turn_id": "synthetic",
          "time_to_first_token_ms": 12}, "limited"),
    ]
    for index, (error, expected) in enumerate(cases):
        path = tmp_path / f"rollout-2026-01-01T00-00-00-{index}.jsonl"
        path.write_text("\n".join([
            _rec("2026-01-01T00:00:00Z", "session_meta", {"id": str(index), "source": "exec"}),
            _rec("2026-01-01T00:00:01Z", "event_msg", {"type": "task_started"}),
            _rec("2026-01-01T00:00:02Z", "event_msg", {"type": "task_complete", "error": error,
                  "last_agent_message": None, "turn_id": "synthetic-turn"}),
        ]) + "\n", encoding="utf-8")
        session = CodexSession(path)
        session.update()
        assert session.state == expected
        assert session.last_event.kind == "err"


def test_reached_type_limit_stops_the_turn(tmp_path):
    path = tmp_path / "rollout-2026-01-01T00-00-00-limit.jsonl"
    path.write_text("\n".join([
        _rec("2026-01-01T00:00:00Z", "session_meta", {"id": "limit", "source": "exec"}),
        _rec("2026-01-01T00:00:01Z", "event_msg", {"type": "task_started"}),
        _rec("2026-01-01T00:00:02Z", "event_msg", {"type": "token_count", "info": None,
              "rate_limits": {"rate_limit_reached_type": "primary"}}),
    ]) + "\n", encoding="utf-8")
    session = CodexSession(path)
    session.update()
    assert session.state == "limited"


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


def test_same_size_rewrite_starts_a_new_generation(tmp_path):
    path = tmp_path / "rollout-2026-01-01T00-00-00-x.jsonl"
    first = _rec("2026-01-01T00:00:00Z", "session_meta", {"id": "aaa", "cwd": "/r", "source": "exec"})
    second = _rec("2026-01-01T00:00:00Z", "session_meta", {"id": "bbb", "cwd": "/r", "source": "exec"})
    assert len(first) == len(second)
    path.write_text(first + "\n", encoding="utf-8")
    s = CodexSession(path)
    s.update()
    generation = s.gen
    st = path.stat()

    path.write_text(second + "\n", encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert s.update()
    assert s.gen != generation and s.session_id == "bbb"


def test_truncate_and_regrow_past_old_offset_is_detected(tmp_path):
    path = tmp_path / "rollout-2026-01-01T00-00-00-x.jsonl"
    old = _rec("2026-01-01T00:00:00Z", "session_meta",
               {"id": "old", "cwd": "/r", "source": "exec", "unused": "x" * 1024})
    path.write_text(old + "\n", encoding="utf-8")
    s = CodexSession(path)
    s.update()
    generation, old_size = s.gen, s.offset
    st = path.stat()

    new_lines = [_rec("2026-01-02T00:00:00Z", "session_meta", {"id": "new", "cwd": "/r", "source": "exec"})]
    new_lines += [_rec("2026-01-02T00:00:01Z", "event_msg", {"type": "task_started"}) for _ in range(20)]
    with open(path, "wb") as fh:
        fh.write(("\n".join(new_lines) + "\n").encode("utf-8"))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert path.stat().st_size > old_size

    assert s.update()
    assert s.gen != generation and s.session_id == "new"
    assert s.events_total == 20


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
    assert len(files) == 10  # every Codex session in the demo is inside the last day
    desktop = session_from(demo_env, "0199a1b2-0000-7000-8000-000000000005")
    assert prov.title(desktop) == "Landing hero copy"
    assert desktop.origin == "Codex Desktop" and desktop.origin_kind == "desktop" and not desktop.headless
    week = next(window for window in prov.quota([session_from(demo_env, SID)])["windows"]
                if window["id"] == "week")
    assert week["minutes"] == 10080


def test_match_new_exec_by_cwd_and_time(demo_env):
    prov = CodexProvider()
    s = session_from(demo_env, SID)
    pi = SimpleNamespace(session_id=None, create=s.start + 1, cwd=s.cwd)
    assert prov.match(pi, set()) == s.path
    assert prov.match(pi, {s.path}) is None
    pi = SimpleNamespace(session_id="0199a1b2-0000-7000-8000-000000000003", create=0, cwd=None)
    assert prov.match(pi, set()).name.endswith("000000000003.jsonl")


def test_match_leaves_delayed_old_process_unmatched_and_uses_newer_rollout(tmp_path, monkeypatch):
    prov = CodexProvider()
    older_file = tmp_path / "rollout-old.jsonl"
    newer_file = tmp_path / "rollout-new.jsonl"
    older_file.touch()
    newer_file.touch()
    timestamps = {older_file: 1070.0, newer_file: 1055.0}
    monkeypatch.setattr(prov, "_date_dirs", lambda _days: [tmp_path])
    monkeypatch.setattr(prov, "name_time", lambda path: timestamps[path])
    monkeypatch.setattr(prov, "peek_meta", lambda _path: {"cwd": "/repo"})
    old = SimpleNamespace(session_id=None, create=1000.0, cwd="/repo")
    new = SimpleNamespace(session_id=None, create=1050.0, cwd="/repo")
    peers = [old, new]

    assert prov.match(old, set(), peers) is None
    assert prov.match(new, set(), peers) == newer_file


def test_match_leaves_near_tied_rollouts_unmatched(tmp_path, monkeypatch):
    prov = CodexProvider()
    first = tmp_path / "rollout-first.jsonl"
    second = tmp_path / "rollout-second.jsonl"
    first.touch()
    second.touch()
    timestamps = {first: 1005.0, second: 1005.1}
    monkeypatch.setattr(prov, "_date_dirs", lambda _days: [tmp_path])
    monkeypatch.setattr(prov, "name_time", lambda path: timestamps[path])
    monkeypatch.setattr(prov, "peek_meta", lambda _path: {"cwd": "/repo"})
    pi = SimpleNamespace(session_id=None, create=1000.0, cwd="/repo")

    assert prov.match(pi, set()) is None


def _rec(ts, typ, payload):
    return json.dumps({"timestamp": ts, "type": typ, "payload": payload})


def test_quota_survives_the_limit_and_reports_it(tmp_path):
    windows = {"limit_id": "codex", "primary": {"used_percent": 100.0, "window_minutes": 300},
               "secondary": {"used_percent": 80.0, "window_minutes": 10080}}
    empty = {"limit_id": "premium", "primary": None, "secondary": None}
    path = tmp_path / "rollout-2026-01-01T00-00-00-x.jsonl"
    path.write_text("\n".join([
        _rec("2026-01-01T00:00:00Z", "session_meta", {"id": "x", "source": "exec"}),
        _rec("2026-01-01T00:01:00Z", "event_msg", {"type": "token_count", "rate_limits": windows}),
        _rec("2026-01-01T00:02:00Z", "event_msg", {"type": "token_count", "rate_limits": empty}),
        _rec("2026-01-01T00:02:01Z", "event_msg", {"type": "task_complete", "error": {
            "message": "You've hit your usage limit. Upgrade to Pro or try again at 1:26 AM."}}),
    ]) + "\n", encoding="utf-8")
    s = CodexSession(path)
    s.update()
    quota = CodexProvider().quota([s])
    week = next(window for window in quota["windows"] if window["id"] == "week")
    assert week["used_percent"] == 80.0  # the empty "premium" record did not wipe the bars
    assert quota["limit"]["reset_text"] == "1:26 AM" and quota["limit"]["source"] == "message"


def test_exhausted_window_is_the_limit_until_it_resets(tmp_path):
    now = int(time.time())
    windows = {"limit_id": "codex",
               "primary": {"used_percent": 100.0, "window_minutes": 300, "resets_at": now + 600},
               "secondary": {"used_percent": 40.0, "window_minutes": 10080, "resets_at": now + 99999}}
    path = tmp_path / "rollout-2026-01-01T00-00-00-y.jsonl"
    path.write_text("\n".join([
        _rec("2026-01-01T00:00:00Z", "session_meta", {"id": "y", "source": "exec"}),
        _rec("2026-01-01T00:01:00Z", "event_msg", {"type": "token_count", "rate_limits": windows}),
    ]) + "\n", encoding="utf-8")
    s = CodexSession(path)
    s.update()
    quota = CodexProvider().quota([s])
    assert quota["limit"]["window"] == "5h" and quota["limit"]["resets_at"] == now + 600
    week = next(window for window in quota["windows"] if window["id"] == "week")
    assert week["used_percent"] == 40.0  # usage keeps showing every window
    # Once the window resets, the bar is back to normal and no limit is reported.
    windows["primary"] = {"used_percent": 100.0, "window_minutes": 300, "resets_at": now - 5}
    path.write_text(path.read_text(encoding="utf-8") + _rec("2026-01-01T05:00:00Z", "event_msg", {
        "type": "token_count", "rate_limits": windows}) + "\n", encoding="utf-8")
    s.update()
    reset_quota = CodexProvider().quota([s])
    assert reset_quota["limit"] is None
    assert next(window for window in reset_quota["windows"] if window["id"] == "5h")["used_percent"] is None
