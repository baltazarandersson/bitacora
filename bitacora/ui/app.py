"""The Textual app. Build it with `create_app`, which labels the key bindings in the current language."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import DataTable, Footer, Markdown, RichLog, Static, TabbedContent, TabPane

from bitacora import procs
from bitacora.collector import Collector
from bitacora.i18n import t
from bitacora.model import MAX_EVENTS, Run
from bitacora.providers import Provider
from bitacora.store import Config, State
from bitacora.ui import render as R
from bitacora.ui.masthead import Beacon
from bitacora.ui.render import activity, agent_text, bar, pct_style, render_event, status_text, visible
from bitacora.ui.screens import ConfirmKill
from bitacora.ui.setup import Settings, SetupScreen, view_name
from bitacora.util import first_line, fmt_ago, fmt_dur, fmt_tokens, hhmm
from bitacora.views import DEFAULT_VIEW, View, all_views


def cell_key(value) -> tuple:
    """What a table cell looks like, to skip updates that would change nothing."""
    if isinstance(value, Text):
        return value.plain, str(value.style), tuple((sp.start, sp.end, str(sp.style)) for sp in value.spans)
    return (str(value),)


FIRST_PAINT = 150  # events drawn when a session is selected
LOG_ERR = re.compile(r"\b(error|failed|panic)\b", re.I)
THEMES = ["tokyo-night", "catppuccin-mocha", "nord", "gruvbox", "dracula", "monokai",
          "textual-dark", "rose-pine", "solarized-light", "textual-light"]


def bindings() -> list[Binding]:
    return [
        Binding("s", "setup", t("key_home")),
        Binding("h", "toggle_header", t("key_header")),
        Binding("a", "toggle_live", t("key_live")),
        Binding("v", "toggle_verbose", t("key_verbose")),
        Binding("k", "kill", t("key_kill")),
        Binding("c", "copy_resume", t("key_copy")),
        Binding("q", "quit", t("key_quit")),
        Binding("p", "setup", show=False),
        Binding("f", "toggle_follow", t("key_follow"), show=False),
        Binding("o", "open_result", t("key_result"), show=False),
        Binding("l", "open_log", t("key_log"), show=False),
        Binding("t", "next_theme", t("key_theme"), show=False),
        Binding("r", "refresh_now", t("key_refresh"), show=False),
        Binding("1", "tab('t-act')", show=False),
        Binding("2", "tab('t-msg')", show=False),
        Binding("3", "tab('t-files')", show=False),
        Binding("4", "tab('t-log')", show=False),
        Binding("5", "tab('t-res')", show=False),
        Binding("6", "tab('t-info')", show=False),
        Binding("escape", "focus_table", t("key_list"), show=False),
        Binding("pageup", "scroll_detail('page_up')", show=False, priority=True),
        Binding("pagedown", "scroll_detail('page_down')", show=False, priority=True),
        Binding("shift+up", "scroll_detail('up')", show=False, priority=True),
        Binding("shift+down", "scroll_detail('down')", show=False, priority=True),
        Binding("shift+home", "scroll_detail('home')", show=False, priority=True),
        Binding("shift+end", "scroll_detail('end')", show=False, priority=True),
    ]


class Bitacora(App):
    TITLE = "Bitácora"
    CSS = """
    Screen { background: $background; }
    #beacon { background: $boost; }
    #beacon:hover { background: $boost-lighten-1; }
    #hud { height: 6; padding: 0 1; background: $boost; border-bottom: hkey $primary; }
    .pane-scroll { height: 1fr; background: $background; }
    #detail RichLog, .pane-scroll { border: blank; }
    #detail RichLog:focus, .pane-scroll:focus, #files:focus { border: round $accent; }
    #runs { height: auto; max-height: 40%; min-height: 5; border: round $panel-lighten-2; }
    #runs:focus { border: round $accent; }
    #detail { height: 1fr; }
    #detail RichLog, #detail Markdown, #info { padding: 0 1; background: $background; }
    #files { border: none; }
    ConfirmKill { align: center middle; }
    #dialog { grid-size: 2; grid-gutter: 1 2; padding: 1 2; width: 64; height: 11;
              border: thick $error; background: $surface; }
    #question { column-span: 2; width: 1fr; content-align: center middle; height: 3; }
    #dialog Button { width: 100%; }
    """
    COLUMNS = ["status", "agent", "name", "repo", "model", "mode", "dur", "last", "cmds", "files",
               "tok", "ctx", "pid"]

    def __init__(self, providers: list[Provider], config: Config, state: State, view: str | None = None,
                 live_only: bool = False, scan_procs: bool = True, refresh_secs: float = 2.0,
                 lang: str = "auto", collector: Collector | None = None):
        super().__init__()
        self.lang_setting = lang
        self.providers = providers
        self.config = config
        self.state = state
        self.collector = collector or Collector(providers, state, config.hours, scan_procs)
        self.collector.state, self.collector.hours = state, config.hours
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
        self.follow = True
        self.verbose = False
        self.tick = 0
        self._shown: dict[str, tuple] = {}
        self._prev_status: dict[str, str] = {}
        self._setup: SetupScreen | None = None
        self._collecting = False
        self._cells: dict[str, list] = {}
        self._detail_timer = None
        self.header_open = state.get("header", True)
        self.header_auto = "header" not in state.data  # folds itself on short terminals until you choose

    @property
    def view(self) -> View:
        return next(v for v in self.views if v.id == self.view_id)

    def compose(self) -> ComposeResult:
        yield Beacon(id="beacon")
        yield Static(id="hud")
        with Vertical():
            yield DataTable(id="runs", cursor_type="row", zebra_stripes=True)
            with TabbedContent(id="detail", initial="t-act"):
                with TabPane(t("tab_act"), id="t-act"):
                    yield RichLog(id="act", wrap=True, max_lines=MAX_EVENTS * 2, markup=False)
                with TabPane(t("tab_msg"), id="t-msg"):
                    with VerticalScroll(id="msgs-scroll", classes="pane-scroll"):
                        yield Markdown(id="msgs")
                with TabPane(t("tab_files"), id="t-files"):
                    yield DataTable(id="files", cursor_type="row", zebra_stripes=True)
                with TabPane(t("tab_log"), id="t-log"):
                    yield RichLog(id="log", wrap=True, max_lines=2000, markup=False)
                with TabPane(t("tab_res"), id="t-res"):
                    with VerticalScroll(id="res-scroll", classes="pane-scroll"):
                        yield Markdown(id="res")
                with TabPane(t("tab_info"), id="t-info"):
                    with VerticalScroll(id="info-scroll", classes="pane-scroll"):
                        yield Static(id="info")
        yield Footer()

    def on_mount(self) -> None:
        theme = self.state.get("theme")
        self.theme = theme if theme in self.available_themes else "tokyo-night"
        R.apply_theme(self.get_css_variables())
        self.theme_changed_signal.subscribe(self, self.theme_changed)
        table = self.q("#runs", DataTable)
        for key in self.COLUMNS:
            table.add_column(t(f"col_{key}"), key=key)
        ft = self.q("#files", DataTable)
        for key, label in (("kind", t("fcol_kind")), ("plus", "+"), ("minus", "−"), ("n", t("fcol_n")),
                           ("ts", t("fcol_ts")), ("path", t("fcol_path"))):
            ft.add_column(label, key=key)
        table.focus()
        for err in self.config.errors:
            self.notify(t("n_config", e=err), severity="warning", timeout=10)
        self.render_hud()
        self.refresh_data()
        self.set_interval(self.refresh_secs, self.refresh_data)
        self.set_interval(1, self.clock)
        self.apply_header()
        if self.first_run:
            self.action_setup()

    def theme_changed(self, _theme) -> None:
        if not self.screen_stack:
            return
        R.apply_theme(self.get_css_variables())
        self._cells.clear()
        self.render_hud()
        self.render_detail(force=True)
        self.show_runs()

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
        except NoMatches:
            pass

    # ---- header
    def working(self) -> bool:
        return self.all_runs is None or any(r.status in ("running", "active", "starting") for r in self.runs)

    def apply_header(self) -> None:
        try:
            self.q("#hud", Static).display = self.header_open
        except NoMatches:
            return
        self.render_hud()

    def on_resize(self, event) -> None:
        # Fold the header on short terminals, unless you chose yourself.
        if self.header_auto:
            want = event.size.height >= 32
            if want != self.header_open:
                self.header_open = want
                self.apply_header()

    def action_toggle_header(self) -> None:
        self.header_open = not self.header_open
        self.header_auto = False
        self.state.set("header", self.header_open)
        self.state.save()
        self.apply_header()

    @on(Beacon.Clicked)
    def beacon_clicked(self) -> None:
        self.action_toggle_header()

    def summary(self) -> list[tuple[str, str]]:
        """The folded header: counts and the selected session, on the beacon line."""
        stats = self.stats()
        parts: list[tuple[str, str]] = [(t("hud_n_running", n=stats["running"]),
                                         f"bold {R.GREEN}" if stats["running"] else R.DIM)]
        if stats["ask"]:
            parts.append(("  " + t("hud_n_ask", n=stats["ask"]), f"bold {R.YELLOW}"))
        if stats["idle"]:
            parts.append(("  " + t("hud_n_idle", n=stats["idle"]), R.BLUE))
        parts.append(("  " + t("hud_n_done", n=stats["done"]), R.GREEN if stats["done"] else R.DIM))
        if stats["cut"]:
            parts.append(("  " + t("hud_n_cut", n=stats["cut"]), R.RED))
        run = self.current()
        if run:
            parts += [("   ▸ ", R.DIM), (first_line(run.label, 32), "bold"), ("  " + t(f"st_{run.status}"),
                                                                              R.STATUS_STYLE.get(run.status, R.DIM))]
        return parts

    def stats(self) -> dict[str, int]:
        stats = {k: 0 for k in ("running", "ask", "idle", "done", "cut")}
        for r in self.runs:
            st = r.status
            key = {"active": "running", "starting": "running", "aborted": "cut", "interrupted": "cut"}.get(st, st)
            if key in stats:
                stats[key] += 1
        return stats

    # ---- command palette
    def get_system_commands(self, screen):
        yield SystemCommand(t("cmd_home"), t("cmd_home_d"), self.action_setup)
        for v in self.views:
            yield SystemCommand(f"{t('cmd_view')}: {view_name(v)}", t("cmd_view_d"),
                                lambda vid=v.id: self.switch_view(vid))
        yield SystemCommand(t("cmd_header"), t("cmd_header_d"), self.action_toggle_header)
        yield SystemCommand(t("cmd_live"), t("cmd_live_d"), self.action_toggle_live)
        yield SystemCommand(t("cmd_verbose"), t("cmd_verbose_d"), self.action_toggle_verbose)
        yield SystemCommand(t("cmd_follow"), t("cmd_follow_d"), self.action_toggle_follow)
        for code, name in (("en", "English"), ("es", "Español")):
            if code != self.lang_setting:
                yield SystemCommand(f"{t('cmd_lang')}: {name}", t("cmd_lang_d"), lambda c=code: self.switch_lang(c))
        yield from super().get_system_commands(screen)

    def switch_view(self, vid: str) -> None:
        self.view_id = vid
        self.state.set("view", vid)
        self.state.save()
        self.row_keys = []
        self.show_runs()

    def switch_lang(self, lang: str) -> None:
        self.state.set("lang", lang)
        self.state.save()
        self.exit("relaunch")

    # ---- data
    def refresh_data(self) -> None:
        # A cold read can take longer than the refresh interval; never stack collections.
        if not self._collecting:
            self._collecting = True
            self._collect()

    @work(thread=True)
    def _collect(self) -> None:
        try:
            runs = self.collector.collect()
            self.call_from_thread(self.apply, runs)
        finally:
            self._collecting = False
        self._cells: dict[str, list] = {}

    def visible_runs(self) -> list[Run]:
        return [r for r in self.all_runs or [] if self.view.matches(r) and (r.live or not self.live_only)]

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
                for i, (col, val) in enumerate(zip(self.COLUMNS, rows[k], strict=True)):
                    if i >= len(old) or old[i] != new[i]:
                        table.update_cell(k, col, val)
                self._cells[k] = new
        if self.selected not in keys:
            self.selected = (keys[table.cursor_row] if 0 <= table.cursor_row < len(keys) else keys[0]) \
                if keys else None
        self.render_hud()
        self.render_detail()

    def notify_changes(self, runs: list[Run]) -> None:
        """Toast and bell when a session we saw live finishes, stops or asks something."""
        if not self.config.notify:
            return
        ring = False
        for r in runs:
            before, now = self._prev_status.get(r.key), r.status
            if before is None or before == now:
                continue
            msg = None
            if now == "ask":
                msg, sev = t("n_ask", label=r.label), "warning"
            elif before in ("running", "active", "starting") and now in ("done", "idle"):
                msg, sev = t("n_done", label=r.label), "information"
            elif before in ("running", "active", "starting") and now in ("aborted", "interrupted"):
                msg, sev = t("n_stopped", label=r.label), "error"
            if msg:
                self.notify(msg, severity=sev, timeout=8)
                ring = True
        if ring:
            self.bell()

    def row_cells(self, r: Run) -> list:
        s = r.session
        model = r.model or "-"
        if s and s.ctx_window:
            frac = s.ctx_used / s.ctx_window
            ctx = Text(f"{100 * frac:.0f}%", style=pct_style(frac))
        elif s and s.ctx_used:
            ctx = Text(fmt_tokens(s.ctx_used), style=R.DIM)
        else:
            ctx = Text("-", style=R.DIM)
        if not s:
            cmds = Text("-", style=R.DIM)
        else:
            cmds = Text(str(s.n_cmds))
            if s.n_fail:
                cmds.append(f" {s.n_fail}✗", style=R.RED)
        return [status_text(r), agent_text(r), Text(first_line(r.label, 32), style="bold" if r.live else ""), r.repo,
                f"{model} {r.effort}" if r.effort else model, r.sandbox or "-", fmt_dur(r.duration),
                fmt_ago(s.last_ts if s else None), cmds, str(len(s.files)) if s else "-",
                fmt_tokens(s.tokens if s else 0), ctx, Text(str(r.proc.pid), style=R.DIM) if r.proc else ""]

    # ---- HUD
    def quota_text(self) -> Text:
        tx = Text()
        for prov, rate in self.collector.quotas.items():
            if tx:
                tx.append("      ")
            tx.append(f"{prov} ", style=f"bold {R.DIM}")
            first = True
            for k in ("primary", "secondary"):
                w = rate.get(k)
                if not w:
                    continue
                mins = w.get("window_minutes") or 0
                name = f"{mins // 60}h" if mins < 1440 else t("quota_week") if mins == 10080 else f"{mins // 1440}d"
                pct = w.get("used_percent") or 0
                if not first:
                    tx.append("    ")
                first = False
                tx.append(f"{name} ", style="bold")
                tx.append(bar(pct / 100, 10))
                tx.append(f" {pct:.0f}%")
                if w.get("resets_at"):
                    fmt = "%H:%M" if mins < 1440 else "%d/%m %H:%M"
                    when = datetime.fromtimestamp(w["resets_at"]).strftime(fmt)
                    tx.append(" (" + t("quota_renews", t=when) + ")", style=R.DIM)
        return tx or Text(t("quota_unknown"), style=R.DIM)

    def render_hud(self) -> None:
        if not self.screen_stack:
            return
        open_ = getattr(self, "header_open", True)
        self.q("#beacon", Beacon).set_state(self.working(), None if open_ else self.summary())
        if not open_:
            return
        runs = self.runs
        stats = self.stats()
        lbl = f"bold {R.DIM}"
        g = Table.grid(padding=(0, 2), expand=True)
        g.add_column(width=12, no_wrap=True, justify="right")
        g.add_column(ratio=1, no_wrap=True, overflow="ellipsis")

        def add(label: str, row: Text) -> None:
            g.add_row(Text(label, style=lbl), row)

        row = Text()
        n_run = stats["running"]
        row.append(t("hud_n_running", n=n_run), style=f"bold {R.GREEN}" if n_run else R.DIM)
        if stats["ask"]:
            row.append("   " + t("hud_n_ask", n=stats["ask"]), style=f"bold {R.YELLOW}")
        if stats["idle"]:
            row.append("   " + t("hud_n_idle", n=stats["idle"]), style=R.BLUE)
        row.append("   " + t("hud_n_done", n=stats["done"]), style=R.GREEN if stats["done"] else R.DIM)
        if stats["cut"]:
            row.append("   " + t("hud_n_cut", n=stats["cut"]), style=R.RED)
        row.append(f"      {t('hud_view')} ", style=R.DIM)
        row.append(view_name(self.view), style="bold")
        row.append(" · ", style=R.DIM)
        row.append(t("hud_live_only") if self.live_only else t("hud_window", h=f"{self.config.hours:g}"))
        if self.verbose:
            row.append("   " + t("hud_verbose"), style=R.CYAN)
        if not self.follow:
            row.append("   " + t("hud_paused"), style=R.YELLOW)
        add(t("hud_runs"), row)
        add(t("hud_quota"), self.quota_text())

        run = self.current()
        if run is None:
            msg = t("hud_searching") if self.all_runs is None else t("hud_empty") if not runs else t("hud_none")
            add(t("hud_selected"), Text(msg, style=R.DIM))
        else:
            s = run.session
            row = Text()
            row.append(run.label, style="bold")
            row.append("   ")
            row.append(status_text(run))
            row.append("   ")
            row.append(agent_text(run))
            row.append(f"   {t('hud_folder')} ", style=R.DIM)
            row.append(run.repo, style=R.CYAN)
            row.append(f"   {t('hud_model')} ", style=R.DIM)
            row.append((run.model or "?") + (f" ({run.effort})" if run.effort else ""))
            row.append(f"   {t('hud_duration')} ", style=R.DIM)
            row.append(fmt_dur(run.duration))
            add(t("hud_selected"), row)
            if s:
                row = Text()
                row.append(f"{t('hud_tokens')} ", style=R.DIM)
                row.append(fmt_tokens(s.tokens))
                if s.ctx_window:
                    frac = s.ctx_used / s.ctx_window
                    row.append(f"   {t('hud_context')} ", style=R.DIM)
                    row.append(bar(frac, 10))
                    row.append(f" {100 * frac:.0f}%")
                elif s.ctx_used:
                    row.append(f"   {t('hud_context')} ", style=R.DIM)
                    row.append(fmt_tokens(s.ctx_used))
                row.append(f"   {t('hud_commands')} ", style=R.DIM)
                row.append(str(s.n_cmds))
                if s.n_fail:
                    row.append(" " + t("hud_failed", n=s.n_fail), style=R.RED)
                row.append(f"   {t('hud_files')} ", style=R.DIM)
                row.append(str(len(s.files)))
                add(t("hud_usage"), row)
            now = Text()
            if run.live and run.status in ("running", "active"):
                now.append("● ", style=R.GREEN)
            now.append(activity(s, run))
            add(t("hud_now"), now)
        self.q("#hud", Static).update(g)

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
        sig = (run and run.key, id(s), self.verbose)
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
            for i, ev in enumerate(evs):
                if ev.seq <= written:
                    continue
                if ev.kind == "think" and ev is s.last_event and run.live:
                    break  # still growing; the HUD shows it
                if visible(ev, self.verbose):
                    prev_ev = evs[i - 1] if i else None
                    think = prev_ev if prev_ev and prev_ev.kind == "think" and not self.verbose else None
                    act.write(render_event(ev, self.verbose, think), expand=True, scroll_end=self.follow)
                written = ev.seq
        elif run and written == 0:
            act.write(Text(t("no_session_file"), style=R.DIM))
            written = -1
        self._shown["act"] = (sig, written)

    def render_messages(self, run: Run | None) -> None:
        s = run.session if run else None
        sig = (run and run.key, len(s.messages) if s else 0)
        if self._shown.get("msg") != sig:
            self._shown["msg"] = sig
            md = "\n\n---\n\n".join(f"**{hhmm(ts)}**\n\n{txt}" for ts, txt in (s.messages if s else [])[-60:])
            self.q("#msgs", Markdown).update(md or t("no_messages"))

    def render_files(self, run: Run | None) -> None:
        s = run.session if run else None
        sig = (run and run.key, s.events_total if s else 0)
        if self._shown.get("files") == sig:
            return
        self._shown["files"] = sig
        ft = self.q("#files", DataTable)
        ft.clear()
        if s:
            for path, (kind, n, ts, plus, minus) in sorted(list(s.files.items()), key=lambda kv: -(kv[1][2] or 0)):
                ft.add_row(Text(kind, style=R.PURPLE), Text(f"+{plus}", style=R.GREEN),
                           Text(f"−{minus}", style=R.RED), str(n), hhmm(ts), path)

    def render_result(self, run: Run | None) -> None:
        s = run.session if run else None
        out = run.out if run else None
        try:
            ostat = os.stat(out) if out else None
        except OSError:
            ostat = None
        sig = (run and run.key, ostat.st_mtime if ostat else None, len(s.last_agent_message) if s else 0)
        if self._shown.get("res") == sig:
            return
        self._shown["res"] = sig
        shown_out = out or t("res_no_out")
        if ostat:
            body = f"_{out}_\n\n" + Path(out).read_text(encoding="utf-8", errors="replace")
        elif s and s.last_agent_message:
            body = t("res_last_msg", out=shown_out) + "\n\n" + s.last_agent_message
        else:
            body = t("res_none", out=shown_out)
        self.q("#res", Markdown).update(body)

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
        if prev is None or prev[0] != ident or size is None or size < prev[1]:
            log.clear()
            start = max(0, (size or 0) - 64_000)
        else:
            start = prev[1]
        if size is not None and path:
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read(size - start)
            block = Text()
            for line in data.decode("utf-8", "replace").splitlines():
                st = R.RED if LOG_ERR.search(line) else R.CYAN if line.startswith(("exec", "$ ")) else ""
                block.append(line + "\n", style=st)
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
        rows = [
            (t("inf_status"), t(f"st_{run.status}")),
            (t("inf_agent"), f"{run.provider} · {run.origin}"),
            (t("inf_session"), (s and s.session_id) or "-"),
            (t("inf_folder"), run.cwd or "-"),
            (t("inf_model"), f"{run.model or '-'}  effort={run.effort or '-'}"),
            (t("inf_mode"), run.sandbox or "-"),
            (t("inf_started"), datetime.fromtimestamp(run.start).strftime("%Y-%m-%d %H:%M:%S") if run.start else "-"),
            (t("inf_duration"), fmt_dur(run.duration)),
            (t("inf_tokens"), f"{s.tokens:,}" if s else "-"),
            (t("inf_context"), f"{s.ctx_used:,} / {s.ctx_window:,}" if s and s.ctx_window
             else f"{s.ctx_used:,}" if s and s.ctx_used else "-"),
            (t("inf_result"), run.out or "-"),
            (t("inf_log"), run.log or "-"),
            (t("inf_file"), str(s.path) if s else "-"),
            (t("inf_pid"), str(pi.pid) if pi else t("not_running")),
            (t("inf_command"), " ".join(pi.cmdline[1:]) if pi else "-"),
            (t("inf_resume"), self.resume_cmd(run) or "-"),
        ]
        if s and s.open_questions:
            rows.insert(1, (t("inf_question"), " | ".join(s.open_questions.values())))
        tx = Text()
        for k, v in rows:
            tx.append(f"{k:>18}  ", style="bold")
            tx.append(f"{v}\n")
        return tx

    # ---- events
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
        found = [p.label for p in self.providers if p.available()]
        themes = [n for n in THEMES if n in self.available_themes]
        self._setup = SetupScreen(self.views, self.all_runs, self.settings(), themes, self.first_run, found)

        def done(result: Settings | None) -> None:
            self._setup = None
            self.first_run = False
            if result is None:
                return
            relaunch = result.lang != self.lang_setting
            self.view_id = result.view
            self.config.hours = self.collector.hours = result.hours
            self.config.notify = result.notify
            self.theme = result.theme
            for key, value in (("view", result.view), ("lang", result.lang), ("hours", result.hours),
                               ("notify", result.notify), ("theme", result.theme)):
                self.state.set(key, value)
            self.state.save()
            if relaunch:
                self.exit("relaunch")
                return
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

    def action_toggle_follow(self) -> None:
        self.follow = not self.follow
        self.notify(t("n_follow_on") if self.follow else t("n_follow_off"))
        self.render_hud()

    def action_next_theme(self) -> None:
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

    def action_kill(self) -> None:
        run = self.current()
        if not run or not run.proc:
            self.notify(t("n_not_running"), severity="warning")
            return

        def done(ok: bool | None) -> None:
            if not ok:
                return
            try:
                n = procs.kill_tree(run.proc.pid)
                self.notify(t("n_killed", pid=run.proc.pid, n=n))
            except Exception as e:
                self.notify(t("n_kill_failed", e=e), severity="error")
            self.refresh_data()

        self.push_screen(ConfirmKill(run), done)

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

    def action_copy_resume(self) -> None:
        run = self.current()
        cmd = run and self.resume_cmd(run)
        if not cmd:
            self.notify(t("n_no_sid"), severity="warning")
            return
        try:
            if os.name == "nt":
                subprocess.run(["clip"], input=cmd.encode("utf-16le"), check=True)
            else:
                self.copy_to_clipboard(cmd)
            self.notify(t("n_copied"))
        except Exception as e:
            self.notify(t("n_copy_failed", e=e), severity="error")


def create_app(*args, **kw) -> Bitacora:
    """An app whose key bindings are labelled in the language set right now."""
    cls = type("Bitacora", (Bitacora,), {"BINDINGS": bindings()})
    return cls(*args, **kw)
