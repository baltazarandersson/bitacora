from bitacora.collector import Collector
from bitacora.providers import all_providers
from bitacora.store import State
from bitacora.views import BUILTIN, all_views, counts, custom_view


def collect(demo_env):
    state = State()
    for key, entry in demo_env["state"].items():
        state.remember(key, entry)
    c = Collector(all_providers(), state, hours=24, scan_procs=False)
    return c, {r.label: r for r in c.collect()}


def test_statuses_and_labels(demo_env):
    c, runs = collect(demo_env)
    assert {label: r.status for label, r in runs.items()} == {
        "auth-cookies": "active",
        "pg17-plan": "ask",
        "a11y-audit": "done",
        "flaky-orders": "aborted",
        "Landing hero copy": "done",
        "Update CHANGELOG.md for the 2.4.0 relea…": "active",
        "Review PR 42: rate limiter": "done",
        "Fix login redirect loop": "done",
    }
    assert runs["a11y-audit"].out.endswith("a11y-audit.result.md")
    assert runs["auth-cookies"].log.endswith("auth-cookies.log")
    assert runs["auth-cookies"].origin == "exec"
    assert runs["Review PR 42: rate limiter"].origin == "desktop"
    assert c.quotas["codex"]["primary"]["used_percent"] == 38.0


def test_live_sessions_sort_first(demo_env):
    c, _ = collect(demo_env)
    ordered = c.collect()
    live = [r.live for r in ordered]
    assert live == sorted(live, reverse=True)
    assert sum(live) == 3


def test_history_window(demo_env):
    state = State()
    c = Collector(all_providers(), state, hours=1, scan_procs=False)
    labels = {r.label for r in c.collect()}
    assert "Fix login redirect loop" not in labels  # 7h old
    assert len(labels) == 3


def test_builtin_view_counts(demo_env):
    _, runs = collect(demo_env)
    rs = list(runs.values())
    got = {v.id: counts(v, rs) for v in BUILTIN}
    assert got == {"background": (5, 3), "everything": (8, 3), "interactive": (3, 0), "live": (3, 3)}


def test_custom_views():
    v = custom_view("mine", {"name": "Codex bg", "providers": ["codex"], "kind": "background"})
    assert v.providers == ("codex",) and v.headless is True and not v.builtin
    assert custom_view("bad", {"providers": "codex"}) is None
    views = all_views({"mine": {"name": "x"}, "live": {"name": "shadowed"}})
    assert [x.id for x in views] == ["background", "everything", "interactive", "live", "mine"]
