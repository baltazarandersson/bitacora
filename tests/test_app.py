"""Drive the real Textual app on demo data, headless."""
import time

from rich.cells import cell_len
from textual.widgets import DataTable, RichLog, TabbedContent

from bitacora.providers import all_providers
from bitacora.store import Config, Memory, State
from bitacora.themes import contrast, rule_color
from bitacora.ui import masthead
from bitacora.ui.app import Bitacora, create_app
from bitacora.ui.masthead import BeaconRule
from bitacora.ui.setup import Choice, SetupScreen


def make_app(demo_env, **kw) -> Bitacora:
    memory = Memory()
    for key, entry in demo_env["state"].items():
        memory.remember(key, entry)
    memory.save()
    kw.setdefault("reader", "thread")
    return create_app(all_providers(), Config(), State(), scan_procs=False, refresh_secs=60, **kw)


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
        assert app.q("#runs", DataTable).row_count == 16


async def test_navigation_tabs_and_toggles(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        assert not isinstance(app.screen, SetupScreen)
        table = app.q("#runs", DataTable)
        assert table.row_count == 9
        assert app.current().status == "asking"  # questions first, then live sessions and newest starts
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


async def test_help_is_accessible_and_footer_fits_both_languages(demo_env):
    from textual.containers import VerticalScroll
    from textual.widgets import Button, Static

    from bitacora import i18n
    from bitacora.ui.app import keys_text
    from bitacora.ui.screens import HelpScreen

    for lang in ("en", "es"):
        i18n.set_lang(lang)
        for width in (30, 60, 80, 100, 120, 172):
            text = keys_text(True, False, False, width)
            assert text.cell_len <= width
            assert "?" in text.plain
            assert f"g {i18n.t('key_maximize')}" in text.plain and i18n.t("ft_off") in text.plain
    for lang in ("en", "es"):
        i18n.set_lang(lang)
        app = make_app(demo_env, view="everything")
        async with app.run_test(size=(80, 24)) as pilot:
            await settle(pilot, app)
            table = app.q("#runs", DataTable)
            selected = next(run for run in app.runs if run.label == "auth-cookies")
            table.move_cursor(row=app.row_keys.index(selected.key))
            await pilot.pause(0.1)
            app.action_tab("t-act")
            app.follow = False
            activity_log = app.q("#act", RichLog)
            for i in range(30):
                activity_log.write(f"synthetic help scroll line {i}", scroll_end=False)
                activity_log.scroll_end(animate=False)
                activity_log.scroll_relative(y=-1, animate=False)
                await pilot.pause(0.1)
                detail_scroll = activity_log.scroll_y
            await pilot.press("question_mark")
            assert isinstance(app.screen, HelpScreen)
            body = app.screen.query_one("#help-body", VerticalScroll)
            intro = app.screen.query_one("#help-intro", Static)
            close = app.screen.query_one("#close-help", Button)
            assert body.scroll_y == 0 and body.has_focus
            assert intro.region.y >= body.region.y and intro.region.bottom <= body.region.bottom
            assert not body.region.contains(close.region.x, close.region.y)
            help_keys = app.screen.key_table()
            assert any("Ctrl+P" in str(cell) for cell in help_keys.columns[0]._cells)
            assert any(str(cell).strip() == "g" for cell in help_keys.columns[0]._cells)
            await pilot.press("pagedown")
            assert body.scroll_y > 0 and activity_log.scroll_y == detail_scroll
            await pilot.press("escape")
            assert not app.screen.is_modal
    i18n.set_lang("en")


async def test_table_columns_scroll_and_preserve_detail_state(demo_env):
    from bitacora import i18n

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot, app)
        assert app.COLUMNS == ["status", "agent", "name", "model", "repo", "now", "last", "dur", "ctx", "tok",
                               "cmds", "files", "mode", "pid"]
        table = app.q("#runs", DataTable)
        selected = next(run for run in app.runs if run.label == "auth-cookies")
        table.move_cursor(row=app.row_keys.index(selected.key))
        await pilot.pause(0.1)
        app.action_tab("t-act")
        app.follow = False
        activity_log = app.q("#act", RichLog)
        for i in range(30):
            activity_log.write(f"synthetic resize scroll line {i}", scroll_end=False)
        activity_log.scroll_end(animate=False)
        activity_log.scroll_relative(y=-1, animate=False)
        await pilot.pause(0.1)
        saved_scroll = activity_log.scroll_y
        assert saved_scroll > 0

        app.cycle_sort("name")
        for lang, width in (("en", 80), ("es", 100), ("en", 160), ("es", 200)):
            i18n.set_lang(lang)
            app.relabel()
            await pilot.resize_terminal(width, 24)
            await pilot.pause(0.15)
            assert app.display_columns == app.COLUMNS
            assert [key.value for key in table.columns] == app.COLUMNS
            assert len(app.row_cells(selected)) == 14
            assert all(table.columns[key].label.plain == app.column_label(key).plain for key in app.COLUMNS)
            if width == 80:
                assert table.max_scroll_x > 0
            assert app.sort_column == "name" and app.sort_direction == "asc"
            assert app.selected == selected.key and app.current().label == "auth-cookies"
            assert app.q("#detail", TabbedContent).active == "t-act"
            assert not app.follow and activity_log.scroll_y > 0

        full_model, full_effort = "model-name-with-more-than-thirty-two-characters-" * 2, "very-high"
        session = selected.session
        original_model, original_effort = session.model, session.effort
        session.model, session.effort = full_model, full_effort
        app.show_runs()
        await pilot.pause(0.1)
        assert app.row_cells(selected)[3].plain == f"{full_model} {full_effort}"
        assert table.columns["model"].content_width >= cell_len(f"{full_model} {full_effort}")
        session.model, session.effort = original_model, original_effort
        app.show_runs()

        i18n.set_lang("en")
        app.relabel()
        natural = {
            key: max(app.column_label(key).cell_len, *(cell_len(app.row_cells(run)[i].plain) for run in app.runs))
            for i, key in enumerate(app.COLUMNS)
        }
        natural_width = sum(natural.values()) + 2 * table.cell_padding * len(app.COLUMNS)
        await pilot.resize_terminal(natural_width + 60, 24)
        await pilot.pause(0.15)
        assert all(table.columns[key].width > natural[key] for key in ("name", "now")), \
            (natural_width, table.scrollable_content_region.width,
             {key: (table.columns[key].width, natural[key]) for key in app.COLUMNS})
        assert all(table.columns[key].width == natural[key]
                   for key in app.COLUMNS if key not in ("name", "now"))

        detail = app.info_text(selected).plain
        for label in ("Reasoning effort", "Effort source", "Access kind", "Commands", "Failed commands",
                      "Changed files", "Reported", "What this total counts", "Context used", "PID"):
            assert label in detail
        assert "4" in detail and "2" in detail


async def test_name_and_now_caps_keep_full_values_in_details(demo_env, monkeypatch):
    from rich.text import Text

    from bitacora.util import first_line

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        run = next(r for r in app.runs if r.label == "auth-cookies")
        long_name = "A synthetic run name 界😀 that keeps going " * 4
        full_now = "A synthetic current activity 界😀 that keeps going " * 3
        monkeypatch.setattr("bitacora.ui.app.activity", lambda _session, _run, **_kwargs: Text(full_now))
        run.label = long_name
        cells = app.row_cells(run)
        assert len(cells[2].plain) == 60 and cells[2].plain.endswith("…")
        assert len(cells[5].plain) == 40 and cells[5].plain.endswith("…")
        assert first_line(long_name, 60) in app.selected_rows(run)[0].plain
        detail = app.info_text(run).plain
        assert long_name in detail and full_now in detail


async def test_maximize_list_hides_details_and_restores_focus(demo_env):
    from bitacora.i18n import t

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        selected = app.selected
        tabs = app.q("#detail", TabbedContent)
        app.action_tab("t-info")
        app.q("#info-scroll").focus()
        await pilot.pause(0.1)
        assert app.focused.id == "info-scroll"

        await pilot.press("g")
        await pilot.pause(0.1)
        assert app.list_maximized and app.focused.id == "runs"
        assert not app.q("#hero").display
        assert not tabs.display and app.selected == selected and tabs.active == "t-info"
        footer = app.q("#keys").render().plain
        assert f"g {t('key_maximize')} ● on" in footer

        await pilot.press("g")
        await pilot.pause(0.1)
        assert not app.list_maximized and app.q("#hero").display and tabs.display
        assert app.q("#sel-question").display and app.selected == selected and tabs.active == "t-info"
        assert app.focused.id == "info-scroll"


async def test_setup_tagline_is_outside_scene_and_settings_scroll_at_80x24(demo_env):
    from textual.containers import VerticalScroll
    from textual.widgets import Static

    from bitacora import i18n

    app = make_app(demo_env)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot, app)
        assert isinstance(app.screen, SetupScreen)
        scroll = app.screen.query_one("#setup-scroll", VerticalScroll)
        tagline = app.screen.query_one("#setup-tagline", Static)
        assert "a logbook for your coding agents" in tagline.render().plain
        await pilot.press("tab", "tab", "tab", "tab")
        await pilot.pause(0.1)
        theme = app.screen.query_one("#set-theme")
        assert theme.has_focus
        assert theme.region.y >= scroll.region.y and theme.region.bottom <= scroll.region.bottom
        i18n.set_lang("es")
        app.screen.relabel()
        assert "la bitácora de tus agentes de código" in tagline.render().plain
        i18n.set_lang("en")


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


async def test_status_notifications_cover_the_normalized_transitions(demo_env):
    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        app._prev_status = {run.key: "running" for run in app.all_runs}
        app.notify_changes(app.all_runs)
        await pilot.pause(0.1)
        messages = {str(n.message) for n in app._notifications}
        assert any("waiting for an answer" in message for message in messages)
        assert any("Fix flaky cart widget test finished" in message for message in messages)
        assert any("Split orders into order lines stopped" in message for message in messages)
        assert any("backfill-dedupe stopped" in message for message in messages)
        assert any("flaky-orders stopped" in message for message in messages)


async def test_done_and_stopped_notifications_follow_asking_and_starting(demo_env):
    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        run = next(run for run in app.all_runs if run.label == "auth-cookies")
        for before, turn, expected in (("asking", "complete", "auth-cookies finished"),
                                       ("starting", "stopped", "auth-cookies stopped")):
            app._notifications.clear()
            app._prev_status[run.key] = before
            run.session.state = turn
            app.notify_changes([run])
            await pilot.pause(0.1)
            assert any(expected in str(notice.message) for notice in app._notifications)


async def test_hud_renders_quota(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        text = app.export_screenshot()
        assert "38%" in text and "week" in text and "auth-cookies" in text


async def test_plain_text_switch_covers_messages_and_result(demo_env):
    from textual.widgets import Markdown

    from bitacora.i18n import t

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        run = next(run for run in app.runs if run.session and run.session.messages)
        app.q("#runs", DataTable).move_cursor(row=app.row_keys.index(run.key))
        await pilot.pause(0.1)
        await pilot.press("2", "m")
        await pilot.pause(0.1)
        assert app.plain_text
        assert app.q("#msgs-plain").display and not app.q("#msgs", Markdown).display
        assert run.session.messages[-1][1] in app.q("#msgs-plain").render().plain
        from bitacora.ui.app import keys_text
        assert "m Plain text" in keys_text(app.follow, app.live_only, app.verbose, 240,
                                           app.list_maximized, app.plain_text).plain
        assert t("desktop_plain") in app.q("#msg-tools").render().plain
        assert t("ft_on") in app.q("#msg-tools").render().plain
        await pilot.press("5")
        await pilot.pause(0.1)
        assert app.q("#res-plain").display and not app.q("#res", Markdown).display
        assert t("desktop_plain") in app.q("#res-tools").render().plain
        assert t("ft_on") in app.q("#res-tools").render().plain
        await pilot.press("m")
        await pilot.pause(0.1)
        assert not app.plain_text
        assert app.q("#res", Markdown).display and not app.q("#res-plain").display


async def test_copy_resume_fallback_is_selectable(demo_env, monkeypatch):
    from textual.widgets import TextArea

    from bitacora.ui.screens import CopyResumeScreen

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(60, 20)) as pilot:
        await settle(pilot, app)
        run = next(run for run in app.all_runs if app.resume_cmd(run))
        app.selected = run.key
        command = app.resume_cmd(run)
        def fail_copy(_command):
            raise OSError("clipboard unavailable")
        monkeypatch.setattr(app, "copy_resume_text", fail_copy)
        app.action_copy_resume()
        await pilot.pause(0.1)
        assert isinstance(app.screen, CopyResumeScreen)
        field = app.screen.query_one("#copy-command", TextArea)
        assert field.read_only and field.text == command
        assert not field.selection.is_empty


async def test_long_stop_warning_keeps_cancel_inside_a_narrow_terminal(demo_env):
    from types import SimpleNamespace

    from textual.widgets import Button

    from bitacora.ui.screens import ConfirmKill

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(60, 20)) as pilot:
        await settle(pilot, app)
        run = SimpleNamespace(label="a long session name " * 40, proc=SimpleNamespace(pid=4242))
        app.push_screen(ConfirmKill(run))
        await pilot.pause(0.2)
        for button in app.screen.query(Button):
            assert 0 <= button.region.x < button.region.right <= 60
            assert 0 <= button.region.y < button.region.bottom <= 20
        assert app.screen.query_one("#no", Button).has_focus


async def test_short_home_shrinks_the_scene_before_the_choices(demo_env):
    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(pilot, app)
        await pilot.press("s")
        await pilot.pause(0.2)
        scene = app.screen.query_one("#masthead", masthead.Masthead)
        assert scene.region.height == 6
        scene.tick()  # sample the full lighthouse scene without out-of-bounds drawing
        assert len(scene.strips) == 6
        hint = app.screen.query_one("#setup-hint")
        assert hint.region.bottom <= 30


async def test_details_show_the_source_of_a_running_status(demo_env):
    from bitacora.i18n import t

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        run = next(run for run in app.all_runs if run.session is not None)
        run.session.state = "running"
        run.session.last_ts = time.time()
        detail = app.info_text(run).plain
        assert run.status_source == "transcript"
        assert t("inf_status_source") in detail and t("status_source_transcript") in detail


async def test_setup_changes_are_saved(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        before = app.theme
        await pilot.press("s")
        await pilot.pause(0.1)
        await pilot.press("tab", "tab", "tab", "tab", "right")  # views -> lang -> hours -> notify -> theme
        await pilot.pause(0.3)  # the preview waits for the arrow key to rest
        assert app.theme != before
        await pilot.press("shift+tab", "right")  # notifications off
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, SetupScreen)
        assert app.state.get("theme") == app.theme != before
        assert app.state.get("notify") is False and app.config.notify is False


async def test_theme_then_view_round_trip_keeps_the_saved_and_rendered_theme(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("s")  # home: change the theme and save
        await pilot.pause(0.1)
        await pilot.press("tab", "tab", "tab", "tab", "right")  # views -> lang -> hours -> notify -> theme
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause(0.1)
        theme = app.theme
        assert State().get("theme") == theme != "tokyo-night"
        await pilot.press("s")  # back home for the view
        await pilot.pause(0.1)
        await pilot.press("down", "enter")  # background -> everything, saved
        await pilot.pause(0.1)
        assert app.view_id == State().get("view") == "everything"
        assert State().get("theme") == app.theme == theme


async def test_setup_save_keeps_a_theme_saved_by_another_interface(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        other = State()  # the desktop window saved a theme while this terminal stayed open
        other.set("theme", "nord")
        assert other.save()
        await pilot.press("s")
        await pilot.pause(0.1)
        await pilot.press("enter")  # save the setup as it stands: nothing changed here
        await pilot.pause(0.1)
        assert State().get("theme") == "nord"
        assert app.theme == "nord"


async def test_setup_selectors_share_one_value_width_in_each_language(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("s")
        await pilot.pause(0.1)
        choices = list(app.screen.query(Choice))
        assert [choice.id for choice in choices] == ["set-lang", "set-hours", "set-notify", "set-theme"]
        assert [value for value, _ in app.screen.query_one("#set-hours", Choice).options] == [6, 12, 24, 48, 72]

        def widest() -> int:
            return max(cell_len(text) for choice in choices for text in choice.option_texts())

        def arrows_line_up() -> None:
            # label (24) + "◂ " + value + " ▸": one cell count for every selector, so the arrows share columns.
            assert {choice.value_width for choice in choices} == {widest()}
            assert {choice.render().cell_len for choice in choices} == {24 + 2 + widest() + 2}

        arrows_line_up()
        await pilot.press("tab", "right", "right")  # System -> English -> Español
        await pilot.pause(0.1)
        assert "últimas 72 h" in app.screen.query_one("#set-hours", Choice).option_texts()
        arrows_line_up()  # recomputed for the new language


async def test_language_change_relabels_in_place(demo_env):
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("s")
        await pilot.pause(0.1)
        masthead = app.screen.query_one("Masthead")
        await pilot.press("tab", "right", "right")  # System -> English -> Español
        await pilot.pause(0.1)
        assert app.screen.query_one("Masthead") is masthead  # the scene was not rebuilt
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app.is_running and app.state.get("lang") == "es"
        text = app.export_screenshot()
        assert "Actividad" in text and "Salir" in text and "Estado" in text


async def test_reader_process_delivers_sessions(demo_env):
    app = make_app(demo_env, view="everything", reader="process")
    async with app.run_test(size=(160, 48)) as pilot:
        for _ in range(100):
            await pilot.pause(0.1)
            if app.all_runs is not None:
                break
        assert len(app.all_runs) == 16
        run = next(r for r in app.all_runs if r.label == "auth-cookies")
        assert run.session.n_cmds == 4 and len(run.session.files) == 2
        five_hour = next(window for window in app.source.mirror.quotas["codex"]["windows"]
                         if window["id"] == "5h")
        assert five_hour["used_percent"] == 38.0


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
        assert "Inicio" in app.export_screenshot()
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
        # The theme's green, unless it is too pale to read on this background: then its readable version.
        assert R.GREEN == R.readable(app.get_css_variables()["success"].lower(), R.BG, R.FG)
        assert R.contrast(R.GREEN, R.BG) >= R.TEXT_CONTRAST


def test_scene_frames_by_time_of_day():
    from datetime import datetime

    from bitacora.ui.scene import Scene, daylight, moon_phase
    assert daylight(13)[0] == 1 and daylight(1)[0] == 0 and daylight(19)[1] > 0.9
    assert abs(moon_phase(datetime(2026, 1, 3, 10, 0)) - 0.5) < 0.05  # a full moon
    sc = Scene(170, 16)
    for hour in (2, 6, 13, 19):
        rows = sc.frame(1.0, datetime(2026, 10, 7, hour, 0), "tagline")
        assert len(rows) == 16 and all(sum(len(seg.text) for seg in r) == 170 for r in rows)
    # Sun and moon run the same arc twelve hours apart: at dawn they sit on opposite sides.
    sun, moon = sc.sun_pos(6.6), sc.moon_pos(6.6)
    assert sun[0] < 60 < moon[0]
    assert sc.moon_pos(13) is None and sc.sun_pos(23) is None
    text = "".join(seg.text for r in rows for seg in r)
    assert "tagline" in text


async def test_header_is_adaptive_without_a_fold(demo_env):
    from bitacora.i18n import t
    from bitacora.ui.masthead import Beacon

    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        assert app.q("#hud").display and app.q("#quota").display
        assert all(binding.key != "h" and "toggle_header" not in binding.action for binding in app.BINDINGS)
        beacon = app.q("#beacon", Beacon)
        assert beacon.working
        assert any("running" in text for text, _, _ in beacon.summary)
        assert app.current().label in app.selected_rows(app.current())[0].plain
        assert app.current().label not in str(app.q("#hero").border_title)
        line = screen_lines(app)[0]
        assert "Background runs" in line and t("hud_window", h="24") in line
        assert app.q("#attention").display
        assert not any(t("hud_n_ask", n=1) in text for text, _, _ in beacon.summary)
        await pilot.press("h")
        await pilot.pause(0.1)
        assert app.q("#hud").display and app.q("#quota").display


async def test_short_terminals_keep_view_list_and_compact_hero(demo_env):
    from bitacora.i18n import t

    for size in ((60, 20), (80, 24)):
        app = make_app(demo_env, view="background")
        async with app.run_test(size=size) as pilot:
            await settle(pilot, app)
            assert app.q("#hud").display and not app.q("#quota").display
            table = app.q("#runs")
            assert table.region.height >= 6  # borders, heading and scroll bar still leave two run rows
            hero = app.q("#hero")
            assert hero.has_class("compact")
            assert app.q("#attention").display
            line = screen_lines(app)[0]
            assert t("tui_view_background") in line and t("tui_window_short", h="24") in line
            assert t("tui_live_short") in line
            assert t("hud_n_ask", n=1) not in line
            assert app.current().status == "asking" and app.q("#sel-question").display
            assert t("desktop_plain") in app.q("#msg-tools").render().plain


async def test_palette_offers_home_views_and_language(demo_env):
    from bitacora import i18n

    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        titles = [c.title for c in app.get_system_commands(app.screen)]
        assert "Home and setup" in titles
        assert "View: All" in titles and "Language: Español" in titles
        assert "Sort by Name" in titles and "Sort by Now" in titles and "Reset table sorting" in titles
        i18n.set_lang("es")
        app.relabel()
        titles = [c.title for c in app.get_system_commands(app.screen)]
        assert "Ordenar por Ahora" in titles
        i18n.set_lang("en")
        app.relabel()
        next(c for c in app.get_system_commands(app.screen) if c.title == "View: All").callback()
        await pilot.pause(0.1)
        assert app.view_id == "everything" and app.state.get("view") == "everything"


async def test_table_sort_cycles_and_preserves_selection_tab_and_paused_scroll(demo_env):
    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        table = app.q("#runs", DataTable)
        selected = next(run for run in app.runs if run.label == "a11y-audit")
        table.move_cursor(row=app.row_keys.index(selected.key))
        await pilot.pause(0.1)
        app.action_tab("t-log")
        app.follow = False
        log = app.q("#log", RichLog)
        before_scroll = log.scroll_y

        app.cycle_sort("name")
        assert app.sort_column == "name" and app.sort_direction == "asc"
        assert [run.label for run in app.runs] == sorted((run.label for run in app.runs), key=str.casefold)
        assert app.selected == selected.key and app.current().label == "a11y-audit"
        assert app.q("#detail", TabbedContent).active == "t-log" and not app.follow
        assert log.scroll_y == before_scroll and table.columns["name"].label.plain.endswith("↑")

        app.cycle_sort("name")
        assert app.sort_direction == "desc"
        expected = sorted((run.label for run in app.runs), key=str.casefold, reverse=True)
        assert [run.label for run in app.runs] == expected
        app.cycle_sort("name")
        assert app.sort_column is None and app.sort_direction is None
        assert app.runs[0].status == "asking"

        app.apply(list(app.all_runs))
        await pilot.pause(0.1)
        assert app.selected == selected.key and app.q("#detail", TabbedContent).active == "t-log"
        assert not app.follow and log.scroll_y == before_scroll


async def test_no_color_terminals_render(demo_env, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")  # Textual then applies its monochrome filter to every segment
    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("s")  # open the scene
        await pilot.pause(0.5)
        assert app.is_running


async def test_no_notifications_from_sessions_outside_the_view(demo_env):
    app = make_app(demo_env, view="interactive")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        run = next(r for r in app.all_runs if r.label == "auth-cookies")  # a background run
        assert not app.view.matches(run)
        app._prev_status[run.key] = "running"
        run.session.state = "complete"
        run.session.last_ts -= 3600
        app.notify_changes(app.all_runs)
        await pilot.pause(0.1)
        assert not any("auth-cookies" in str(n.message) for n in app._notifications)


def test_limit_and_reset_read_in_words():
    from bitacora.ui.app import quota_limit_text, quota_window_name, quota_window_text

    assert quota_window_name({"name": "5h"}) == "5h"
    assert quota_window_name({"name": "weekly"}) == "week"
    reset = time.time() + 3600
    assert quota_limit_text({"window": "week", "name": "weekly", "resets_at": reset}).startswith(
        "limit reached · week · available again ")
    assert quota_limit_text({"window": "5h", "name": "5h", "resets_at": reset}).startswith(
        "limit reached · 5h · available again ")
    assert quota_limit_text({"reset_text": "1:26 AM"}) == "limit reached · available again 1:26 AM"
    assert quota_limit_text({"window": "opus-week", "name": "weekly Opus"}) == "limit reached · Opus weekly"
    assert quota_limit_text({}) == "limit reached"
    assert quota_window_text({"name": "5h", "used_percent": None}) == "?"
    stale = {"name": "5h", "used_percent": 84.0, "resets_at": time.time() - 60}
    assert quota_window_text(stale) == "reset"


def test_scene_is_still_under_the_web_driver_unless_asked(monkeypatch):
    from bitacora.ui.masthead import default_scene_fps

    monkeypatch.delenv("BITACORA_SCENE_FPS", raising=False)
    monkeypatch.delenv("TEXTUAL_DRIVER", raising=False)
    assert default_scene_fps() == 15.0
    monkeypatch.setenv("TEXTUAL_DRIVER", "textual.drivers.web_driver:WebDriver")
    assert default_scene_fps() == 10.0  # the window mode: animated, at a lower rate than the terminal
    monkeypatch.setenv("BITACORA_SCENE_FPS", "6")
    assert default_scene_fps() == 6.0


def test_normalized_status_styles_cover_every_status():
    from bitacora.ui import render as R

    expected = {"asking", "running", "starting", "your_turn", "done", "failed", "limited", "stopped",
                "quiet", "unknown"}
    assert set(R.STATUS_STYLE) == expected
    assert R.STATUS_STYLE["limited"] not in {R.STATUS_STYLE["failed"], R.STATUS_STYLE["stopped"]}
    assert R.STATUS_STYLE["quiet"] not in {
        R.STATUS_STYLE["failed"], R.STATUS_STYLE["stopped"], R.STATUS_STYLE["limited"]}


async def test_every_text_color_reads_on_every_theme(demo_env):
    """WCAG AA (4.5:1) for the dim tone and every status color, on each built-in theme the app offers."""
    from bitacora.ui import render as R
    from bitacora.ui.app import THEMES

    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        checked = 0
        for name in THEMES:
            if name not in app.available_themes:
                continue
            app.theme = name
            await pilot.pause(0.05)
            R.apply_theme(app.get_css_variables())
            for label, color in (("dim", R.DIM), ("green", R.GREEN), ("red", R.RED), ("yellow", R.YELLOW),
                                 ("cyan", R.CYAN), ("blue", R.BLUE), ("violet", R.VIOLET)):
                ratio = R.contrast(color, R.BG)
                assert ratio >= R.TEXT_CONTRAST, f"{name}: {label} {color} is {ratio:.2f}:1 on {R.BG}"
            for status, style in R.STATUS_STYLE.items():
                color = style.removeprefix("bold ")
                ratio = R.contrast(color, R.BG)
                assert ratio >= R.TEXT_CONTRAST, f"{name}: {status} {color} is {ratio:.2f}:1 on {R.BG}"
            checked += 1
        assert checked >= 8

def screen_lines(app) -> list[str]:
    """The main screen as text, one row at a time, the way a person reads it."""
    return [strip.text.rstrip() for strip in app.screen._compositor.render_strips()]


def heading_offset(app, column: str) -> tuple[int, int]:
    """Where a click lands on a table heading: past the border, the columns before it and the cell padding."""
    table = app.q("#runs", DataTable)
    before = sum(table.columns[key].get_render_width(table)
                 for key in app.display_columns[:app.display_columns.index(column)])
    return 1 + before + table.cell_padding + 1, 1


async def test_header_click_cycles_ascending_descending_and_default(demo_env):
    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        table = app.q("#runs", DataTable)
        await pilot.click("#runs", offset=heading_offset(app, "name"))
        await pilot.pause(0.1)
        assert app.sort_column == "name" and app.sort_direction == "asc"
        assert table.columns["name"].label.plain.endswith("↑")
        assert [r.label for r in app.runs] == sorted((r.label for r in app.runs), key=str.casefold)
        await pilot.click("#runs", offset=heading_offset(app, "name"))
        await pilot.pause(0.1)
        assert app.sort_direction == "desc" and table.columns["name"].label.plain.endswith("↓")
        await pilot.click("#runs", offset=heading_offset(app, "name"))
        await pilot.pause(0.1)
        assert app.sort_column is None and app.sort_direction is None
        assert not table.columns["name"].label.plain.endswith(("↑", "↓"))
        assert app.runs[0].status == "asking"  # back to questions, live sessions and newest starts


async def test_less_and_greater_step_the_sort_and_ctrl_r_resets(demo_env):
    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        table = app.q("#runs", DataTable)
        await pilot.press("greater_than_sign")
        assert (app.sort_column, app.sort_direction) == ("status", "asc")  # from the default: the first column
        await pilot.press("greater_than_sign")
        assert (app.sort_column, app.sort_direction) == ("agent", "asc")
        await pilot.press("less_than_sign", "less_than_sign")
        assert (app.sort_column, app.sort_direction) == ("pid", "asc")  # past the first column, it wraps round
        assert table.columns["pid"].label.plain.endswith("↑")
        assert not table.columns["agent"].label.plain.endswith(("↑", "↓"))
        await pilot.press("ctrl+r")
        await pilot.pause(0.1)
        assert app.sort_column is None and app.sort_direction is None
        assert app.runs[0].status == "asking"
        assert not table.columns["pid"].label.plain.endswith(("↑", "↓"))


async def test_pending_questions_line_and_its_jumps(demo_env):
    from textual.widgets import Static

    from bitacora.i18n import t
    from bitacora.ui.masthead import Beacon

    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        table = app.q("#runs", DataTable)
        asks = app.questions()
        assert len(asks) == 1 and asks[0].status == "asking"
        from bitacora.ui.render import activity
        question = next(iter(asks[0].session.open_questions.values()))
        assert question in activity(asks[0].session, asks[0], limit=None).plain
        assert t("act_wait_answer") not in activity(asks[0].session, asks[0], limit=None).plain
        row = next(i for i, line in enumerate(screen_lines(app)) if "? 1 question" in line)
        line = screen_lines(app)[row]
        assert asks[0].label in line and "w jump" in line and app.q("#attention", Static).display

        table.move_cursor(row=len(app.row_keys) - 1)  # the last row, which does not ask
        await pilot.pause(0.1)
        assert app.current().status != "asking"
        await pilot.press("w")
        await pilot.pause(0.1)
        assert app.current().key == asks[0].key and table.cursor_row == app.row_keys.index(asks[0].key)

        table.move_cursor(row=len(app.row_keys) - 1)
        await pilot.pause(0.1)
        await pilot.click(offset=(line.index(asks[0].label), row))  # the session's name in the strip
        await pilot.pause(0.1)
        assert app.current().key == asks[0].key

        table.move_cursor(row=len(app.row_keys) - 1)
        await pilot.pause(0.1)
        assert not any(action == "jump_question" for _, _, action in app.q("#beacon", Beacon).hits)


async def test_selected_summary_sits_between_the_table_and_the_tabs(demo_env):
    from textual.widgets import Static

    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        table = app.q("#runs", DataTable)
        box = app.q("#sel-question", Static)
        hero = app.q("#hero")
        tabs = app.q("#detail", TabbedContent)
        assert app.q("#hud").region.bottom <= table.region.y
        assert table.region.bottom <= hero.region.y and hero.region.bottom <= tabs.region.y
        assert app.current().status == "asking" and box.display  # the first run waits for an answer
        assert hero.region.contains(box.region.x, box.region.y)
        assert box.region.bottom <= tabs.region.y
        assert len(app.selected_rows(app.current())) == 4
        table.move_cursor(row=1)
        await pilot.pause(0.1)
        assert app.current().status != "asking" and not box.display
        assert hero.region.bottom <= tabs.region.y


async def test_selected_hero_has_four_unwrapped_rows_in_desktop_order(demo_env):
    from bitacora.i18n import t
    from bitacora.ui.app import started_text
    from bitacora.ui.render import status_text

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        run = app.current()
        rows = app.selected_rows(run)
        assert len(rows) == 4 and all(row.no_wrap for row in rows)
        assert run.label not in str(app.q("#hero").border_title)
        assert run.label in rows[0].plain

        row1 = rows[0].plain
        assert row1.startswith(status_text(run).plain)
        from bitacora.ui.render import agent_table_text, agent_text

        agent = agent_text(run).plain
        assert row1.index(run.label) < row1.index(agent) < row1.index(run.repo)
        table_agent = app.row_cells(run)[app.COLUMNS.index("agent")]
        assert table_agent.plain == agent_table_text(run).plain
        assert table_agent.plain.split(" · ", 1)[0] in {"codex", "claude"}
        assert agent in app.info_text(run).plain
        row2 = rows[1].plain
        row2_labels = [t(key) for key in ("col_model", "col_mode", "inf_started", "col_dur", "col_last")]
        assert [row2.index(label) for label in row2_labels] == sorted(row2.index(label) for label in row2_labels)
        assert started_text(run.start) in row2
        row3 = rows[2].plain
        basis = "processed" if run.provider == "claude" else "reported"
        row3_labels = [t(f"usage_{basis}"), t("hud_context"), t("hud_commands"), t("hud_files")]
        assert [row3.index(label) for label in row3_labels] == sorted(row3.index(label) for label in row3_labels)
        assert rows[3].plain.startswith(t("hud_now") + " ")

        content = app.q("#selected").content
        assert content.columns[0].no_wrap and content.columns[0].overflow == "ellipsis"


async def test_footer_keeps_the_desktop_groups_and_no_sort_keys(demo_env):
    from bitacora.i18n import t
    from bitacora.ui.app import keys_text

    app = make_app(demo_env, view="background")
    async with app.run_test(size=(240, 48)) as pilot:
        await settle(pilot, app)
        footer = keys_text(app.follow, app.live_only, app.verbose, 240).plain
        assert "Ctrl+P" not in footer and "Ctrl+R" not in footer
        assert all("Ctrl+P" not in line for line in screen_lines(app))
        core = keys_text(app.follow, app.live_only, app.verbose, 30).plain
        assert cell_len(core) <= 30 and "g" in core and "?" in core
        order = [footer.index(part) for part in ("move", "Auto-scroll", "Result", "Home")]
        assert order == sorted(order)  # nav, switches, actions, then home, help and quit
        assert "m Plain text" in footer
        assert "f Auto-scroll ● on" in footer and "a Live only ○ off" in footer
        assert f"g {t('key_maximize')} ○ off" in footer
        maximized = keys_text(app.follow, app.live_only, app.verbose, 200, True).plain
        assert f"g {t('key_maximize')} ● on" in maximized
        footer = app.screen._compositor.render_strips()[app.q("#keys").region.y]
        assert not any(segment.style and segment.style.underline for segment in footer)


async def test_header_uses_one_rule_and_backgrounded_question_chips(demo_env, monkeypatch):
    from bitacora.i18n import t
    from bitacora.ui import masthead
    from bitacora.ui.masthead import BeaconRule
    from bitacora.util import first_line

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        beacon = app.q("#beacon")
        line1 = beacon.render_line(0).text
        assert line1.startswith("▲ bitácora")
        assert line1.index("updated") > 100 and line1.index("Live only") > line1.index("updated")
        rule = app.q("#beacon-rule", BeaconRule)
        line = rule.render_line(0)
        assert line.text == "─" * rule.size.width
        assert all(segment.style and not segment.style.underline for segment in line._segments)
        # The beam moves with time: render it at two clock readings a third of a pass apart.
        clock = [rule.t0 + 0.2]
        monkeypatch.setattr(masthead, "time", type("Clock", (), {"monotonic": staticmethod(lambda: clock[0])}))
        before = [str(segment.style.color) for segment in rule.render_line(0)._segments]
        clock[0] += rule.PASS / 3
        after = [str(segment.style.color) for segment in rule.render_line(0)._segments]
        monkeypatch.undo()
        assert before != after
        screen = screen_lines(app)
        assert sum(bool(value.strip()) and set(value.strip()) == {"─"} for value in screen) == 1

        asks = app.questions()
        assert asks
        chips = app.attention_grid([asks[0], asks[0]]).columns[0]._cells[0]
        assert chips.plain.endswith("w " + t("ft_jump"))
        assert chips.plain.count(first_line(asks[0].label, 40)) == 2
        oldest = t("desktop_oldest")
        end_oldest = chips.plain.index(oldest) + len(oldest)
        next_name = chips.plain.index(first_line(asks[0].label, 40), end_oldest)
        assert chips.plain[end_oldest:next_name] == " "
        name_offset = chips.plain.index(first_line(asks[0].label, 40))
        assert chips.get_style_at_offset(app.console, name_offset).bgcolor is not None
        attention = app.screen._compositor.render_strips()[app.q("#attention").region.y]
        assert not any(segment.style and segment.style.underline for segment in attention)


async def test_narrow_spanish_brand_keeps_view_window_and_reader_status(demo_env):
    from bitacora import i18n
    from bitacora.i18n import t
    from bitacora.util import fmt_ago

    i18n.set_lang("es")
    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(pilot, app)
        await pilot.pause(0.1)
        line = app.screen._compositor.render_strips()[0].text
        assert "▲ bitácora" in line
        assert "Todas" in line
        assert (t("hud_window", h="24") in line or t("tui_window_short", h="24") in line)
        assert ("actualizado hace" in line or app.q("#beacon").tooltip)
        assert (t("key_live") in line or t("tui_live_short") in line)

        run = app.current()
        assert run.session is not None
        run.session.model = "modelo-muy-largo-" * 8
        run.session.effort = "alto"
        run.session.last_ts = time.time() - 754
        app.render_selected()
        await pilot.pause(0.1)
        selected = app.q("#selected")
        hero_row2 = screen_lines(app)[selected.region.y + 1]
        label = t("col_last")
        assert label in hero_row2 and "…" in hero_row2
        value = hero_row2.split(label, 1)[1].lstrip()
        assert value.startswith(fmt_ago(run.session.last_ts)[:3])
        assert selected.region.height == 4
    i18n.set_lang("en")


async def test_top_line_reports_reader_health_and_switches_live_only(demo_env):
    import re

    from bitacora.ui.masthead import Beacon

    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        beacon = app.q("#beacon", Beacon)

        def texts() -> list[str]:
            return [text for text, _, _ in beacon.right_summary]

        assert re.search(r"updated \S+ ago", "".join(texts()))
        app.source.last_update = time.monotonic() - 3600  # no new data for an hour
        app.render_hud()
        await pilot.pause(0.1)
        assert texts()[:2] == ["● ", "out of date"]  # reader health leads the right-side status
        app.source.last_update = time.monotonic()
        app.source.error = "reader crashed"
        app.render_hud()
        await pilot.pause(0.1)
        assert texts()[:2] == ["● ", "read error"] and "read error" in screen_lines(app)[0]
        app.source.error = None
        app.render_hud()
        await pilot.pause(0.1)

        assert not app.live_only
        start = next(begin for begin, _, action in beacon.hits if action == "toggle_live")
        await pilot.click("#beacon", offset=(start + 1, 0))
        await pilot.pause(0.1)
        assert app.live_only
        start = next(begin for begin, _, action in beacon.hits if action == "toggle_live")
        await pilot.click("#beacon", offset=(start + 1, 0))
        await pilot.pause(0.1)
        assert not app.live_only


async def test_highlights_read_on_every_theme(demo_env):
    """Text on each highlight the terminal draws reaches WCAG AA (4.5:1) on every theme the app offers: the selected
    row and the home list's cursor, the primary and error buttons, the active tab and every waiting chip."""
    from rich.style import Style

    from bitacora.ui import render as R
    from bitacora.ui.app import THEMES

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        assert app.questions(), "the demo data has a session waiting for an answer"
        checked = 0
        for name in THEMES:
            if name not in app.available_themes:
                continue
            app.theme = name
            await pilot.pause(0.05)
            v = app.get_css_variables()
            pairs = [("selected row", v["block-cursor-background"], v["block-cursor-foreground"]),
                     ("primary button", v["primary"], v["ink-primary"]),
                     ("error button", v["error"], v["ink-error"]),
                     ("active tab", v["background"], v["foreground"])]
            for span in app.attention_text(app.questions()).spans:
                style = Style.parse(span.style) if isinstance(span.style, str) else span.style
                if style.bgcolor is not None and style.color is not None:
                    pairs.append(("waiting chip", style.bgcolor.get_truecolor().hex, style.color.get_truecolor().hex))
            for label, fill, ink in pairs:
                ratio = contrast(ink, fill)
                assert ratio >= R.TEXT_CONTRAST, f"{name}: {label} {ink} on {fill} is {ratio:.2f}:1"
            checked += 1
        assert checked >= 8


def strip_colors(strip) -> list[str]:
    """The foreground colour of every cell in a rendered rule line."""
    colors = []
    for segment in strip:
        color = segment.style.color.get_truecolor().hex if segment.style and segment.style.color else ""
        colors.extend([color] * len(segment.text))
    return colors


def distance(a: str, b: str) -> int:
    return sum((int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) ** 2 for i in (1, 3, 5))


async def test_home_rows_keep_three_columns_and_never_wrap(demo_env):
    from bitacora.i18n import t
    from bitacora.ui.setup import view_name
    from bitacora.views import counts

    app = make_app(demo_env, view="background")
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("s")
        await pilot.pause(0.1)
        screen = app.screen
        assert isinstance(screen, SetupScreen)
        views = screen.query_one("#setup-views")
        name_w = max(cell_len(view_name(v)) for v in app.views)
        for width in (120, 100, 80, 56, 44):
            await pilot.resize_terminal(width, 48)
            await pilot.pause(0.25)
            lines = [views.get_option_at_index(i).prompt.plain for i in range(views.option_count)]
            detail = "\n".join(f"{cell_len(line)} {line!r}" for line in lines)
            assert len({cell_len(line) for line in lines}) == 1, detail  # one width: every column lines up
            assert cell_len(lines[0]) <= views.scrollable_content_region.width, (width, lines[0])
            shown = [strip.text.rstrip() for strip in app.screen._compositor.render_strips()]
            for line in lines:  # each row is drawn on one line, counts included
                assert any(line.rstrip() in text for text in shown), (width, line)
            for view, line in zip(app.views, lines, strict=True):
                if width >= 80:  # the name column, as wide as the longest name
                    assert line[2:2 + name_w].rstrip() == view_name(view), line
                else:  # too narrow for all of it: the name gives way first, with an ellipsis
                    assert line[2:].startswith(view_name(view)[:4]), line
                shown, live = counts(view, app.all_runs)
                assert line.endswith(t("picker_counts", n=shown, live=live)), line  # counts end the row
            if width == 44:  # the descriptions are cut with an ellipsis, never wrapped
                assert any("…" in line for line in lines), lines


async def test_divider_is_still_when_nothing_runs_and_beams_while_sessions_run(demo_env, monkeypatch):
    from bitacora.ui import render as R

    app = make_app(demo_env, view="everything")
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot, app)
        rule = app.q("#beacon-rule", BeaconRule)
        assert rule.size.height == 1 and rule.size.width == 120  # one line, the full width, no layout shift
        muted = rule_color(R.BG, R.FG)
        assert contrast(muted, R.BG) >= 1.6
        rule.set_state(False, False)
        still = strip_colors(rule.render_line(0))
        assert set(still) == {muted} and len(still) == 120

        variables = app.get_css_variables()
        accent, warning = variables["accent"], variables["warning"]
        rule.set_state(True, False)  # sessions run: a bright run of cells moves through the still colour, in accent
        beam = strip_colors(rule.render_line(0))
        assert len(set(beam)) > 2
        brightest = max(beam, key=lambda c: distance(c, muted))
        assert distance(brightest, accent) < distance(brightest, warning)

        rule.set_state(True, True)  # a session waits for an answer: the same beam, in the theme's warning colour
        amber = strip_colors(rule.render_line(0))
        brightest = max(amber, key=lambda c: distance(c, muted))
        assert distance(brightest, warning) < distance(brightest, accent)

        clock = [100.0]
        monkeypatch.setattr(masthead, "time", type("Clock", (), {"monotonic": staticmethod(lambda: clock[0])}))
        rule.t0 = 100.0
        clock[0] = 101.5
        first = strip_colors(rule.render_line(0))
        clock[0] = 101.5 + BeaconRule.PASS  # one pass later the beam is where it was
        assert strip_colors(rule.render_line(0)) == first and set(first) != {muted}
        rule.set_state(False, False)
        assert set(strip_colors(rule.render_line(0))) == {muted}
