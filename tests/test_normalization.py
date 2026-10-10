import json
import time

import pytest

from bitacora import i18n
from bitacora.model import ProcInfo, Run
from bitacora.providers.claude import ClaudeProvider, ClaudeSession, context_window_for_model
from bitacora.providers.codex import CodexProvider, CodexSession
from bitacora.util import access_kind


def _from_demo(home, sid, session_type):
    path = next(path for path in home.rglob("*.jsonl") if sid in path.name)
    session = session_type(path)
    session.update()
    return session


def _claude_session(path, *, entrypoint="cli", model="claude-sonnet-5-5", permission=None):
    user = {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "sessionId": "synthetic",
            "cwd": "/repo", "entrypoint": entrypoint, "message": {"role": "user", "content": "prompt"}}
    if permission is not None:
        user["permissionMode"] = permission
    assistant = {"type": "assistant", "timestamp": "2026-01-01T00:00:01Z", "sessionId": "synthetic",
                 "cwd": "/repo", "entrypoint": entrypoint,
                 "message": {"id": "m1", "role": "assistant", "model": model,
                             "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn",
                             "usage": {"input_tokens": 100, "cache_creation_input_tokens": 20,
                                       "cache_read_input_tokens": 30, "output_tokens": 10}}}
    path.write_text("\n".join(json.dumps(row) for row in (user, assistant)) + "\n", encoding="utf-8")
    session = ClaudeSession(path)
    session.update()
    return session


def _codex_session(path, *, source="exec", originator="codex_exec"):
    row = {"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta",
           "payload": {"id": "synthetic", "timestamp": "2026-01-01T00:00:00Z", "cwd": "/repo",
                       "source": source, "originator": originator}}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    session = CodexSession(path)
    session.update()
    return session


def test_usage_metrics_keep_context_and_cumulative_totals_separate(demo_env):
    codex = _from_demo(demo_env["codex_home"], "0199a1b2-0000-7000-8000-000000000001", CodexSession)
    claude = ClaudeSession(demo_env["live"][1].path)
    claude.update()

    assert codex.context_tokens == codex.ctx_used == 58_500
    assert codex.context_window == codex.ctx_window == 272_000
    assert codex.tokens == 109_000 and codex.usage_basis == "reported"
    assert claude.context_tokens == 40_604
    assert claude.context_window == 1_000_000 and claude.context_window_assumed
    assert claude.tokens == 7 * (4 + 40_000 + 600 + 300)
    assert claude.usage_basis == "processed"
    assert claude.files["CHANGELOG.md"][0:2] == ["update", 2]
    assert claude.n_cmds == 1  # Bash executes; Read remains an activity event
    edit = next(event for event in claude.events if event.kind == "file" and event.plus == 3)
    assert (edit.plus, edit.minus) == (3, 2)


@pytest.mark.parametrize(("model", "expected"), [
    ("claude-haiku-4-5", 200_000),
    ("claude-sonnet-4-5", 200_000),
    ("claude-sonnet-4-20250514", 200_000),
    ("claude-opus-4-20250514", 200_000),
    ("claude-opus-4-5[1m]", 1_000_000),
    ("claude-sonnet-4-6", 1_000_000),
    ("claude-fable-5-1", 1_000_000),
    ("claude-mythos-5-2", 1_000_000),
    ("claude-unknown-6-0", None),
])
def test_claude_context_window_table(model, expected):
    assert context_window_for_model(model) == expected


def test_unknown_claude_model_keeps_context_count_without_an_assumed_window(tmp_path):
    session = _claude_session(tmp_path / "unknown.jsonl", model="claude-unknown-6-0")
    assert session.context_tokens == 150
    assert session.context_window is None
    assert not session.context_window_assumed


def test_demo_has_claude_45_and_5x_context_windows(demo_env):
    older = _from_demo(demo_env["claude_home"], "5f0c2a1e-0000-4000-8000-000000000009", ClaudeSession)
    newer = ClaudeSession(demo_env["live"][1].path)
    newer.update()
    assert older.context_window == 200_000 and older.context_window_assumed
    assert newer.context_window == 1_000_000 and newer.context_window_assumed


def test_codex_stale_window_has_unknown_usage_and_type_only_limit_has_a_name(demo_env):
    stale = _from_demo(demo_env["codex_home"], "0199a1b2-0000-7000-8000-000000000004", CodexSession)
    quota = CodexProvider().quota([stale])
    assert quota["reporting"] == "windows"
    assert len(quota["windows"]) == 1
    assert quota["windows"][0]["used_percent"] is None
    assert quota["windows"][0]["resets_at"] < time.time()

    type_only = _from_demo(demo_env["codex_home"], "0199a1b2-0000-7000-8000-000000000007", CodexSession)
    assert type_only.limit_info == {"type": "primary"}
    limit = CodexProvider().quota([type_only])["limit"]
    assert limit is not None and limit["name"] == "5h" and limit["window"] == "5h"
    assert "primary" not in json.dumps(limit).casefold()


def test_codex_type_only_limit_without_a_window_uses_limit_only_reporting(tmp_path):
    path = tmp_path / "type-only.jsonl"
    session = _codex_session(path)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        row = {"timestamp": "2026-01-01T00:00:01Z", "type": "event_msg",
               "payload": {"type": "token_count", "info": None,
                           "rate_limits": {"rate_limit_reached_type": "primary"}}}
        stream.write(json.dumps(row) + "\n")
    session = CodexSession(path)
    session.update()
    quota = CodexProvider().quota([session])
    assert quota["reporting"] == "limit-only" and quota["windows"] == []
    assert quota["limit"]["name"] is None and "primary" not in json.dumps(quota["limit"]).casefold()


def test_claude_quota_uses_shared_shape_and_provider_window_name(demo_env):
    session = _from_demo(demo_env["claude_home"], "5f0c2a1e-0000-4000-8000-000000000008", ClaudeSession)
    quota = ClaudeProvider().quota([session])
    assert quota["reporting"] == "limit-only" and quota["windows"] == []
    assert quota["limit"]["window"] == "sonnet-week"
    assert quota["limit"]["name"] == "weekly Sonnet"
    assert quota["limit"]["scope"] == "model:sonnet"
    assert "seven_day_sonnet" not in json.dumps(quota)


def test_provider_and_origin_labels_are_localized(demo_env):
    from bitacora.ui.app import quota_window_name
    from bitacora.ui.render import agent_table_text, agent_text

    session = ClaudeSession(demo_env["live"][1].path)
    session.update()
    run = Run("claude", "claude", session, None, "synthetic")
    assert agent_text(run).plain == "Claude Code · Background"
    assert agent_table_text(run).plain == "claude · Background"
    assert quota_window_name({"name": "weekly Sonnet"}) == "Sonnet weekly"
    i18n.set_lang("es")
    assert agent_text(run).plain == "Claude Code · Segundo plano"
    assert agent_table_text(run).plain == "claude · Segundo plano"
    assert quota_window_name({"name": "weekly Sonnet"}) == "semana de Sonnet"
    i18n.set_lang("en")


def test_agent_table_labels_stay_short_while_full_labels_keep_provider_names():
    from types import SimpleNamespace

    from bitacora.ui.render import agent_table_text, agent_text

    kinds = {
        "terminal": ("Terminal", "Terminal", "Terminal", "Terminal"),
        "background": ("Background", "Segundo plano", "Background", "Segundo plano"),
        "desktop": ("Desktop", "Escritorio", "Desktop app", "App de escritorio"),
        "ide": ("IDE", "IDE", "IDE", "IDE"),
        "subagent": ("Subagent", "Subagente", "Subagent", "Subagente"),
        "other": ("Other", "Otro", "Other", "Otro"),
    }
    try:
        for lang, short_provider, full_provider in (
            ("en", "codex", "Codex"),
            ("es", "codex", "Codex"),
            ("en", "claude", "Claude Code"),
            ("es", "claude", "Claude Code"),
        ):
            i18n.set_lang(lang)
            index = 0 if lang == "en" else 1
            full_index = index + 2
            provider = "codex" if short_provider == "codex" else "claude"
            for kind, labels in kinds.items():
                run = Run(
                    f"{provider}-{kind}", provider,
                    SimpleNamespace(origin_kind=kind, origin="?"), None, "synthetic",
                )
                assert agent_table_text(run).plain == f"{short_provider} · {labels[index]}"
                assert agent_text(run).plain == f"{full_provider} · {labels[full_index]}"
    finally:
        i18n.set_lang("en")


@pytest.mark.parametrize(("source", "originator", "expected"), [
    ("exec", "codex_exec", "background"),
    (None, "codex_exec", "background"),
    ({"subagent": {"other": "guardian"}}, "codex_subagent", "subagent"),
    ("vscode", "Codex Desktop", "desktop"),
    ("cli", "codex-tui", "terminal"),
    ("vscode", "codex", "ide"),
    ("future", "codex", "other"),
])
def test_codex_origin_kinds_keep_raw_source(source, originator, expected, tmp_path):
    session = _codex_session(tmp_path / "codex.jsonl", source=source, originator=originator)
    assert session.origin_kind == expected
    if isinstance(source, str):
        raw = originator if "desktop" in originator.casefold() or originator.casefold() == "codex-tui" else source
        assert session.origin == raw


@pytest.mark.parametrize(("entrypoint", "expected"), [
    ("cli", "terminal"),
    ("sdk-cli", "background"),
    ("sdk-ts", "background"),
    ("sdk-py", "background"),
    ("claude-desktop", "desktop"),
    ("claude-vscode", "ide"),
    ("claude-jetbrains", "ide"),
    ("future-entrypoint", "other"),
])
def test_claude_origin_kinds_keep_raw_entrypoint(entrypoint, expected, tmp_path):
    session = _claude_session(tmp_path / "claude.jsonl", entrypoint=entrypoint)
    assert session.origin_kind == expected
    assert session.origin == entrypoint


@pytest.mark.parametrize(("raw", "expected"), [
    ("default", "prompt"),
    ("acceptEdits", "auto-edit"),
    ("plan", "plan"),
    ("bypassPermissions", "full-access"),
    ("unseen-mode", "other"),
])
def test_claude_access_modes_keep_kind_and_raw_value(raw, expected, tmp_path):
    session = _claude_session(tmp_path / "claude.jsonl", permission=raw)
    assert session.access_kind == expected
    assert session.sandbox == raw
    assert access_kind(raw, "claude") == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("read-only", "read-only"),
    ("workspace-write", "workspace-write"),
    ("danger-full-access", "full-access"),
    ("unseen-mode", "other"),
])
def test_codex_access_modes_map_to_shared_kinds(raw, expected):
    assert access_kind(raw, "codex") == expected


def test_transcript_model_effort_and_access_win_over_launch_values(demo_env):
    session = _from_demo(demo_env["codex_home"], "0199a1b2-0000-7000-8000-000000000001", CodexSession)
    process = ProcInfo("codex", 1, time.time(), ["codex"], model="launch-model", effort="max",
                       sandbox="read-only")
    run = Run("codex", "codex", session, process, "demo")
    assert run.model == session.model == "gpt-6-luna"
    assert run.effort == session.effort == "high"
    assert run.effort_source == "transcript"
    assert run.sandbox == "workspace-write" and run.access_kind == "workspace-write"

    claude = ClaudeSession(demo_env["live"][1].path)
    claude.update()
    process = ProcInfo("claude", 2, time.time(), ["claude"], model="opus", effort="max",
                       sandbox="acceptEdits")
    run = Run("claude", "claude", claude, process, "demo")
    assert run.model == "claude-opus-5-5"
    assert run.effort == "max" and run.effort_source == "launch"
    assert run.access_kind == "auto-edit" and run.sandbox == "acceptEdits"


def test_claude_reads_are_events_and_edit_errors_do_not_count_as_command_failures(tmp_path):
    path = tmp_path / "tools.jsonl"
    records = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "sessionId": "tools", "cwd": "/repo",
         "entrypoint": "cli", "message": {"role": "user", "content": "go"}},
    ]
    for tid, name, inp, is_error, result in (
        ("r1", "Read", {"file_path": "/repo/a.py"}, False, "result"),
        ("g1", "Grep", {"pattern": "value", "path": "/repo"}, False, "result"),
        ("b1", "Bash", {"command": "pytest"}, False, "result"),
        ("e1", "Edit", {"file_path": "/repo/a.py", "old_string": "x", "new_string": "y"}, True, "tool error"),
        ("e2", "Edit", {"file_path": "/repo/a.py", "old_string": "p", "new_string": "q"}, False,
         "text says failed without an error flag"),
        ("m1", "MultiEdit", {"file_path": "/repo/a.py", "edits": [
            {"old_string": "a\nb\n", "new_string": "a\nc\nd\n"},
            {"old_string": "x\n", "new_string": "y\nz\n"},
        ]}, False, "result"),
    ):
        records.append({"type": "assistant", "timestamp": "2026-01-01T00:00:01Z", "sessionId": "tools",
                        "cwd": "/repo", "message": {"id": tid, "model": "claude-sonnet-5-5",
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
                        "stop_reason": "tool_use", "usage": {}}})
        records.append({"type": "user", "timestamp": "2026-01-01T00:00:02Z", "sessionId": "tools",
                        "cwd": "/repo", "entrypoint": "cli", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": is_error}]}})
    path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
    session = ClaudeSession(path)
    session.update()
    assert session.n_cmds == 1 and session.n_fail == 0
    assert any(event.kind == "tool" and event.sub == "read" for event in session.events)
    assert any(event.kind == "tool" and event.sub == "search" for event in session.events)
    assert any(event.kind == "cmd" and event.text == "pytest" for event in session.events)
    assert any(event.kind == "err" and event.sub == "Edit" and event.text == "Edit a.py"
               for event in session.events)
    diff = next(event for event in session.events if event.kind == "file" and event.plus == 4)
    assert (diff.plus, diff.minus) == (4, 2)
