"""Kill confirmation dialog."""
from __future__ import annotations

from collections.abc import Callable

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static, TextArea

from bitacora.i18n import t
from bitacora.model import Run
from bitacora.ui import render as R


class ConfirmKill(ModalScreen[bool]):
    BINDINGS = [Binding("y,s", "yes", show=False), Binding("n,escape", "no", show=False)]

    def __init__(self, run: Run):
        super().__init__()
        self.run_ = run

    def compose(self) -> ComposeResult:
        with Grid(id="dialog"):
            yield Label(t("kill_q", label=self.run_.label, pid=self.run_.proc.pid), id="question", markup=False)
            yield Button(t("kill_yes"), variant="error", id="yes")
            yield Button(t("kill_no"), variant="primary", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed)
    def pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(ev.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class CopyResumeScreen(ModalScreen[None]):
    """A selectable fallback for a resume command when the system clipboard is unavailable."""

    DEFAULT_CSS = """
    CopyResumeScreen { align: center middle; }
    #copy-card { width: 90%; max-width: 72; height: auto; max-height: 100%; padding: 1 2;
                 border: round $accent; background: $surface; }
    #copy-command { width: 1fr; height: 4; max-height: 35%; }
    #copy-note, #copy-manual, #copy-error { height: auto; }
    #copy-error { color: $error; }
    #copy-buttons { grid-size: 2; grid-columns: 1fr 1fr; grid-gutter: 0 1; height: 3; margin-top: 1; }
    #copy-buttons Button { width: 100%; }
    """
    BINDINGS = [Binding("escape", "dismiss", show=False)]

    def __init__(self, command: str, copy_command: Callable[[str], None], error: Exception | None = None):
        super().__init__()
        self.command = command
        self.copy_command = copy_command
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="copy-card"):
            yield Label(t("desktop_copy_title"))
            yield Static(t("desktop_copy_manual"), id="copy-manual")
            yield TextArea(self.command, id="copy-command", read_only=True, soft_wrap=True,
                           show_line_numbers=False)
            yield Static(t("desktop_copy_note"), id="copy-note")
            yield Static(t("n_copy_failed", e=self.error) if self.error else "", id="copy-error")
            with Grid(id="copy-buttons"):
                yield Button(t("desktop_copy_again"), variant="primary", id="copy-again")
                yield Button(t("desktop_close"), id="copy-close")

    def on_mount(self) -> None:
        command = self.query_one("#copy-command", TextArea)
        command.focus()
        command.select_all()

    @on(Button.Pressed)
    def pressed(self, ev: Button.Pressed) -> None:
        if ev.button.id == "copy-close":
            self.dismiss()
        elif ev.button.id == "copy-again":
            try:
                self.copy_command(self.command)
            except Exception as error:
                self.query_one("#copy-error", Static).update(t("n_copy_failed", e=error))
            else:
                self.app.notify(t("n_copied"))
                self.dismiss()



class HelpScreen(ModalScreen):
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help-card { width: 90%; max-width: 100%; height: 90%; max-height: 100%;
                 background: $surface; border: round $accent; padding: 1 2; }
    #help-title { height: 1; }
    #help-body { height: 1fr; }
    #help-card Button { width: 100%; margin-top: 1; }
    #help-body .help-section { margin-top: 1; text-style: bold; color: $primary; }
    #help-body .help-copy { height: auto; }
    #help-body .help-keys { height: auto; color: $foreground; }
    """
    BINDINGS = [
        Binding("escape,question_mark,f1", "dismiss", show=False),
        Binding("pageup", "scroll_help_page_up", show=False, priority=True),
        Binding("pagedown", "scroll_help_page_down", show=False, priority=True),
        Binding("shift+up", "scroll_help_up", show=False, priority=True),
        Binding("shift+down", "scroll_help_down", show=False, priority=True),
        Binding("shift+home", "scroll_help_home", show=False, priority=True),
        Binding("shift+end", "scroll_help_end", show=False, priority=True),
    ]

    def compose(self):
        with Vertical(id="help-card"):
            yield Label(t("desktop_help"), id="help-title")
            with VerticalScroll(id="help-body"):
                yield Static(t("terminal_help_intro"), id="help-intro", classes="help-copy")
                yield Static(t("terminal_help_sort"), id="help-sort", classes="help-copy")
                yield Label(t("desktop_help_keys"), classes="help-section")
                yield Static(self.key_table(), id="help-keys", classes="help-keys")
                yield Label(t("terminal_help_questions_title"), classes="help-section")
                yield Static(t("terminal_help_questions"), classes="help-copy")
                yield Label(t("terminal_help_resume_title"), classes="help-section")
                yield Static(t("desktop_help_copy"), classes="help-copy")
            yield Button(t("desktop_close"), id="close-help")

    def on_mount(self) -> None:
        self.query_one("#help-body", VerticalScroll).focus()

    def action_scroll_help_page_up(self) -> None:
        self.scroll_help("page_up")

    def action_scroll_help_page_down(self) -> None:
        self.scroll_help("page_down")

    def action_scroll_help_up(self) -> None:
        self.scroll_help("up")

    def action_scroll_help_down(self) -> None:
        self.scroll_help("down")

    def action_scroll_help_home(self) -> None:
        self.scroll_help("home")

    def action_scroll_help_end(self) -> None:
        self.scroll_help("end")

    def scroll_help(self, how: str) -> None:
        body = self.query_one("#help-body", VerticalScroll)
        {
            "up": lambda: body.scroll_relative(y=-3, animate=False),
            "down": lambda: body.scroll_relative(y=3, animate=False),
            "page_up": lambda: body.scroll_page_up(animate=False),
            "page_down": lambda: body.scroll_page_down(animate=False),
            "home": lambda: body.scroll_home(animate=False),
            "end": lambda: body.scroll_end(animate=False),
        }[how]()

    @staticmethod
    def key_table() -> Table:
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(no_wrap=True, style=f"bold {R.CYAN}")
        table.add_column(ratio=1)
        table.add_row(Text(t("terminal_help_key_column"), style="bold"),
                      Text(t("terminal_help_action_column"), style="bold"))

        def section(key: str) -> None:
            table.add_row(Text(t(key), style=f"bold {R.PURPLE}"), "")

        def item(keys: str, label: str) -> None:
            table.add_row(keys, t(label))

        section("terminal_help_navigation_title")
        item("↑ ↓", "ft_move")
        item("Enter", "ft_detail")
        item("Esc", "ft_list")
        item("1–6", "ft_tabs")
        item("PgUp/PgDn, Shift+↑↓", "desktop_help_scroll")
        item("Shift+Home/End", "desktop_help_scroll")
        section("terminal_help_monitor_title")
        item("a", "key_live")
        item("v", "key_verbose")
        item("m", "desktop_plain")
        item("g", "key_maximize")
        item("f", "key_follow")
        item("w", "key_jump_question")
        section("terminal_help_sort_title")
        item("Click heading", "sort_click")
        item("< / >", "sort_step")
        item("Ctrl+R", "key_sort_reset")
        section("terminal_help_actions_title")
        item("o", "key_result")
        item("l", "key_log")
        item("c", "key_copy")
        item("k", "desktop_stop")
        section("terminal_help_app_title")
        item("s / p", "cmd_home")
        item("t", "key_theme")
        item("r", "key_refresh")
        item("Ctrl+P", "palette")
        item("? / F1", "desktop_help")
        item("q", "key_quit")
        return table

    @on(Button.Pressed)
    def close_help(self):
        self.dismiss()
