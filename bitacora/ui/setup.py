"""Home and setup screen: the masthead, which view to start in, and a few preferences."""
from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from bitacora import i18n
from bitacora.i18n import t
from bitacora.model import Run
from bitacora.ui import render as R
from bitacora.ui.masthead import Masthead
from bitacora.views import View, counts

HOURS = [6, 12, 24, 48, 72, 168]


def view_name(v: View) -> str:
    return t(v.name) if v.builtin else v.name


def view_desc(v: View) -> str:
    return t(v.name + "_d") if v.builtin else t("view_custom_d")


@dataclass
class Settings:
    view: str
    lang: str  # "auto", "en" or "es"
    hours: float
    notify: bool
    theme: str


class Choice(Widget, can_focus=True):
    """A setting you change with ←/→."""

    DEFAULT_CSS = """
    Choice { height: 1; padding: 0 2; }
    Choice:focus { background: $boost; }
    """
    BINDINGS = [
        Binding("left", "step(-1)", show=False),
        Binding("right", "step(1)", show=False),
        Binding("up", "app.focus_previous", show=False),
        Binding("down", "app.focus_next", show=False),
    ]

    class Changed(Message):
        def __init__(self, choice: Choice):
            super().__init__()
            self.choice = choice

    def __init__(self, label: str, options: list[tuple[object, str]], value: object, **kw):
        super().__init__(**kw)
        self.label = label
        self.options = options
        values = [v for v, _ in options]
        self.index = values.index(value) if value in values else 0

    @property
    def value(self) -> object:
        return self.options[self.index][0]

    def action_step(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.options)
        self.refresh()
        self.post_message(self.Changed(self))

    def render(self) -> Text:
        tx = Text()
        tx.append(f"{self.label:<24}", style="bold" if self.has_focus else R.DIM)
        tx.append("◂ ", style=R.DIM)
        tx.append(self.options[self.index][1], style=f"bold {R.GREEN}" if self.has_focus else "")
        tx.append(" ▸", style=R.DIM)
        return tx


class ViewList(OptionList):
    """The views list; ↓ past the last view moves on to the settings below."""

    def action_cursor_down(self) -> None:
        if self.highlighted is not None and self.highlighted >= self.option_count - 1:
            self.screen.focus_next()
            return
        super().action_cursor_down()


class SetupScreen(Screen[Settings | None]):
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("enter", "save", show=False),
        Binding("ctrl+s", "save", show=False),
    ]
    DEFAULT_CSS = """
    SetupScreen { align: center top; background: $background; }
    #setup-body { width: 110; max-width: 100%; height: auto; padding: 0 2; }
    #setup-head { margin: 1 0 0 0; }
    #setup-views { height: auto; max-height: 10; margin: 1 0; border: round $panel-lighten-2; }
    #setup-views:focus { border: round $accent; }
    #setup-note, #setup-hint { height: auto; margin: 1 0 0 0; }
    """

    def __init__(self, views: list[View], runs: list[Run] | None, current: Settings, themes: list[str],
                 first_run: bool, found: list[str]):
        super().__init__()
        self.views = views
        self.runs = runs
        self.current = current
        self.themes = themes
        self.first_run = first_run
        self.found = found
        self.original_theme = current.theme
        self.original_lang = i18n.lang()

    def compose(self) -> ComposeResult:
        yield Masthead(id="masthead")
        with Vertical(id="setup-body"):
            head = Text(t("setup_first") if self.first_run else t("setup_title"), style="bold")
            if self.found:
                head.append("    " + t("picker_found", where=", ".join(self.found)), style=R.DIM)
            yield Static(head, id="setup-head")
            yield ViewList(id="setup-views")
            c = self.current
            yield Choice(t("set_lang"), [("auto", t("lang_auto")), ("en", "English"), ("es", "Español")],
                         c.lang, id="set-lang")
            hours = sorted(set(HOURS) | {int(c.hours) if c.hours == int(c.hours) else c.hours})
            yield Choice(t("set_hours"), [(h, t("hours_n", h=f"{h:g}")) for h in hours], c.hours, id="set-hours")
            yield Choice(t("set_notify"), [(True, t("on")), (False, t("off"))], c.notify, id="set-notify")
            yield Choice(t("set_theme"), [(name, name) for name in self.themes], c.theme, id="set-theme")
            yield Static("", id="setup-note")
            yield Static(Text(t("setup_hint"), style=R.DIM), id="setup-hint")

    def on_mount(self) -> None:
        self.fill(self.runs)
        self.query_one("#setup-views", OptionList).focus()

    def fill(self, runs: list[Run] | None) -> None:
        self.runs = runs
        ol = self.query_one("#setup-views", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        for v in self.views:
            line = Text()
            line.append("▸ " if v.id == self.current.view else "  ", style=f"bold {R.GREEN}")
            line.append(f"{view_name(v):<20}", style="bold")
            line.append(f"{view_desc(v):<46}", style=R.DIM)
            if runs is None:
                line.append(t("picker_loading"), style=R.DIM)
            else:
                n, live = counts(v, runs)
                line.append(t("picker_counts", n=n, live=live), style="" if n else R.DIM)
            ol.add_option(Option(line, id=v.id))
        ids = [v.id for v in self.views]
        ol.highlighted = keep if keep is not None else (
            ids.index(self.current.view) if self.current.view in ids else 0)
        note = self.query_one("#setup-note", Static)
        note.update(Text(t("picker_nothing"), style=R.YELLOW) if runs is not None and not runs else "")

    @on(Choice.Changed)
    async def preview(self, ev: Choice.Changed) -> None:
        if ev.choice.id == "set-theme":
            self.app.theme = ev.choice.value  # live preview
        elif ev.choice.id == "set-lang":  # redraw this screen in the new language right away
            lang = ev.choice.value
            i18n.set_lang(None if lang == "auto" else lang)
            self.current = self.result()
            await self.recompose()
            self.fill(self.runs)
            self.query_one("#set-lang", Choice).focus()

    @on(OptionList.OptionSelected)
    def chosen(self, ev: OptionList.OptionSelected) -> None:
        self.action_save()

    def result(self) -> Settings:
        ol = self.query_one("#setup-views", OptionList)
        view = self.views[ol.highlighted].id if ol.highlighted is not None else self.current.view
        return Settings(view=view, lang=self.query_one("#set-lang", Choice).value,
                        hours=self.query_one("#set-hours", Choice).value,
                        notify=self.query_one("#set-notify", Choice).value,
                        theme=self.query_one("#set-theme", Choice).value)

    def action_save(self) -> None:
        self.dismiss(self.result())

    def action_cancel(self) -> None:
        if self.first_run:  # Esc on the first run keeps the recommended defaults
            self.dismiss(self.current)
            return
        self.app.theme = self.original_theme
        i18n.set_lang(self.original_lang)
        self.dismiss(None)
