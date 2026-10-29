"""The Textual app. Every label is looked up at draw time, so switching language never restarts anything."""
from __future__ import annotations

import asyncio
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from rich.cells import cell_len
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import DataTable, Markdown, RichLog, Static, TabbedContent, TabPane

from bitacora import i18n, procs
from bitacora.i18n import t
from bitacora.model import MAX_EVENTS, Run
from bitacora.providers import Provider
from bitacora.sorting import RUN_COLUMNS, sort_runs, step_sort
from bitacora.sorting import cycle_sort as next_sort
from bitacora.store import Config, State, capped_hours
from bitacora.themes import DEFAULT_THEME, THEMES, ink_on
from bitacora.ui import render as R
from bitacora.ui.masthead import Beacon, BeaconRule
from bitacora.ui.render import (
    activity,
    agent_table_text,
    agent_text,
    bar,
    pct_style,
    render_event,
    status_text,
    visible,
)
from bitacora.ui.screens import ConfirmKill, CopyResumeScreen, HelpScreen
from bitacora.ui.setup import Settings, SetupScreen, view_name
from bitacora.util import first_line, fmt_ago, fmt_dur, fmt_tokens, hhmm
from bitacora.views import DEFAULT_VIEW, View, all_views
from bitacora.worker import Source


def cell_key(value) -> tuple:
    """What a table cell looks like, to skip updates that would change nothing."""
    if isinstance(value, Text):
        return value.plain, str(value.style), tuple((sp.start, sp.end, str(sp.style)) for sp in value.spans)
    return (str(value),)


def cell_width(value: object) -> int:
    return value.cell_len if isinstance(value, Text) else cell_len(str(value))


def when_text(ts: float, mins: int = 0) -> str:
    """A reset time: the clock for today's windows, day and clock for anything further away."""
    when = datetime.fromtimestamp(ts)
    far = mins >= 1440 if mins else when.timestamp() - datetime.now().timestamp() >= 86400
    return when.strftime("%d/%m %H:%M" if far else "%H:%M")


def started_text(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts is not None else "-"


def provider_label(provider: str) -> str:
    key = str(provider).lower().replace("_", "-")
    if key in ("claude", "claude-code"):
        return t("provider_claude")
    if key == "codex":
        return t("provider_codex")
    return str(provider)


def usage_basis(session, provider: str) -> str:
    basis = getattr(session, "usage_basis", None)
    if basis not in ("processed", "reported"):
        basis = "processed" if str(provider).lower().startswith("claude") else "reported"
    return basis


def context_tokens(session) -> int | None:
    if session is None:
        return None
    return session.context_tokens if hasattr(session, "context_tokens") else getattr(session, "ctx_used", None)


def effort_source(session, run: Run) -> str:
    source = getattr(session, "effort_source", "unknown") if session else "unknown"
    if source in ("transcript", "launch"):
        return source
    if run.provider == "codex" and session and getattr(session, "effort", None):
        return "transcript"
    if run.proc and getattr(run.proc, "effort", None):
        return "launch"
    if session and getattr(session, "effort", None):
        return "transcript"
    return "unknown"


def access_kind(session, run: Run) -> str:
    value = getattr(session, "access_kind", None) if session else None
    allowed = ("read-only", "workspace-write", "prompt", "auto-edit", "plan", "full-access", "other")
    if value not in allowed:
        from bitacora.util import access_kind as normalize_access
        value = normalize_access(getattr(session, "sandbox", None) if session else run.sandbox, run.provider)
    return value if value in allowed else "other"


def context_window_assumed(session, run: Run) -> bool:
    if getattr(session, "context_window_assumed", False):
        return True
    if run.provider == "claude":
        from bitacora.providers.claude import context_window_for_model
        return context_window_for_model(run.model) is not None
    return False


def format_context_tokens(value: int | None) -> str:
    return "-" if value is None else "0" if value == 0 else fmt_tokens(value)


def translated_kind(value: str | None, values: tuple[str, ...], prefix: str) -> str:
    kind = value if value in values else "other"
    return t(f"{prefix}_{kind.replace('-', '_')}")


def quota_name_label(name, window=None) -> str:
    labels = {"5h": "quota_five_hour", "week": "quota_week", "weekly": "quota_week",
              "weekly Opus": "quota_week_opus", "weekly Sonnet": "quota_week_sonnet"}
    key = labels.get(name) if isinstance(name, str) else None
    key = key or (labels.get(window) if isinstance(window, str) else None)
    return t(key) if key else ""


def quota_window_name(window: dict) -> str:
    return quota_name_label(window.get("name")) or t("quota_window_unknown")


def quota_timestamp(value) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def quota_window_text(window: dict) -> str:
    resets_at = quota_timestamp(window.get("resets_at"))
    if resets_at is not None and resets_at <= time.time():
        return t("quota_reset")
    pct = window.get("used_percent")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not math.isfinite(pct):
        return t("quota_value_unknown")
    return f"{pct:.0f}%"


def quota_limit_text(limit: dict) -> str:
    name = quota_name_label(limit.get("name"), limit.get("window"))
    resets_at = quota_timestamp(limit.get("resets_at"))
    if resets_at is not None:
        if resets_at <= time.time():
            return t("quota_limit_reset")
        when = when_text(resets_at)
    else:
        reset_text = limit.get("reset_text")
        when = reset_text if isinstance(reset_text, str) else ""
    if when and name:
        return t("quota_limit_until", type=name, when=when)
    if when:
        return t("quota_limit_when", when=when)
    if name:
        return t("quota_limit_type", type=name)
    return t("quota_limit")


def question_text(run: Run) -> str:
    """What the session waits on: its open question(s), or the last one it asked, on one line."""
    s = run.session
    if s is None:
        return ""
    if s.open_questions:
        text = " | ".join(s.open_questions.values())
    else:
        ask = next((ev for ev in reversed(s.events) if ev.kind == "ask"), None)
        text = (ask.detail or ask.text) if ask else ""
    return " ".join(text.split())


FIRST_PAINT = 150  # events drawn when a session is selected
PAINT_BATCH = 12  # events drawn per frame
LOG_ERR = re.compile(r"\b(error|failed|panic)\b", re.I)
RES_READ_LIMIT = 64_000  # bytes of a result file painted at most
LOG_INITIAL = 64_000  # bytes of a log painted when the tab first shows it
LOG_READ_LIMIT = 128_000  # bytes of a log painted per incremental pass


def keys_text(follow: bool, live_only: bool, verbose: bool, width: int, list_maximized: bool = False,
              plain_text: bool = False) -> Text:
    """The footer preserves navigation and help as space allows."""
    def state(on: bool) -> Text:
        return Text(f"● {t('ft_on')}", style=f"bold {R.GREEN}") if on else Text(f"○ {t('ft_off')}", style=R.DIM)

    def hint(key: str, label: str | Text) -> Text:
        tx = Text(key, style=f"bold {R.CYAN}")
        actions = {"Enter": "focus_detail", "Esc": "focus_table", "s": "setup", "?": "help",
                   "q": "quit", "f": "toggle_follow", "a": "toggle_live", "v": "toggle_verbose",
                   "m": "toggle_plain",
                   "g": "toggle_maximize_list", "o": "open_result", "l": "open_log", "k": "kill",
                   "c": "copy_resume"}
        if key in actions:
            tx.apply_meta({"@click": f"app.{actions[key]}"})
        tx.append(" ")
        tx.append_text(Text(label, style=R.DIM) if isinstance(label, str) else label)
        return tx

    def switch(key: str, label: str, on: bool) -> Text:
        tx = hint(key, label + " ")
        tx.append_text(state(on))
        tx.append(" ")  # a switch ends in its state; the extra space keeps it from touching the next key
        return tx

    # The desktop's groups in its order. Sorting keys are documented in help, not here.
    groups = {
        "nav": [hint("↑↓", t("ft_move")), hint("Enter", t("ft_detail")), hint("1–6", t("ft_tabs")),
                hint("Esc", t("ft_list"))],
        "switch": [switch("f", t("key_follow"), follow), switch("a", t("key_live"), live_only),
                   switch("v", t("key_verbose"), verbose), switch("m", t("desktop_plain"), plain_text),
                   switch("g", t("key_maximize"), list_maximized)],
        "act": [hint("o", t("key_result")), hint("l", t("key_log")), hint("k", t("key_kill")),
                hint("c", t("key_copy"))],
        "app": [hint("s", t("key_home")), hint("?", t("desktop_help")), hint("q", t("key_quit"))],
    }
    # Drop optional actions and other switches before navigation. Help remains available.
    for drop in ("act", "switch", "nav", None):
        line = _join([g for g in groups.values() if g])
        if line.cell_len <= max(1, width - 2):
            return line
        if drop == "switch":
            groups["switch"] = [switch("g", t("key_maximize"), list_maximized)]
        elif drop is not None:
            groups.pop(drop)
    line = hint("g", t("key_maximize"))
    line.append(" ")
    line.append_text(state(list_maximized))
    line.append("  ")
    line.append_text(hint("?", t("desktop_help")))
    line.truncate(max(1, width), overflow="ellipsis")
    return line


def _join(groups: list[list[Text]]) -> Text:
    tx = Text()
    for i, group in enumerate(groups):
        if i:
            tx.append(" │ ", style=R.DIM)
        for j, item in enumerate(group):
            if j:
                tx.append(" ")
            tx.append_text(item)
    return tx


def bindings() -> list[Binding]:
    return [
        Binding("s", "setup", show=False),
        Binding("question_mark,f1", "help", show=False),
        Binding("g", "toggle_maximize_list", show=False),
        Binding("a", "toggle_live", show=False),
        Binding("v", "toggle_verbose", show=False),
        Binding("m", "toggle_plain", show=False),
        Binding("k", "kill", show=False),
        Binding("c", "copy_resume", show=False),
        Binding("q", "quit", show=False),
        Binding("p", "setup", show=False),
        Binding("f", "toggle_follow", show=False),
        Binding("o", "open_result", show=False),
        Binding("l", "open_log", show=False),
        Binding("t", "next_theme", show=False),
        Binding("r", "refresh_now", show=False),
        Binding("ctrl+r", "reset_sort", show=False),
        Binding("less_than_sign", "sort_step(-1)", show=False),
        Binding("greater_than_sign", "sort_step(1)", show=False),
        Binding("w", "jump_question", show=False),
        Binding("1", "tab('t-act')", show=False),
        Binding("2", "tab('t-msg')", show=False),
        Binding("3", "tab('t-files')", show=False),
        Binding("4", "tab('t-log')", show=False),
        Binding("5", "tab('t-res')", show=False),
        Binding("6", "tab('t-info')", show=False),
        Binding("escape", "focus_table", "key_list", show=False),
        Binding("pageup", "scroll_detail('page_up')", show=False, priority=True),
        Binding("pagedown", "scroll_detail('page_down')", show=False, priority=True),
        Binding("shift+up", "scroll_detail('up')", show=False, priority=True),
        Binding("shift+down", "scroll_detail('down')", show=False, priority=True),
        Binding("shift+home", "scroll_detail('home')", show=False, priority=True),
        Binding("shift+end", "scroll_detail('end')", show=False, priority=True),
    ]


class HintLine(Static):
    auto_links = False  # Keep click metadata without Textual's automatic link colors and underline.


class RunTable(DataTable):
    def on_resize(self) -> None:
        self.call_after_refresh(self.app.size_run_columns)


class Bitacora(App):
    TITLE = "Bitácora"
    CSS = """
    Screen { background: $background; }
    #beacon { background: $surface; }  /* opaque: $boost is translucent and renders black in a web view */
    #beacon:hover { background: $panel; }
    #hud { height: auto; padding: 0 1; background: $surface; }
    #quota, #attention { height: 1; }
    #attention { display: none; }
    #hero { height: auto; min-height: 6; padding: 0 1; border: round $panel-lighten-2; background: $surface; }
    #hero.compact { min-height: 4; }
    #selected { height: 4; padding: 0; background: $surface; }
    #hero.compact #selected { height: 1; }
    #hero.compact.has-now #selected { height: 2; }
    #sel-question { display: none; height: auto; max-height: 4; padding: 0; background: $surface; }
    #hero.compact #sel-question { height: 1; max-height: 1; }
    #keys { height: 1; background: $panel; }
    .pane-scroll { height: 1fr; background: $background; }
    #msgs-plain, #res-plain { display: none; padding: 0 1; background: $background; }
    #msg-tools, #res-tools { height: 1; padding: 0 1; background: $panel; }
    #detail RichLog, .pane-scroll { border: blank; }
    #detail RichLog:focus, .pane-scroll:focus, #files:focus { border: round $accent; }
    #runs { height: auto; max-height: 40%; min-height: 6; border: round $panel-lighten-2; }
    #runs.maximized { height: 1fr; max-height: 100%; }
    #runs:focus { border: round $accent; }
    #detail { height: 1fr; }
    #detail RichLog, #detail Markdown, #info { padding: 0 1; background: $background; }
    #files { border: none; }
    ConfirmKill { align: center middle; }
    #dialog { grid-size: 2; grid-columns: 1fr 1fr; grid-gutter: 0 2; padding: 1 2;
              width: 90%; max-width: 64; height: auto; max-height: 100%;
              border: thick $error; background: $surface; }
    #question { column-span: 2; width: 1fr; content-align: center middle;
                height: auto; min-height: 2; max-height: 8; text-wrap: wrap; overflow-y: auto; }
    #dialog Button { width: 100%; }
    Button.-primary { color: $ink-primary; }  /* the default colour is read from the screen, not the button */
    Button.-error { color: $ink-error; }
    """
    BINDINGS = bindings()
    COLUMNS = list(RUN_COLUMNS)

    def __init__(self, providers: list[Provider], config: Config, state: State, view: str | None = None,
                 live_only: bool = False, scan_procs: bool = True, refresh_secs: float = 2.0,
                 lang: str = "auto", reader: str = "process"):
        super().__init__()
        self.lang_setting = lang
        self.providers = providers
        self.config = config
        self.state = state
        self.source = Source(config.hours, scan_procs, refresh_secs, process=reader == "process")
        # Start before Textual takes over stdin/stdout: on Linux and macOS a spawned process inherits them.
        self.source.start()
        self.views = all_views(config.views)
        ids = [v.id for v in self.views]
        self.first_run = view is None and state.first_run
        chosen = view or state.get("view") or DEFAULT_VIEW
        self.view_id = chosen if chosen in ids else DEFAULT_VIEW
        self.live_only = live_only
        self.refresh_secs = refresh_secs
        self.all_runs: list[Run] | None = None
        self.runs: list[Run] = []
        self.row_keys: list[str] = []
        self.selected: str | None = None
        self.display_columns: list[str] = []
        self.list_maximized = False
        self._list_focus_id: str | None = None
        self.sort_column: str | None = None
        self.sort_direction: str | None = None
        self.follow = True
        self.verbose = False
        self.tick = 0
        self._shown: dict[str, tuple] = {}
        self._prev_status: dict[str, str] = {}
        self._setup: SetupScreen | None = None
        self._cells: dict[str, list] = {}
        self._stale = False
        self._detail_timer = None
        self._paint_timer = None
        self.scene_time: str | None = None  # fixed hour for the home scene (--time), for previews
        self.plain_text = False
        self.compact_hero = False
        self.short_quota = False
        self._compact_reader = False

    @property
    def view(self) -> View:
        return next(v for v in self.views if v.id == self.view_id)

    def compose(self) -> ComposeResult:
        yield Beacon(id="beacon")
        with Vertical(id="hud"):
            yield Static(id="quota")
            yield HintLine(id="attention")
        yield BeaconRule(id="beacon-rule")
        with Vertical():
            yield RunTable(id="runs", cursor_type="row", zebra_stripes=True)
            with Vertical(id="hero"):
                yield Static(id="selected")
                yield Static(id="sel-question")
            with TabbedContent(id="detail", initial="t-act"):
                with TabPane(t("tab_act"), id="t-act"):
                    yield RichLog(id="act", wrap=True, max_lines=MAX_EVENTS * 2, markup=False)
                with TabPane(t("tab_msg"), id="t-msg"):
                    yield HintLine(id="msg-tools")
                    with VerticalScroll(id="msgs-scroll", classes="pane-scroll"):
                        yield Markdown(id="msgs")
                        yield Static(id="msgs-plain", markup=False)
                with TabPane(t("tab_files"), id="t-files"):
                    yield DataTable(id="files", cursor_type="row", zebra_stripes=True)
                with TabPane(t("tab_log"), id="t-log"):
                    yield RichLog(id="log", wrap=True, max_lines=2000, markup=False)
                with TabPane(t("tab_res"), id="t-res"):
                    yield HintLine(id="res-tools")
                    with VerticalScroll(id="res-scroll", classes="pane-scroll"):
                        yield Markdown(id="res")
                        yield Static(id="res-plain", markup=False)
                with TabPane(t("tab_info"), id="t-info"):
                    with VerticalScroll(id="info-scroll", classes="pane-scroll"):
                        yield Static(id="info")
        yield HintLine(id="keys")

    def get_css_variables(self) -> dict[str, str]:
        """Textual's variables, plus the text color for each highlight, readable on that highlight (themes.ink_on).

        Textual sets the block cursor's text from the screen background, so a bright primary got unreadable text.
        The cursor (table rows, the home list), the primary and error buttons each take their own ink here.
        """
        variables = super().get_css_variables()
        text = [variables["background"], variables["foreground"]]
        variables["block-cursor-foreground"] = ink_on(variables["block-cursor-background"], text)
        variables["ink-primary"] = ink_on(variables["primary"], text)
        variables["ink-error"] = ink_on(variables["error"], text)
        return variables

    def on_mount(self) -> None:
        theme = self.state.get("theme")
        self.theme = theme if theme in self.available_themes else DEFAULT_THEME
        R.apply_theme(self.get_css_variables())
        self.theme_changed_signal.subscribe(self, self.theme_changed)
        self.build_columns()
        self.q("#runs", DataTable).focus()
        for err in self.config.errors:
            self.notify(t("n_config", e=err), severity="warning", timeout=10)
        self.render_hud()
        self.set_interval(0.1, self.poll_source)
        self.set_interval(1, self.clock)
        if self.first_run:
            self.action_setup()

    def build_columns(self) -> None:
        table = self.q("#runs", DataTable)
        table.clear(columns=True)
        self.display_columns = list(self.COLUMNS)
        for key in self.display_columns:
            table.add_column(self.column_label(key), key=key)
        self.row_keys = []
        self._cells.clear()
        ft = self.q("#files", DataTable)
        ft.clear(columns=True)
        for key, label in (("kind", t("fcol_kind")), ("plus", "+"), ("minus", "−"), ("n", t("fcol_n")),
                           ("ts", t("fcol_ts")), ("path", t("fcol_path"))):
            ft.add_column(label, key=key)
        self._shown.clear()

    def size_run_columns(self, rows: dict[str, list] | None = None) -> None:
        """Size each column to its current header and cells, then share spare width with Name and Now."""
        if not self.screen_stack:
            return
        table = self.q("#runs", DataTable)
        if not table.columns:
            return
        rows = rows if rows is not None else {run.key: self.row_cells(run) for run in self.runs}
        widths = {key: cell_width(self.column_label(key)) for key in self.COLUMNS}
        for values in rows.values():
            for key, value in zip(self.COLUMNS, values, strict=True):
                widths[key] = max(widths[key], cell_width(value))
        natural = widths.copy()
        padding = 2 * table.cell_padding * len(self.COLUMNS)
        extra = max(0, table.scrollable_content_region.width - padding - sum(widths.values()))
        if extra:
            name_extra = extra * 4 // 7
            widths["name"] += name_extra
            widths["now"] += extra - name_extra
        changed = False
        for key in self.COLUMNS:
            column = table.columns[key]
            new = (False, widths[key], natural[key])
            old = (column.auto_width, column.width, column.content_width)
            if old != new:
                column.auto_width = False
                column.width = widths[key]
                column.content_width = natural[key]
                changed = True
        if changed:
            table._require_update_dimensions = True
            table.check_idle()
            table.refresh(layout=True)

    def relabel(self) -> None:
        """Redraw every label in the current language, in place."""
        tabs = self.q("#detail", TabbedContent)
        for pane, key in (("t-act", "tab_act"), ("t-msg", "tab_msg"), ("t-files", "tab_files"),
                          ("t-log", "tab_log"), ("t-res", "tab_res"), ("t-info", "tab_info")):
            tabs.get_tab(pane).label = t(key)
        self.build_columns()
        self.show_runs()
        self.render_detail(force=True)

    def column_label(self, key: str) -> Text:
        label = self.column_name(key)
        if key == self.sort_column:
            return Text(f"{label} {'↓' if self.sort_direction == 'desc' else '↑'}")
        return Text(f"{label}  ")  # reserve the sort marker's width in both languages

    @staticmethod
    def column_name(key: str) -> str:
        return t(f"col_{key}")

    def update_sort_headers(self) -> None:
        table = self.q("#runs", DataTable)
        for key in self.display_columns:
            table.columns[key].label = self.column_label(key)
        table.refresh()

    def cycle_sort(self, column: str) -> None:
        self.sort_column, self.sort_direction = next_sort(self.sort_column, self.sort_direction, column)
        self.update_sort_headers()
        self.show_runs()

    def action_sort_step(self, step: int) -> None:
        """< and > move the sort to the neighbouring column on screen, starting ascending."""
        move = step_sort(self.sort_column, self.display_columns or self.COLUMNS, step, self.COLUMNS)
        if move:
            self.sort_column, self.sort_direction = move
            self.update_sort_headers()
            self.show_runs()

    def action_reset_sort(self) -> None:
        if self.sort_column is None:
            return
        self.sort_column = self.sort_direction = None
        self.update_sort_headers()
        self.show_runs()

    def theme_changed(self, _theme) -> None:
        if not self.screen_stack:
            return
        R.apply_theme(self.get_css_variables())
        if len(self.screen_stack) > 1:  # the main screen is hidden: redraw it when it comes back
            self._stale = True
            return
        self.redraw_all()

    def redraw_all(self) -> None:
        self._stale = False
        self.relabel()
        self.render_hud()
        self.q("#beacon-rule", BeaconRule).refresh()  # its still line takes the new theme's colours

    def q(self, selector: str, expect_type=None):
        """Query the main screen even while a dialog is on top."""
        if not self.screen_stack:  # shutting down or relaunching
            raise NoMatches(selector)
        base = self.screen_stack[0]
        return base.query_one(selector, expect_type) if expect_type else base.query_one(selector)

    def clock(self) -> None:
        """Once a second: ages and durations. Animation lives in the beacon, which redraws on its own."""
        self.tick += 1
        try:
            self.render_hud()
            health = self.source.health
            if health in ("error", "stale") and health != getattr(self, "_health_notice", None):
                key = "desktop_reader_short_error" if health == "error" else "desktop_reader_short_stale"
                self.notify(t(key), severity="warning", timeout=10)
            self._health_notice = health
        except NoMatches:
            pass

    # ---- header
    def working(self) -> bool:
        return self.all_runs is None or any(r.status in ("running", "asking", "starting") for r in self.runs)

    def on_resize(self, event) -> None:
        if self.screen_stack:
            self.call_after_refresh(self.size_run_columns)
            self.render_hud()
        self.render_keys()

    @on(Beacon.Clicked)
    def beacon_clicked(self, ev: Beacon.Clicked) -> None:
        if ev.action == "jump_question":
            self.action_jump_question()
        elif ev.action == "toggle_live":
            self.action_toggle_live()
        elif ev.action == "setup":
            self.action_setup()

    def summary(self) -> list[tuple[str, str, str | None]]:
        """Compact counts first; keep the current view and history window on every terminal width."""
        stats = self.stats()
        try:
            width = self.q("#beacon").size.width or self.size.width
        except NoMatches:
            width = self.size.width
        asks_on_line = self.questions_line_visible()
        hours = f"{self.config.hours:g}"
        full_window = t("hud_live_only") if self.live_only else t("hud_window", h=hours)
        short_window = t("tui_live_short") if self.live_only else t("tui_window_short", h=hours)
        count_labels = {
            "running": "hud_n_running", "asking": "hud_n_ask", "starting": "hud_n_starting",
            "your_turn": "hud_n_idle", "done": "hud_n_done", "failed": "hud_n_failed",
            "limited": "hud_n_limited", "stopped": "hud_n_cut", "quiet": "hud_n_quiet",
        }
        count_styles = {
            "running": f"bold {R.GREEN}", "asking": f"bold {R.YELLOW}", "starting": R.CYAN,
            "your_turn": R.BLUE, "done": R.GREEN, "failed": R.RED, "limited": R.PURPLE,
            "stopped": R.RED, "quiet": R.DIM,
        }
        glyphs = {
            "running": "\u25cf", "asking": "?", "starting": "\u25cb", "your_turn": "\u25cc",
            "done": "\u2713", "failed": "\u2717", "limited": "\u231b", "stopped": "\u23f9",
            "quiet": "\u25f7",
        }
        order = ("running", "asking", "starting", "your_turn", "done", "failed", "limited", "stopped", "quiet")

        def counts(compact: bool) -> list[tuple[str, str, str | None]]:
            parts = []
            for key in order:
                n = stats[key]
                if not n or key == "asking" and asks_on_line:
                    continue
                text = f" {glyphs[key]}{n}" if compact else "  " + t(count_labels[key], n=n)
                parts.append((text, count_styles[key], "jump_question" if key == "asking" else None))
            return parts

        def view_parts(compact: bool) -> list[tuple[str, str, str | None]]:
            view = self.short_view_name() if compact else view_name(self.view)
            window = short_window if compact else full_window
            return [("  ", R.DIM, None), (view, "bold", "setup"), (" \u00b7 " + window, R.DIM, None)]

        self._compact_reader = False
        full_counts = counts(False)
        short_counts = counts(True)
        full_view = view_parts(False)
        short_view = view_parts(True)
        full_right = self._summary_right_parts(compact=False)

        def left_budget(right: list[tuple[str, str, str | None]]) -> int:
            return max(0, width - cell_len("\u25b2 bit\u00e1cora ") - sum(cell_len(item[0]) for item in right) - 1)

        def fits(parts: list[tuple[str, str, str | None]], right: list[tuple[str, str, str | None]]) -> bool:
            return sum(cell_len(text) for text, _, _ in parts) <= left_budget(right)

        full_parts = [*full_counts, *full_view]
        if fits(full_parts, full_right):
            return full_parts

        short_parts = [*short_counts, *short_view]
        if not fits(short_parts, full_right):
            self._compact_reader = True
        short_right = self._summary_right_parts(compact=self._compact_reader)

        # Only the running and (when its chip line is hidden) asking counts survive the last compact step.
        while not fits(short_parts, short_right):
            removable = next(
                (key for key in ("quiet", "stopped", "limited", "failed", "done", "your_turn", "starting")
                 if stats[key] and any(glyphs[key] in part[0] for part in short_parts)),
                None,
            )
            if removable is None:
                break
            short_parts = [part for part in short_parts if glyphs[removable] not in part[0]]

        if not fits(short_parts, short_right):
            kept_counts = [part for part in short_counts if part in short_parts]
            budget = left_budget(short_right) - sum(cell_len(text) for text, _, _ in kept_counts)
            window_part = " \u00b7 " + short_window
            view_width = max(1, budget - cell_len("  " + window_part))
            short_view = [("  ", R.DIM, None), (first_line(self.short_view_name(), view_width), "bold", "setup"),
                          (window_part, R.DIM, None)]
            short_parts = [*kept_counts, *short_view]
            while not fits(short_parts, short_right):
                removable = next(
                    (key for key in ("quiet", "stopped", "limited", "failed", "done", "your_turn", "starting")
                     if stats[key] and any(glyphs[key] in part[0] for part in short_parts)),
                    None,
                )
                if removable is None:
                    break
                short_parts = [part for part in short_parts if glyphs[removable] not in part[0]]
        return short_parts

    def short_view_name(self) -> str:
        keys = {"everything": "tui_view_all", "background": "tui_view_background",
                "interactive": "tui_view_interactive", "live": "tui_view_live"}
        key = keys.get(self.view.id)
        return t(key) if key else first_line(view_name(self.view), 12)

    def questions_line_visible(self) -> bool:
        return bool(self.questions()) and self.size.height > 6

    def _summary_right_parts(self, *, compact: bool) -> list[tuple[str, str, str | None]]:
        health, dot, style = self.reader_health()
        state = f"\u25cf {t('ft_on')}" if self.live_only else f"\u25cb {t('ft_off')}"
        state_style = f"bold {R.GREEN}" if self.live_only else R.DIM
        if compact:
            return [("\u25cf", dot, None), (" " + t("tui_live_short") + " ", R.DIM, "toggle_live"),
                    (state, state_style, "toggle_live")]
        return [("\u25cf ", dot, None), (health, style, None),
                ("   " + t("key_live") + " ", R.DIM, "toggle_live"), (state, state_style, "toggle_live")]

    def summary_right(self) -> list[tuple[str, str, str | None]]:
        """Reader health and Live stay at the right edge; health text yields first on narrow terminals."""
        return self._summary_right_parts(compact=self._compact_reader)

    def reader_health(self) -> tuple[str, str, str]:
        """The reader's state as the desktop words it: the text, then the colours of its dot and of the text."""
        health = self.source.health
        if health == "error":
            return t("desktop_reader_short_error"), f"bold {R.RED}", f"bold {R.RED}"
        if health == "stale":
            return t("desktop_reader_short_stale"), R.YELLOW, R.YELLOW
        if health == "partial":
            return t("desktop_reader_short_partial"), R.YELLOW, R.YELLOW
        if health == "loading":
            return t("picker_loading"), R.CYAN, R.DIM
        return t("desktop_reader_ready", age=fmt_dur(time.monotonic() - self.source.last_update)), R.GREEN, R.DIM

    def reader_errors_note(self) -> str:
        """The failed providers, named exactly as the reader reports them."""
        return t("desktop_reader_partial", providers=", ".join(sorted(self.source.provider_errors)))

    def stats(self) -> dict[str, int]:
        stats = {k: 0 for k in ("asking", "running", "starting", "your_turn", "done", "failed", "limited",
                                "stopped", "quiet")}
        for run in self.runs:
            if run.status in stats:
                stats[run.status] += 1
        return stats

    # ---- command palette
    def get_system_commands(self, screen):
        yield SystemCommand(t("cmd_home"), t("cmd_home_d"), self.action_setup)
        for v in self.views:
            yield SystemCommand(f"{t('cmd_view')}: {view_name(v)}", t("cmd_view_d"),
                                lambda vid=v.id: self.switch_view(vid))
        for key in self.COLUMNS:
            yield SystemCommand(t("cmd_sort_column", column=self.column_name(key)), t("cmd_sort_column_d"),
                                lambda column=key: self.cycle_sort(column))
        yield SystemCommand(t("cmd_sort_reset"), t("cmd_sort_reset_d"), self.action_reset_sort)
        yield SystemCommand(t("cmd_live"), t("cmd_live_d"), self.action_toggle_live)
        yield SystemCommand(t("cmd_verbose"), t("cmd_verbose_d"), self.action_toggle_verbose)
        yield SystemCommand(t("cmd_follow"), t("cmd_follow_d"), self.action_toggle_follow)
        for code, name in (("en", "English"), ("es", "Español")):
            if code != self.lang_setting:
                yield SystemCommand(f"{t('cmd_lang')}: {name}", t("cmd_lang_d"), lambda c=code: self.switch_lang(c))
        yield from super().get_system_commands(screen)

    def sync_saved(self) -> None:
        """Show what another interface saved since this window last looked; keys it did not change stay as shown."""
        changed = self.state.refresh()
        if "view" in changed and self.state.get("view") in {v.id for v in self.views}:
            self.view_id = self.state.get("view")
        if "theme" in changed and self.state.get("theme") in self.available_themes:
            self.theme = self.state.get("theme")
        if "hours" in changed and capped_hours(self.state.get("hours")):
            self.config.hours = capped_hours(self.state.get("hours"))
            self.source.set_hours(self.config.hours)
        if "notify" in changed and isinstance(self.state.get("notify"), bool):
            self.config.notify = self.state.get("notify")
        if "lang" in changed and self.state.get("lang") in (*i18n.LANGS, "auto"):
            self.lang_setting = self.state.get("lang")
            i18n.set_lang(None if self.lang_setting == "auto" else self.lang_setting)
            self.relabel()
        if "view" in changed:
            self.row_keys = []
            self.show_runs()

    def switch_view(self, vid: str) -> None:
        self.sync_saved()
        self.view_id = vid
        self.state.set("view", vid)
        self.state.save()
        self.row_keys = []
        self.show_runs()

    def switch_lang(self, lang: str) -> None:
        self.state.set("lang", lang)
        self.state.save()
        self.lang_setting = lang
        i18n.set_lang(None if lang == "auto" else lang)
        self.relabel()

    # ---- data
    def poll_source(self) -> None:
        runs = self.source.poll()
        if runs is None:
            return
        try:
            self.apply(runs)
        except NoMatches:  # a timer tick after the widgets were torn down on exit; nothing left to draw
            pass

    def refresh_data(self) -> None:
        self.source.refresh()

    def on_unmount(self) -> None:
        self.source.stop()

    def visible_runs(self) -> list[Run]:
        runs = [r for r in self.all_runs or [] if self.view.matches(r) and (r.live or not self.live_only)]
        return sort_runs(runs, self.sort_column, self.sort_direction)

    def apply(self, runs: list[Run]) -> None:
        if not self.screen_stack:
            return
        first = self.all_runs is None
        self.all_runs = runs
        if not first:
            self.notify_changes(runs)
        self._prev_status = {r.key: r.status for r in runs}
        if self._setup is not None:
            self._setup.fill(runs)
        self.show_runs()

    def show_runs(self) -> None:
        if not self.screen_stack:
            return
        runs = self.visible_runs()
        self.runs = runs
        table = self.q("#runs", DataTable)
        keys = [r.key for r in runs]
        rows = {r.key: self.row_cells(r) for r in runs}
        if keys != self.row_keys:
            cur = self.selected
            table.clear()
            for column in table.columns.values():
                column.auto_width = True
                column.content_width = column.label.cell_len
            self._cells.clear()
            for k in keys:
                table.add_row(*rows[k], key=k)
                self._cells[k] = [cell_key(v) for v in rows[k]]
            self.row_keys = keys
            if cur in keys:
                table.move_cursor(row=keys.index(cur))
            elif keys:
                table.move_cursor(row=0)
        else:
            for k in keys:
                old = self._cells.get(k, [])
                new = [cell_key(v) for v in rows[k]]
                for i, (col, val) in enumerate(zip(self.display_columns, rows[k], strict=True)):
                    if i >= len(old) or old[i] != new[i]:
                        table.update_cell(k, col, val, update_width=True)
                self._cells[k] = new
        if self.selected not in keys:
            self.selected = (keys[table.cursor_row] if 0 <= table.cursor_row < len(keys) else keys[0]) \
                if keys else None
        self.render_hud()
        self.render_detail()
        self.size_run_columns(rows)
        self.call_after_refresh(self.size_run_columns)

    def notify_changes(self, runs: list[Run]) -> None:
        """Toast and bell when a session you are watching finishes, stops or asks something.

        Only sessions the current view shows count: a view is a statement of what you care about.
        """
        if not self.config.notify:
            return
        ring = False
        for r in runs:
            if not self.view.matches(r):
                continue
            before, now = self._prev_status.get(r.key), r.status
            if before is None or before == now:
                continue
            msg = None
            if now == "asking":
                msg, sev = t("n_ask", label=r.label), "warning"
            elif before in ("running", "asking", "starting") and now in ("done", "your_turn"):
                msg, sev = t("n_done", label=r.label), "information"
            elif before in ("running", "asking", "starting") and now in ("failed", "limited", "stopped"):
                msg, sev = t("n_stopped", label=r.label), "error"
            if msg:
                self.notify(msg, severity=sev, timeout=8)
                ring = True
        if ring:
            self.bell()

    def row_cells(self, r: Run) -> list:
        s = r.session
        model = r.model or "-"
        ctx_used = context_tokens(s)
        ctx_window = getattr(s, "ctx_window", 0) if s else 0
        if s and ctx_window and ctx_window > 0 and ctx_used is not None:
            frac = ctx_used / ctx_window
            ctx = Text(f"{100 * frac:.0f}%", style=pct_style(frac))
        elif s and ctx_window and ctx_window > 0:
            ctx = Text(t("quota_value_unknown"), style=R.DIM)
        elif s:
            ctx = Text(format_context_tokens(ctx_used), style=R.DIM)
        else:
            ctx = Text("-", style=R.DIM)
        if not s:
            cmds = Text("-", style=R.DIM)
        else:
            cmds = Text(str(s.n_cmds))
            if s.n_fail:
                cmds.append(f" {s.n_fail}✗", style=R.RED)
        now = activity(s, r, limit=None)
        if len(now.plain) > 40:
            now = now[:39] + Text("…", style=now.style)
        values = {
            "status": status_text(r),
            "agent": agent_table_text(r),
            "name": Text(first_line(r.label, 60), style="bold" if r.live else ""),
            "now": now,
            "model": Text(f"{model} {r.effort}" if r.effort else model),
            "repo": Text(r.repo, style=R.CYAN),
            "last": Text(fmt_ago(s.last_ts if s else None)),
            "dur": Text(fmt_dur(r.duration)),
            "ctx": ctx,
            "tok": Text(fmt_tokens(s.tokens if s else 0)),
            "cmds": cmds,
            "files": Text(str(len(s.files)) if s else "-"),
            "mode": Text(r.sandbox or "-"),
            "pid": Text(str(r.proc.pid), style=R.DIM) if r.proc else Text("-", style=R.DIM),
        }
        return [values[key] for key in self.COLUMNS]

    # ---- HUD
    def quota_text(self) -> Text:
        """A single compact line with bars, percentages and short renewal times."""
        tx = Text(no_wrap=True)
        first_provider = True
        for provider, rate in self.source.mirror.quotas.items():
            if not first_provider:
                tx.append("  \u00b7  ", style=R.DIM)
            first_provider = False
            tx.append(provider_label(provider) + " ", style=f"bold {R.DIM}")
            windows = rate.get("windows") if isinstance(rate.get("windows"), list) else []
            limit = rate.get("limit")
            if isinstance(limit, dict):
                name = quota_name_label(limit.get("name"), limit.get("window")) or t("quota_window_unknown")
                resets_at = quota_timestamp(limit.get("resets_at"))
                if resets_at is not None and resets_at > time.time():
                    text = t("tui_quota_limit_short", type=name, when=when_text(resets_at))
                    tx.append(text, style=f"bold {R.YELLOW}")
                else:
                    tx.append(name + " " + t("tui_quota_limit_generic"), style=f"bold {R.YELLOW}")
                if windows:
                    tx.append("  ")
            first_window = True
            for window in windows:
                if not isinstance(window, dict):
                    continue
                if not first_window:
                    tx.append("  \u00b7  ", style=R.DIM)
                first_window = False
                name = quota_window_name(window)
                resets_at = quota_timestamp(window.get("resets_at"))
                expired = resets_at is not None and resets_at <= time.time()
                if expired:
                    tx.append(name + " " + t("quota_reset"), style=R.DIM)
                    continue
                pct = window.get("used_percent")
                known = isinstance(pct, (int, float)) and not isinstance(pct, bool) and math.isfinite(pct)
                tx.append(name + " ", style="bold")
                tx.append_text(bar(pct / 100 if known else None, 5))
                tx.append(" " + quota_window_text(window))
                if resets_at is not None:
                    tx.append(" " + t("tui_quota_renew_short", when=when_text(resets_at)), style=R.DIM)
            if not windows and not isinstance(limit, dict):
                tx.append(t("quota_unknown"), style=R.DIM)
        return tx or Text(t("quota_unknown"), style=R.DIM, no_wrap=True)

    def quota_details_text(self) -> str:
        """The quota line's full renewal details, retained in the Details tab."""
        providers = []
        for provider, rate in self.source.mirror.quotas.items():
            pieces = [provider_label(provider)]
            windows = rate.get("windows") if isinstance(rate.get("windows"), list) else []
            for window in windows:
                if not isinstance(window, dict):
                    continue
                value = quota_window_name(window) + " " + quota_window_text(window)
                resets_at = quota_timestamp(window.get("resets_at"))
                if resets_at is not None and resets_at > time.time():
                    when = datetime.fromtimestamp(resets_at).strftime("%d/%m %H:%M")
                    value += " (" + t("quota_renews", t=when) + ")"
                pieces.append(value)
            limit = rate.get("limit")
            if isinstance(limit, dict):
                name = quota_name_label(limit.get("name"), limit.get("window"))
                resets_at = quota_timestamp(limit.get("resets_at"))
                if resets_at is not None and resets_at > time.time():
                    when = datetime.fromtimestamp(resets_at).strftime("%d/%m %H:%M")
                    value = t("quota_limit_until", type=name, when=when) if name else t("quota_limit_when", when=when)
                else:
                    value = quota_limit_text(limit)
                pieces.append(value)
            if rate.get("reporting") == "limit-only" and not windows:
                pieces.append(t("quota_unrecorded"))
            elif len(pieces) == 1:
                pieces.append(t("quota_unrecorded") if rate.get("reporting") == "limit-only" else t("quota_unknown"))
            providers.append(" \u00b7 ".join(pieces))
        return "\n".join(providers) if providers else t("quota_unknown")

    def render_hud(self) -> None:
        if not self.screen_stack:
            return
        self.compact_hero = self.size.height <= 24
        self.short_quota = self.size.height <= 24
        beacon = self.q("#beacon", Beacon)
        summary = self.summary()
        beacon.set_state(self.working(), summary, self.summary_right())
        health = self.reader_health()[0]
        if self._compact_reader and self.source.health == "partial" and self.source.provider_errors:
            health = self.reader_errors_note()
        beacon.tooltip = health if self._compact_reader else None
        self.q("#beacon-rule", BeaconRule).set_state(
            any(r.status in ("running", "asking", "starting") for r in self.runs),
            any(r.status == "asking" for r in self.runs))
        self.render_header()
        self.render_plain_tools()
        self.render_selected()
        self.render_keys()

    def render_header(self) -> None:
        """One compact quota line and an optional row of waiting-question chips."""
        quota_widget = self.q("#quota", Static)
        quota_widget.display = not self.short_quota
        if quota_widget.display:
            quota = Text(t("hud_quota") + " ", style=f"bold {R.DIM}", no_wrap=True)
            quota.append_text(self.quota_text())
            width = quota_widget.size.width or max(1, self.size.width - 2)
            quota.truncate(width, overflow="ellipsis")
            quota_widget.update(quota, layout=False)
        asks = self.questions()
        strip = self.q("#attention", Static)
        strip.display = bool(asks) and self.size.height > 6
        if strip.display:
            strip.update(self.attention_grid(asks), layout=False)

    def attention_grid(self, asks: list[Run]) -> Table:
        g = Table.grid(expand=True)
        g.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        g.add_row(self.attention_text(asks))
        return g

    def attention_text(self, asks: list[Run]) -> Text:
        """The waiting sessions as chips: the first one (oldest) names the strip; each chip opens its question."""
        tx = Text("? " + t("hud_n_ask", n=len(asks)), style=f"bold {R.YELLOW}")
        bg = R.mix(R.BG, R.YELLOW, 0.16)
        ink = ink_on(bg, [R.BG, R.FG])  # the chip's own fill sets its text, as on the desktop
        for i, run in enumerate(asks[:8]):
            tx.append(" ", style=R.DIM)
            chip = Text()
            name = first_line(run.label, 40) or "-"
            start = len(chip.plain)
            chip.append(name, style=f"bold {ink} on {bg}")
            chip.apply_meta({"@click": f"app.reveal_question({i})"}, start=start, end=len(chip.plain))
            last = run.session.last_ts if run.session else None
            if last:
                chip.append(" " + t("desktop_waiting_for", age=fmt_ago(last)),
                            style=f"{R.readable(R.DIM, bg, ink)} on {bg}")
            if i == 0 and len(asks) > 1:
                chip.append(" " + t("desktop_oldest"), style=f"{R.readable(R.YELLOW, bg, ink)} on {bg}")
            tx.append_text(chip)
        if len(asks) > 8:
            tx.append(f" +{len(asks) - 8}", style=R.DIM)
        tx.append(" ", style=R.DIM)
        tx.append("w " + t("ft_jump"), style=R.DIM)
        return tx

    def selected_fields(self, run: Run | None, *, compact: bool = False) -> list[list[tuple[Text, int, int]]]:
        """The hero rows; compact mode keeps identity and Now or the pending question."""
        def pair(label: str, value: str | Text, ratio: int = 1) -> tuple[Text, int, int]:
            field = Text(no_wrap=True)
            field.append(f"{label} ", style=R.DIM)
            field.append_text(value) if isinstance(value, Text) else field.append(value)
            return field, cell_width(Text(f"{label} ")) + 3, ratio

        if run is None:
            msg = t("hud_searching") if self.all_runs is None else t("hud_empty") if not self.runs else t("hud_none")
            empty = [(Text(no_wrap=True), 3, 1)]
            return [[(Text(msg, no_wrap=True), 3, 1)], empty] if compact else [
                [(Text(msg, no_wrap=True), 3, 1)], empty, empty, empty]
        s = run.session
        status = status_text(run)
        name = Text(first_line(run.label, 60), style="bold", no_wrap=True)
        agent = agent_text(run)
        repo = Text(run.repo, style=R.CYAN, no_wrap=True)
        row1 = [(status, cell_width(status), 0), (name, 3, 4), (agent, 3, 2), (repo, 3, 2)]
        now = pair(t("hud_now"), activity(s, run, limit=None), 1)
        if compact:
            return [row1] if run.status == "asking" else [row1, [now]]

        model = (run.model or "-") + (f" {run.effort}" if run.effort else "")
        row2 = [
            pair(t("col_model"), model, 3),
            pair(t("col_mode"), run.sandbox or "-", 1),
            pair(t("inf_started"), started_text(run.start), 1),
            pair(t("col_dur"), fmt_dur(run.duration), 1),
            pair(t("col_last"), fmt_ago(s.last_ts if s else None), 1),
        ]
        context = Text(no_wrap=True)
        ctx_used = context_tokens(s)
        ctx_window = getattr(s, "ctx_window", 0) if s else 0
        if s and ctx_window and ctx_window > 0 and ctx_used is not None:
            frac = ctx_used / ctx_window
            context.append_text(bar(frac, 10))
            context.append(f" {100 * frac:.0f}%")
        elif s and ctx_window and ctx_window > 0:
            context.append_text(bar(None, 10))
            context.append(" " + t("quota_value_unknown"), style=R.DIM)
        else:
            context.append(format_context_tokens(ctx_used))
        commands = Text(str(s.n_cmds) if s else "-", no_wrap=True)
        if s and s.n_fail:
            commands.append(f" {s.n_fail}\u2717", style=R.RED)
        row3 = [
            pair(t("usage_" + usage_basis(s, run.provider)), fmt_tokens(s.tokens if s else 0), 1),
            pair(t("hud_context"), context, 2),
            pair(t("hud_commands"), commands, 1),
            pair(t("hud_files"), str(len(s.files)) if s else "-", 1),
        ]
        row4 = [now]
        return [row1, row2, row3, row4]

    def selected_rows(self, run: Run | None) -> list[Text]:
        """The four raw one-line hero rows, in desktop detail order, for tests and inspection."""
        rows = []
        for fields in self.selected_fields(run):
            row = Text(no_wrap=True)
            for index, (field, _, _) in enumerate(fields):
                if index:
                    row.append("   ")
                row.append_text(field)
            rows.append(row)
        return rows

    @staticmethod
    def selected_field_grid(fields: list[tuple[Text, int, int]], width: int) -> Table:
        grid = Table.grid(padding=(0, 1), expand=False)
        available = max(0, width - len(fields) + 1)  # grid padding is one cell between fields
        desired = [cell_width(field) for field, _, _ in fields]
        widths = [min(wanted, minimum) for wanted, (_, minimum, _) in zip(desired, fields, strict=True)]
        if sum(widths) > available:
            widths = [available // len(fields)] * len(fields)
        remaining = available - sum(widths)
        shares = [i for i, (_, _, ratio) in enumerate(fields) for _ in range(ratio)] or [0]
        while remaining:
            grew = False
            for i in shares:
                if remaining and widths[i] < desired[i]:
                    widths[i] += 1
                    remaining -= 1
                    grew = True
            if not grew:
                for i in shares[:remaining]:
                    widths[i] += 1
                remaining = 0
        for field_width in widths:
            grid.add_column(width=field_width, no_wrap=True, overflow="ellipsis")
        grid.add_row(*(field for field, _, _ in fields))
        return grid

    def render_selected(self) -> None:
        """Draw a compact identity row at short heights, otherwise the full hero."""
        run = self.current()
        hero = self.q("#hero")
        compact = self.compact_hero
        asking = run is not None and run.status == "asking"
        hero.set_class(compact, "compact")
        hero.set_class(compact and not asking, "has-now")
        hero.border_title = t("hud_selected")
        g = Table.grid(expand=True)
        g.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        selected = self.q("#selected", Static)
        for fields in self.selected_fields(run, compact=compact):
            width = selected.size.width or max(1, self.size.width - 4)
            g.add_row(self.selected_field_grid(fields, width))
        selected.update(g, layout=False)
        box = self.q("#sel-question", Static)
        box.display = not self.list_maximized and asking
        if not box.display:
            return
        question = question_text(run)
        if compact:
            line = Text("? " + question, style=f"bold {R.YELLOW}", no_wrap=True)
            line.truncate(max(1, box.size.width or self.size.width - 4), overflow="ellipsis")
            box.update(line, layout=False)
            return
        heading = Text(f"? {t('desktop_question_banner')} ", style=f"bold {R.YELLOW}")
        heading.append(question, style=f"bold {R.YELLOW}")
        width = max(1, box.size.width or self.size.width - 4)
        lines = heading.wrap(self.console, max(1, width - 1), overflow="fold")
        if len(lines) > 3:
            lines = lines[:3]
            if width > 1:
                lines[-1].truncate(width - 2, overflow="crop")
                lines[-1].append("\u2026")
            else:
                lines[-1] = Text("\u2026", style=f"bold {R.YELLOW}")
        q = Table.grid(expand=True)
        q.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        for line in lines:
            q.add_row(line)
        q.add_row(Text(t("desktop_question_hint"), style=R.DIM, no_wrap=True, overflow="ellipsis"))
        box.update(q)

    def render_plain_tools(self) -> None:
        """Keep the Plain text switch beside Messages and Result, even when the footer is compact."""
        text = Text()
        text.append("m", style=f"bold {R.CYAN}")
        text.append(" " + t("desktop_plain") + " ", style=R.DIM)
        on = f"\u25cf {t('ft_on')}" if self.plain_text else f"\u25cb {t('ft_off')}"
        text.append(on, style=f"bold {R.GREEN}" if self.plain_text else R.DIM)
        text.apply_meta({"@click": "app.toggle_plain"})
        self.q("#msg-tools", HintLine).update(text, layout=False)
        self.q("#res-tools", HintLine).update(text, layout=False)

    def render_keys(self) -> None:
        """The key line: what each key does, and the state of the four switches it controls."""
        if not self.screen_stack:
            return
        self.q("#keys", Static).update(keys_text(self.follow, self.live_only, self.verbose, self.size.width,
                                                  self.list_maximized, self.plain_text))

    def current(self) -> Run | None:
        return next((r for r in self.runs if r.key == self.selected), None)

    # ---- detail
    def render_detail(self, force: bool = False) -> None:
        """Draw the active tab only; the others catch up when they are opened."""
        if not self.screen_stack:
            return
        if force:
            self._shown.clear()
        run = self.current()
        tab = self.q("#detail", TabbedContent).active
        {"t-act": self.render_activity, "t-msg": self.render_messages, "t-files": self.render_files,
         "t-log": self.render_log, "t-res": self.render_result, "t-info": self.render_info}[tab](run)

    @on(TabbedContent.TabActivated, "#detail")
    def tab_activated(self, ev: TabbedContent.TabActivated) -> None:
        self.render_detail()

    def render_activity(self, run: Run | None) -> None:
        s = run.session if run else None
        act = self.q("#act", RichLog)
        sig = (run and run.key, id(s), self.verbose, getattr(s, "events_revision", 0))
        prev = self._shown.get("act")
        if prev is None or prev[0] != sig:
            act.clear()
            written = 0
        else:
            written = prev[1]
        if s:
            evs = list(s.events)
            if written == 0:
                # Drawing thousands of events makes switching rows sluggish; start with the latest ones.
                shown = [ev for ev in evs if visible(ev, self.verbose)]
                if len(shown) > FIRST_PAINT:
                    act.write(Text(t("ev_earlier", n=len(shown) - FIRST_PAINT), style=R.DIM))
                    written = shown[-FIRST_PAINT - 1].seq
            budget = PAINT_BATCH  # each event costs a few ms to lay out; spread big backlogs over frames
            for i, ev in enumerate(evs):
                if ev.seq <= written:
                    continue
                if ev.kind == "think" and ev is s.last_event and run.live:
                    break  # still growing; the HUD shows it
                if budget == 0:
                    if self._paint_timer is None:
                        self._paint_timer = self.set_timer(1 / 60, self._continue_paint)
                    break
                if visible(ev, self.verbose):
                    prev_ev = evs[i - 1] if i else None
                    think = prev_ev if prev_ev and prev_ev.kind == "think" and not self.verbose else None
                    act.write(render_event(ev, self.verbose, think), expand=True, scroll_end=self.follow)
                    budget -= 1
                written = ev.seq
        elif run and written == 0:
            act.write(Text(t("no_session_file"), style=R.DIM))
            written = -1
        self._shown["act"] = (sig, written)

    def _continue_paint(self) -> None:
        self._paint_timer = None
        if self.screen_stack and self.q("#detail", TabbedContent).active == "t-act":
            self.render_activity(self.current())

    def render_messages(self, run: Run | None) -> None:
        s = run.session if run else None
        messages = (s.messages if s else [])[-60:]
        # The mirror caps its copy, so len(messages) stops growing while new ones arrive: sign what is painted.
        sig = (run and run.key, tuple(messages), self.plain_text)
        markdown = self.q("#msgs", Markdown)
        plain = self.q("#msgs-plain", Static)
        markdown.display = not self.plain_text
        plain.display = self.plain_text
        if self._shown.get("msg") != sig:
            self._shown["msg"] = sig
            md = "\n\n---\n\n".join(f"**{hhmm(ts)}**\n\n{txt}" for ts, txt in messages)
            text = "\n\n---\n\n".join(f"{hhmm(ts)}\n\n{txt}" for ts, txt in messages)
            laid_out = markdown.update(md or t("no_messages"))
            plain.update(text or t("no_messages").strip("_"))
            if self.follow:
                self.run_worker(self.follow_messages(laid_out), exclusive=True, group="follow-messages")

    async def follow_messages(self, laid_out) -> None:
        """Keep the newest message in view: wait until the Markdown has parsed the new text, then go down."""
        await laid_out
        scroll = self.q("#msgs-scroll", VerticalScroll)
        self.call_after_refresh(scroll.scroll_end, animate=False)

    def render_files(self, run: Run | None) -> None:
        s = run.session if run else None
        # Only what the table shows: rebuilding it on every event sent the cursor back to the top.
        sig = (run and run.key, tuple((p, v[0], v[1], v[3], v[4]) for p, v in s.files.items()) if s else ())
        if self._shown.get("files") == sig:
            return
        self._shown["files"] = sig
        ft = self.q("#files", DataTable)
        keep = None
        if ft.row_count and 0 <= ft.cursor_row < ft.row_count:
            keep = ft.ordered_rows[ft.cursor_row].key.value
        ft.clear()
        order = sorted(s.files, key=lambda p: -(s.files[p][2] or 0)) if s else []
        for path in order:
            kind, n, ts, plus, minus = s.files[path]
            ft.add_row(Text(kind, style=R.PURPLE), Text(f"+{plus}", style=R.GREEN),
                       Text(f"−{minus}", style=R.RED), str(n), hhmm(ts), path, key=path)
        if keep in order:
            ft.move_cursor(row=order.index(keep))

    def render_result(self, run: Run | None) -> None:
        s = run.session if run else None
        out = run.out if run else None
        try:
            ostat = os.stat(out) if out else None
        except OSError:
            ostat = None
        sig = (run and run.key, ostat.st_mtime if ostat else None, ostat.st_size if ostat else None,
               len(s.last_agent_message) if s else 0, self.plain_text)
        if self._shown.get("res") == sig:
            return
        self._shown["res"] = sig
        shown_out = out or t("res_no_out")
        if ostat:
            start = max(0, ostat.st_size - RES_READ_LIMIT)
            with open(out, "rb") as fh:
                fh.seek(start)
                data = fh.read(ostat.st_size - start)
            content = data.decode("utf-8", errors="replace")
            note = ""
            if start > 0:
                # Drop the half line (or half character) the cutoff landed in, up to its newline.
                content = content.partition("\n")[2]
                note = t("desktop_file_tail") + "\n\n"
            rich_body = f"_{out}_\n\n" + note + content
            plain_body = f"{out}\n\n" + note + content
        elif s and s.last_agent_message:
            note = t("res_last_msg", out=shown_out)
            rich_body = note + "\n\n" + s.last_agent_message
            plain_body = note.strip("_") + "\n\n" + s.last_agent_message
        else:
            rich_body = t("res_none", out=shown_out)
            plain_body = rich_body.strip("_")
        markdown = self.q("#res", Markdown)
        plain = self.q("#res-plain", Static)
        markdown.display = not self.plain_text
        plain.display = self.plain_text
        markdown.update(rich_body)
        plain.update(plain_body)

    def render_info(self, run: Run | None) -> None:
        self.q("#info", Static).update(self.info_text(run))

    def render_log(self, run: Run | None) -> None:
        log = self.q("#log", RichLog)
        path = run.log if run else None
        try:
            size = os.path.getsize(path) if path else None
        except OSError:
            size = None
        ident = (run and run.key, path)
        prev = self._shown.get("log")
        if prev and prev[0] == ident and prev[1] == (size or 0):
            return
        initial = prev is None or prev[0] != ident or size is None or size < prev[1]
        if initial:
            log.clear()
            start = max(0, (size or 0) - LOG_INITIAL)
            skipped = start > 0
        else:
            start = max(prev[1], (size or 0) - LOG_READ_LIMIT)
            skipped = start > prev[1]
        if size is not None and path:
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read(size - start)
            content = data.decode("utf-8", "replace")
            if skipped:
                content = content.partition("\n")[2]  # the cutoff may have landed mid-line
            block = Text()
            for line in content.splitlines():
                st = R.RED if LOG_ERR.search(line) else R.CYAN if line.startswith(("exec", "$ ")) else ""
                block.append(line + "\n", style=st)
            if skipped:
                # Only the pass that left bytes out says so; the next pass does not repeat the note.
                block.append(t("desktop_file_tail") + "\n", style=R.DIM)
            if block:
                block.rstrip()
                log.write(block, scroll_end=self.follow)
        elif run:
            log.write(Text(t("no_log"), style=R.DIM))
        self._shown["log"] = (ident, size or 0)

    def provider(self, name: str) -> Provider | None:
        return next((p for p in self.providers if p.name == name), None)

    def resume_cmd(self, run: Run) -> str | None:
        prov = self.provider(run.provider)
        return prov.resume_cmd(run) if prov else None

    def info_text(self, run: Run | None) -> Text:
        if not run:
            return Text(t("no_selection"), style=R.DIM)
        s, pi = run.session, run.proc
        basis = usage_basis(s, run.provider)
        context_used = context_tokens(s)
        context_window = getattr(s, "ctx_window", 0) if s else 0
        if s and context_window and context_window > 0:
            context = f"{context_used:,} / {context_window:,}" if context_used is not None else \
                f"{t('quota_value_unknown')} / {context_window:,}"
        elif s:
            context = format_context_tokens(context_used)
        else:
            context = "-"
        if s and context_window_assumed(s, run):
            context += " · " + t("context_window_assumed")
        source = effort_source(s, run)
        access = translated_kind(
            access_kind(s, run),
            ("read-only", "workspace-write", "prompt", "auto-edit", "plan", "full-access", "other"),
            "access",
        )
        origin_raw = getattr(s, "origin", getattr(run, "origin", "-")) if s else getattr(run, "origin", "-")
        rows = [
            (t("inf_name"), run.label),
            (t("inf_now"), activity(s, run, limit=None).plain),
            (t("inf_status"), t(f"st_{run.status}")),
            (t("inf_status_source"), t(f"status_source_{run.status_source}") if run.status_source else "-"),
            (t("inf_agent"), agent_text(run).plain),
            (t("inf_origin_raw"), origin_raw or "-"),
            (t("inf_session"), (s and s.session_id) or "-"),
            (t("inf_folder"), run.cwd or "-"),
            (t("inf_model"), run.model or "-"),
            (t("inf_effort"), run.effort or "-"),
            (t("inf_effort_source"), t(f"effort_source_{source}")),
            (t("inf_access_kind"), access),
            (t("inf_mode"), run.sandbox or "-"),
            (t("inf_started"), datetime.fromtimestamp(run.start).strftime("%Y-%m-%d %H:%M:%S") if run.start else "-"),
            (t("inf_duration"), fmt_dur(run.duration)),
            (t("inf_commands"), str(s.n_cmds) if s else "-"),
            (t("inf_failed"), str(s.n_fail) if s else "-"),
            (t("inf_files"), str(len(s.files)) if s else "-"),
            (t(f"usage_{basis}"), f"{s.tokens:,}" if s else "-"),
            (t("inf_tokens_basis"), t(f"usage_{basis}_basis")),
            (t("inf_context"), context),
            (t("hud_quota"), self.quota_details_text()),
            (t("inf_result"), run.out or "-"),
            (t("inf_log"), run.log or "-"),
            (t("inf_file"), str(s.path) if s else "-"),
            (t("inf_pid"), str(pi.pid) if pi else t("not_running")),
            (t("inf_command"), " ".join(pi.cmdline[1:]) if pi else "-"),
            (t("inf_resume"), self.resume_cmd(run) or "-"),
        ]
        if s and s.open_questions:
            rows.insert(1, (t("inf_question"), " | ".join(s.open_questions.values())))
        if self.source.provider_errors:
            rows.insert(1, (t("inf_reader"), self.reader_errors_note()))
        tx = Text()
        for k, v in rows:
            tx.append(f"{k:>18}  ", style="bold")
            tx.append(f"{v}\n")
        return tx

    # ---- events
    @on(DataTable.HeaderSelected, "#runs")
    def header_selected(self, ev: DataTable.HeaderSelected) -> None:
        self.cycle_sort(ev.column_key.value)

    @on(DataTable.RowHighlighted, "#runs")
    def row_highlighted(self, ev: DataTable.RowHighlighted) -> None:
        if ev.row_key is not None and ev.row_key.value != self.selected:
            self.selected = ev.row_key.value
            self.render_hud()
            # Wait for the cursor to settle so holding an arrow key stays smooth.
            if self._detail_timer:
                self._detail_timer.stop()
            self._detail_timer = self.set_timer(0.12, lambda: self.render_detail(force=True))

    @on(DataTable.RowSelected, "#runs")
    def row_selected(self, ev: DataTable.RowSelected) -> None:
        target = self.detail_scroller()
        if target:
            target.focus()

    def detail_scroller(self):
        """The scrollable widget of the active tab (RichLog, table or container)."""
        pane = self.q("#detail", TabbedContent).active_pane
        if not pane or not pane.children:
            return None
        return pane.children[0]

    def action_scroll_detail(self, how: str) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.scroll_help(how)
            return
        w = self.detail_scroller()
        if w is None:
            return
        {"up": lambda: w.scroll_relative(y=-3, animate=False),
         "down": lambda: w.scroll_relative(y=3, animate=False),
         "page_up": lambda: w.scroll_page_up(animate=False),
         "page_down": lambda: w.scroll_page_down(animate=False),
         "home": lambda: w.scroll_home(animate=False),
         "end": lambda: w.scroll_end(animate=False)}[how]()
        if how in ("up", "page_up", "home") and self.follow and isinstance(w, RichLog):
            self.follow = False
            self.notify(t("n_paused"))
            self.render_hud()

    # ---- actions
    def action_refresh_now(self) -> None:
        self.refresh_data()

    def settings(self) -> Settings:
        return Settings(view=self.view_id, lang=self.lang_setting, hours=self.config.hours,
                        notify=self.config.notify, theme=self.theme)

    def action_setup(self) -> None:
        if self._setup is not None:
            return
        self.sync_saved()  # the home starts from what is saved now, not from what this window read at launch
        found = [p.label for p in self.providers if p.available()]
        themes = [n for n in THEMES if n in self.available_themes]
        opened = self.settings()
        first_run = self.first_run
        self._setup = SetupScreen(self.views, self.all_runs, opened, themes, first_run, found)

        def done(result: Settings | None) -> None:
            self._setup = None
            self.first_run = False
            if result is None:
                if self._stale:
                    self.redraw_all()
                return
            # Only the settings changed here are written: an unchanged one may already be newer on disk.
            # The first save writes them all, so the picker does not come back on the next start.
            keys = [key for key in ("view", "lang", "hours", "notify", "theme")
                    if first_run or getattr(result, key) != getattr(opened, key)]
            lang_changed = result.lang != self.lang_setting
            if "view" in keys:
                self.view_id = result.view
            if "hours" in keys:
                self.config.hours = result.hours
                self.source.set_hours(result.hours)
            if "notify" in keys:
                self.config.notify = result.notify
            if "theme" in keys:
                self.theme = result.theme
            for key in keys:
                self.state.set(key, getattr(result, key))
            if keys and not self.state.save():
                self.notify(t("desktop_preferences_failed"), severity="error")
            self.lang_setting = result.lang
            if lang_changed:
                self.relabel()
            elif self._stale:
                self.redraw_all()
            self.notify(t("n_saved"))
            self.row_keys = []
            self.show_runs()
            self.refresh_data()

        self.push_screen(self._setup, done)

    def action_toggle_live(self) -> None:
        self.live_only = not self.live_only
        self.notify(t("n_live_on") if self.live_only else t("n_live_off", h=f"{self.config.hours:g}"))
        self.show_runs()

    def action_toggle_verbose(self) -> None:
        self.verbose = not self.verbose
        self.notify(t("n_verbose_on") if self.verbose else t("n_verbose_off"))
        self.render_detail(force=True)
        self.render_hud()

    def action_toggle_plain(self) -> None:
        self.plain_text = not self.plain_text
        self.render_detail(force=True)
        self.render_hud()

    def action_toggle_follow(self) -> None:
        self.follow = not self.follow
        self.notify(t("n_follow_on") if self.follow else t("n_follow_off"))
        if self.follow:
            self.jump_to_end()
        self.render_hud()

    def jump_to_end(self) -> None:
        w = self.detail_scroller()
        if w is not None and self.active_follows():
            self.call_after_refresh(w.scroll_end, animate=False)

    def active_follows(self) -> bool:
        """Whether the active tab streams new content: Activity, Messages and Log."""
        return self.q("#detail", TabbedContent).active in ("t-act", "t-msg", "t-log")

    def on_mouse_scroll_up(self, event) -> None:
        """Reading older output with the wheel pauses following, as Shift+Up does."""
        if self.follow and self.active_follows() and self.q("#detail").region.contains(event.screen_x,
                                                                                       event.screen_y):
            self.follow = False
            self.notify(t("n_paused"))
            self.render_hud()

    def on_mouse_scroll_down(self, event) -> None:
        """Back at the bottom by hand: follow again."""
        if not self.follow and self.active_follows() and self.q("#detail").region.contains(event.screen_x,
                                                                                         event.screen_y):
            self.call_after_refresh(self.resume_if_at_end)

    def resume_if_at_end(self) -> None:
        w = self.detail_scroller()
        if w is not None and not self.follow and w.scroll_y >= w.max_scroll_y - 1:
            self.follow = True
            self.render_hud()

    def action_next_theme(self) -> None:
        self.sync_saved()  # the next theme follows the saved one, even if another interface changed it
        names = [n for n in THEMES if n in self.available_themes]
        i = names.index(self.theme) + 1 if self.theme in names else 0
        self.theme = names[i % len(names)]
        self.state.set("theme", self.theme)
        self.state.save()
        self.notify(t("n_theme", name=self.theme))

    def action_tab(self, tab: str) -> None:
        self.q("#detail", TabbedContent).active = tab

    def action_focus_table(self) -> None:
        self.q("#runs", DataTable).focus()

    def action_toggle_maximize_list(self) -> None:
        table = self.q("#runs", DataTable)
        if not self.list_maximized:
            self._list_focus_id = getattr(self.focused, "id", None)
            self.list_maximized = True
            self.q("#hero").display = False
            self.q("#detail").display = False
            table.add_class("maximized")
            table.focus()
        else:
            self.list_maximized = False
            table.remove_class("maximized")
            self.q("#hero").display = True
            self.q("#detail").display = True
            self.render_selected()
            focus_id, self._list_focus_id = self._list_focus_id, None
            def restore_focus() -> None:
                try:
                    self.q(f"#{focus_id}").focus() if focus_id else table.focus()
                except NoMatches:
                    table.focus()
            self.call_after_refresh(restore_focus)
        self.render_keys()

    def action_focus_detail(self) -> None:
        widget = self.detail_scroller()
        if widget:
            widget.focus()

    def questions(self) -> list[Run]:
        """Sessions of this view waiting for an answer, oldest first, as the desktop strip lists them."""
        asks = [r for r in self.all_runs or [] if self.view.matches(r) and r.status == "asking"]
        return sorted(asks, key=lambda r: r.session.last_ts if r.session and r.session.last_ts else math.inf)

    def action_jump_question(self) -> None:
        asks = self.questions()
        if asks:
            self.reveal_run(asks[0].key)
        else:
            self.notify(t("n_no_question"), severity="warning")

    def action_reveal_question(self, index: int) -> None:
        asks = self.questions()
        if index < len(asks):
            self.reveal_run(asks[index].key)

    def reveal_run(self, key: str) -> None:
        """Select a session in the table, leaving the Live-only filter if that is what hides it."""
        run = next((r for r in self.all_runs or [] if r.key == key), None)
        if run is not None and self.live_only and not run.live:
            self.live_only = False
        self.show_runs()
        table = self.q("#runs", DataTable)
        if key in self.row_keys:
            table.move_cursor(row=self.row_keys.index(key))
            table.focus()

    def action_help(self) -> None:
        from bitacora.ui.screens import HelpScreen

        self.push_screen(HelpScreen())

    def action_kill(self) -> None:
        run = self.current()
        if not run or not run.proc or self.source.health not in ("ready", "partial"):
            self.notify(t("n_not_running"), severity="warning")
            return

        def done(ok: bool | None) -> None:
            if not ok:
                return
            pid = run.proc.pid
            created = run.proc.create
            self.run_worker(self.kill_process(pid, created), exclusive=True, group="kill")

        self.push_screen(ConfirmKill(run), done)

    async def kill_process(self, pid: int, created: float) -> None:
        try:
            n = await asyncio.to_thread(procs.kill_tree, pid, expected_create=created)
        except Exception as e:
            self.notify(t("n_kill_failed", e=e), severity="error")
        else:
            self.notify(t("n_killed", pid=pid, n=n))
        finally:
            self.refresh_data()

    def _open(self, path: str | None, what: str) -> None:
        if not path or not os.path.exists(path):
            self.notify(t("n_no_file", what=t(what)), severity="warning")
            return
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def action_open_result(self) -> None:
        run = self.current()
        self._open(run and run.out, "what_result")

    def action_open_log(self) -> None:
        run = self.current()
        self._open(run and run.log, "what_log")

    def copy_resume_text(self, command: str) -> None:
        if os.name == "nt":
            subprocess.run(["clip"], input=command.encode("utf-16le"), check=True)
        else:
            self.copy_to_clipboard(command)

    def action_copy_resume(self) -> None:
        run = self.current()
        cmd = run and self.resume_cmd(run)
        if not cmd:
            self.notify(t("n_no_sid"), severity="warning")
            return
        try:
            self.copy_resume_text(cmd)
        except Exception as error:
            self.push_screen(CopyResumeScreen(cmd, self.copy_resume_text, error=error))
        else:
            self.notify(t("n_copied"))



def create_app(*args, **kw) -> Bitacora:
    return Bitacora(*args, **kw)
