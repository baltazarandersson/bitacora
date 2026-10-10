from types import SimpleNamespace

from bitacora.sorting import (
    DESKTOP_SORT_COLUMNS,
    RUN_COLUMNS,
    SORT_COLUMNS,
    TEXTUAL_SORT_COLUMNS,
    cycle_sort,
    sort_runs,
    step_sort,
)


def make_run(key, *, status="done", live=False, start=1, label="", duration=None,
             last=None, commands=None, files=None, tokens=None, context=None, context_window=None,
             provider="codex", origin="?", origin_kind="other", model="model", effort=None, repo="repo",
             sandbox="workspace-write",
             pid=None, activity_text=None):
    session = None
    if any(value is not None for value in (last, commands, files, tokens, context, context_window, activity_text)):
        session = SimpleNamespace(
            last_ts=last,
            n_cmds=commands,
            files={str(index): None for index in range(files or 0)},
            tokens=tokens,
            context_tokens=context,
            ctx_used=context,
            ctx_window=context_window,
            open_questions={},
            in_flight={},
            last_event=SimpleNamespace(kind="cmd", text=activity_text) if activity_text else None,
        )
    return SimpleNamespace(
        key=key,
        status=status,
        live=live,
        start=start,
        label=label,
        duration=duration,
        provider=provider,
        origin=origin,
        origin_kind=origin_kind,
        repo=repo,
        model=model,
        effort=effort,
        sandbox=sandbox,
        session=session,
        proc=SimpleNamespace(pid=pid) if pid is not None else None,
    )


def test_both_interfaces_share_the_same_ordered_sort_columns():
    expected = ("status", "agent", "name", "model", "repo", "now", "last", "dur", "ctx", "tok", "cmds", "files",
                "mode", "pid")
    assert RUN_COLUMNS == expected
    assert TEXTUAL_SORT_COLUMNS == DESKTOP_SORT_COLUMNS == SORT_COLUMNS == frozenset(expected)


def test_default_order_is_shared_and_deterministic():
    runs = [
        make_run("z", status="done", start=30),
        make_run("q", status="asking", start=1),
        make_run("live-old", status="running", live=True, start=2),
        make_run("live-new", status="running", live=True, start=4),
        make_run("a", status="done", start=30),
        make_run("no-start", status="unknown", start=None),
    ]
    assert [run.key for run in sort_runs(runs)] == [
        "q", "live-new", "live-old", "a", "z", "no-start",
    ]


def test_numeric_and_timestamp_sorting_keep_unknown_last_in_both_directions():
    runs = [
        make_run("ten", duration=10, last=100, commands=10, tokens=10, context=10),
        make_run("unknown", duration=None, last=None, commands=None, tokens=None, context=None),
        make_run("two", duration=2, last=2, commands=2, tokens=2, context=2),
    ]
    assert [run.key for run in sort_runs(runs, "dur", "asc")] == ["two", "ten", "unknown"]
    assert [run.key for run in sort_runs(runs, "dur", "desc")] == ["ten", "two", "unknown"]
    assert [run.key for run in sort_runs(runs, "last", "asc")] == ["two", "ten", "unknown"]
    assert [run.key for run in sort_runs(runs, "cmds", "desc")] == ["ten", "two", "unknown"]
    assert [run.key for run in sort_runs(runs, "tok", "asc")] == ["two", "ten", "unknown"]
    assert [run.key for run in sort_runs(runs, "ctx", "desc")] == ["ten", "two", "unknown"]
    file_runs = [make_run("ten-files", files=10), make_run("two-files", files=2), make_run("unknown-files")]
    assert [run.key for run in sort_runs(file_runs, "files", "asc")] == [
        "two-files", "ten-files", "unknown-files",
    ]

    with_pids = [make_run("pid-20", pid=20), make_run("pid-4", pid=4), make_run("pid-unknown")]
    assert [run.key for run in sort_runs(with_pids, "pid", "asc")] == ["pid-4", "pid-20", "pid-unknown"]

    contexts = [make_run("half", context=50, context_window=100),
                make_run("quarter", context=25, context_window=100),
                make_run("zero", context=0, context_window=100),
                make_run("zero-window", context=1, context_window=0),
                make_run("missing-window", context=1)]
    assert [run.key for run in sort_runs(contexts, "ctx", "asc")] == [
        "zero", "quarter", "half", "missing-window", "zero-window",
    ]


def test_agent_sorts_by_provider_and_origin_text_and_model_includes_effort():
    runs = [
        make_run("codex-other", provider="codex", origin="future", origin_kind="other"),
        make_run("claude-other", provider="claude", origin="future", origin_kind="other"),
        make_run("codex-background", provider="codex", origin="exec", origin_kind="background"),
    ]
    assert [run.key for run in sort_runs(runs, "agent", "asc")] == [
        "claude-other", "codex-background", "codex-other",
    ]

    models = [
        make_run("high", model="gpt-6-luna", effort="high"),
        make_run("low", model="gpt-6-luna", effort="low"),
        make_run("other", model="gpt-6-astra", effort="max"),
    ]
    assert [run.key for run in sort_runs(models, "model", "asc")] == ["other", "high", "low"]


def test_agent_sort_uses_the_localized_short_table_label():
    from bitacora import i18n
    from bitacora.ui.render import agent_table_text

    runs = [
        make_run("desktop", provider="codex", origin_kind="desktop"),
        make_run("background", provider="codex", origin_kind="background"),
    ]
    try:
        i18n.set_lang("en")
        assert agent_table_text(runs[0]).plain == "codex · Desktop"
        assert [run.key for run in sort_runs(runs, "agent", "asc")] == ["background", "desktop"]
        i18n.set_lang("es")
        assert agent_table_text(runs[0]).plain == "codex · Escritorio"
        assert [run.key for run in sort_runs(runs, "agent", "asc")] == ["desktop", "background"]
    finally:
        i18n.set_lang("en")


def test_total_sort_uses_cumulative_usage_and_context_sort_uses_latest_context():
    runs = [
        make_run("small-total-low-context", tokens=100, context=10, context_window=100),
        make_run("large-total-high-context", tokens=1000, context=90, context_window=100),
    ]
    assert [run.key for run in sort_runs(runs, "tok", "asc")] == [
        "small-total-low-context", "large-total-high-context",
    ]
    assert [run.key for run in sort_runs(runs, "ctx", "desc")] == [
        "large-total-high-context", "small-total-low-context",
    ]


def test_context_sort_uses_percentages_and_keeps_unknown_windows_last():
    runs = [
        make_run("half", context=100_000, context_window=200_000),
        make_run("nearly-full", context=190_000, context_window=200_000),
        make_run("unknown-window", context=20_000),
        make_run("missing-context"),
    ]
    assert [run.key for run in sort_runs(runs, "ctx", "asc")] == [
        "half", "nearly-full", "missing-context", "unknown-window",
    ]
    assert [run.key for run in sort_runs(runs, "ctx", "desc")] == [
        "nearly-full", "half", "missing-context", "unknown-window",
    ]


def test_now_sorts_by_the_full_activity_text():
    runs = [
        make_run("z", status="running", live=True, last=1, activity_text="zebra"),
        make_run("a", status="running", live=True, last=1, activity_text="Alpha"),
    ]
    assert [run.key for run in sort_runs(runs, "now", "asc")] == ["a", "z"]


def test_text_sort_is_casefolded_and_status_unknown_is_last():
    runs = [make_run("b", status="running", label="beta"), make_run("a", label="Alpha"),
            make_run("u", status="unknown")]
    assert [run.key for run in sort_runs(runs, "name", "asc")] == ["a", "b", "u"]
    assert [run.key for run in sort_runs(runs, "status", "desc")] == ["a", "b", "u"]  # rank: done after running


def test_status_sort_uses_the_declared_rank():
    statuses = ("asking", "running", "starting", "quiet", "your_turn", "limited", "failed", "stopped", "done",
                "unknown")
    runs = [make_run(status, status=status) for status in reversed(statuses)]
    assert [run.status for run in sort_runs(runs, "status", "asc")] == list(statuses)


def test_equal_sort_values_keep_the_deterministic_default_tie_order():
    runs = [make_run("z", label="same"), make_run("a", label="same")]
    assert [run.key for run in sort_runs(runs, "name", "asc")] == ["a", "z"]
    assert [run.key for run in sort_runs(runs, "name", "desc")] == ["a", "z"]


def test_sort_cycle_returns_to_default_and_new_column_starts_ascending():
    assert cycle_sort(None, None, "name") == ("name", "asc")
    assert cycle_sort("name", "asc", "name") == ("name", "desc")
    assert cycle_sort("name", "desc", "name") == (None, None)
    assert cycle_sort("name", "desc", "last") == ("last", "asc")
    assert cycle_sort("name", "asc", "not-a-column") == ("name", "asc")
    for column in RUN_COLUMNS:
        assert cycle_sort(None, None, column) == (column, "asc")


def test_less_and_greater_step_through_the_shown_columns_and_wrap():
    order = RUN_COLUMNS
    assert step_sort(None, order, 1, order) == ("status", "asc")  # from the default, > starts at the first column
    assert step_sort(None, order, -1, order) == ("pid", "asc")  # and < at the last one
    assert step_sort("status", order, 1, order) == ("agent", "asc")
    assert step_sort("status", order, -1, order) == ("pid", "asc")
    assert step_sort("dur", order, 1, order) == ("ctx", "asc")
    # The helper still handles a caller that supplies a subset of visible columns.
    shown = ["status", "name"]
    assert step_sort("status", shown, 1, order) == ("name", "asc")
    assert step_sort("agent", shown, 1, order) == ("name", "asc")
    assert step_sort("agent", shown, -1, order) == ("status", "asc")
    assert step_sort("name", shown, 1, order) == ("status", "asc")
    assert step_sort("name", [], 1, order) is None
