"""Drive the real Textual app on demo data, headless."""
from textual.widgets import DataTable, RichLog, TabbedContent

from bitacora.providers import all_providers
from bitacora.store import Config, State
from bitacora.ui.app import Bitacora, create_app
from bitacora.ui.setup import SetupScreen


def make_app(demo_env, **kw) -> Bitacora:
    state = State()
    for key, entry in demo_env["state"].items():
        state.remember(key, entry)
    return create_app(all_providers(), Config(), state, scan_procs=False, refresh_secs=60, **kw)


async def settle(pilot, app):
    for _ in range(50):
        await pilot.pause(0.05)
        if app.all_runs is not None:
            break
    await pilot.pause(0.1)


async def test_first_run_setup_then_view(demo_env):
    app = make_app(demo_env)
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        assert isinstance(app.screen, SetupScreen)
        await pilot.press("down", "enter")  # Everything
        await pilot.pause(0.1)
        assert app.view_id == "everything"
        assert app.state.get("view") == "everything"
        assert app.q("#runs", DataTable).row_count == 8


async def test_navigation_tabs_and_toggles(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        assert not isinstance(app.screen, SetupScreen)
        table = app.q("#runs", DataTable)
        assert table.row_count == 5
        assert app.current().label.startswith("Update CHANGELOG")  # newest live session first
        assert app.q("#act", RichLog).lines
        for key in "123456":
            await pilot.press(key)
        assert app.q("#detail", TabbedContent).active == "t-info"
        await pilot.press("v", "a")
        assert app.verbose and app.live_only
        await pilot.pause(0.1)
        assert table.row_count == 3
        await pilot.press("a")
        await pilot.pause(0.1)
        table.move_cursor(row=[r.label for r in app.runs].index("a11y-audit"))
        await pilot.pause(0.1)
        assert app.current().label == "a11y-audit"
        await pilot.press("k")  # not running: only a warning
        assert not app.screen.is_modal
        await pilot.press("s")
        assert isinstance(app.screen, SetupScreen)
        await pilot.press("escape")
        assert app.view_id == "background"


async def test_status_change_notifies(demo_env):
    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        run = next(r for r in app.all_runs if r.label == "auth-cookies")
        app._prev_status[run.key] = "running"
        run.session.state = "complete"
        run.session.last_ts -= 3600
        app.notify_changes(app.all_runs)
        await pilot.pause(0.1)
        assert any("auth-cookies finished" in str(n.message) for n in app._notifications)


async def test_hud_renders_quota(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        text = app.export_screenshot()
        assert "38%" in text and "week" in text and "auth-cookies" in text


async def test_setup_changes_are_saved(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        before = app.theme
        await pilot.press("s")
        await pilot.pause(0.1)
        await pilot.press("tab", "tab", "tab", "tab", "right")  # views -> lang -> hours -> notify -> theme
        assert app.theme != before  # live preview
        await pilot.press("shift+tab", "right")  # notifications off
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, SetupScreen)
        assert app.state.get("theme") == app.theme != before
        assert app.state.get("notify") is False and app.config.notify is False


async def test_language_change_relaunches(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("s")
        await pilot.pause(0.1)
        await pilot.press("tab", "right", "right")  # System -> English -> Español
        await pilot.press("enter")
        await pilot.pause(0.1)
    assert app.return_value == "relaunch"
    assert app.state.get("lang") == "es"


def test_bindings_follow_the_language(demo_env):
    from bitacora import i18n
    i18n.set_lang("es")
    app = make_app(demo_env)
    assert any(b.description == "Salir" for b in type(app).BINDINGS)
    assert isinstance(app, Bitacora)


async def test_setup_arrows_flow_from_views_to_settings(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("s")
        await pilot.pause(0.1)
        await pilot.press("down", "down", "down", "down")  # through the four views and out
        assert app.focused.id == "set-lang"
        await pilot.press("up")
        assert app.focused.id == "setup-views"


async def test_language_previews_at_once_and_esc_reverts(demo_env):
    from bitacora import i18n
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("s")
        await pilot.pause(0.1)
        await pilot.press("tab", "right", "right")  # System -> English -> Español
        await pilot.pause(0.2)
        assert i18n.lang() == "es"
        assert "Configuración" in app.export_screenshot()
        assert app.focused.id == "set-lang"
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert i18n.lang() == "en"


async def test_colors_follow_the_theme(demo_env):
    from bitacora.ui import render as R
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        dark_bg = R.BG
        app.theme = "textual-light"
        await pilot.pause(0.2)
        assert R.BG != dark_bg and not R.DARK
        assert R.GREEN == app.get_css_variables()["success"].lower()


def test_masthead_frame():
    from bitacora.ui.masthead import LEVELS, Masthead, Palette
    m = Masthead()
    m._layout(170, 16)
    m.pal = Palette()
    strips = m._draw(170, 16)
    assert len(strips) == 16 and all(s.cell_length == 170 for s in strips)
    text = "".join(seg.text for s in strips for seg in s)
    assert "▟███▙" in text and "█████" in text
    assert len(m.pal.rain[0]) == LEVELS


async def test_header_folds_and_summary_moves_to_the_beacon(demo_env):
    from bitacora.ui.masthead import Beacon
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        beacon = app.q("#beacon", Beacon)
        assert app.q("#hud").display and not beacon.summary
        assert beacon.working  # two demo runs are active, so the beam sweeps
        await pilot.press("h")
        await pilot.pause(0.1)
        assert not app.q("#hud").display
        assert any("running" in text for text, _ in beacon.summary)
        assert app.state.get("header") is False
        await pilot.click("#beacon")
        await pilot.pause(0.1)
        assert app.q("#hud").display


async def test_short_terminal_folds_the_header(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 26)) as pilot:
        await settle(pilot, app)
        assert not app.q("#hud").display


async def test_palette_offers_home_views_and_language(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        titles = [c.title for c in app.get_system_commands(app.screen)]
        assert "Home and setup" in titles
        assert "View: Everything" in titles and "Language: Español" in titles
        next(c for c in app.get_system_commands(app.screen) if c.title == "View: Everything").callback()
        await pilot.pause(0.1)
        assert app.view_id == "everything" and app.state.get("view") == "everything"
