"""Home and setup screen: the masthead, which view to start in, and a few preferences."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.cells import cell_len
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from bitacora import __version__, i18n
from bitacora.i18n import t
from bitacora.model import Run
from bitacora.ui import render as R
from bitacora.ui.masthead import Masthead
from bitacora.views import View, counts

HOURS = [6, 12, 24, 48, 72]


class SetupScroll(VerticalScroll, can_focus=False):
    """Let focused settings scroll into view without adding the container to the focus order."""


def view_name(v: View) -> str:
    return t(v.name) if v.builtin else v.name


def view_desc(v: View) -> str:
    return t(v.name + "_d") if v.builtin else t("view_custom_d")


def fit_cells(text: str, width: int, style: str) -> Text:
    """The text cut with an ellipsis, then padded, to exactly `width` cells. Rich's pad= leaves the length stale,
    which Textual then cuts the row by, so the padding is added as plain text here."""
    if width <= 0:
        return Text()
    out = Text(text, style=style, no_wrap=True)
    out.truncate(width, overflow="ellipsis")
    out.append(" " * (width - cell_len(out.plain)))
    return out


def centered(text: str, width: int) -> str:
    """Pads a value to `width` cells with the text in the middle, so the arrows of every selector line up."""
    room = max(0, width - cell_len(text))
    return " " * (room // 2) + text + " " * (room - room // 2)


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

    def __init__(self, label: Callable[[], str], options: list[tuple[object, Callable[[], str]]], value: object,
                 **kw):
        """Labels are callables so a language switch only needs a redraw."""
        super().__init__(**kw)
        self.label = label
        self.options = options
        values = [v for v, _ in options]
        self.index = values.index(value) if value in values else 0
        self.value_width = 0  # cells; the screen sets it to the widest option of every selector

    @property
    def value(self) -> object:
        return self.options[self.index][0]

    def option_texts(self) -> list[str]:
        return [text() for _, text in self.options]

    def action_step(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.options)
        self.refresh()
        self.post_message(self.Changed(self))

    def render(self) -> Text:
        tx = Text()
        tx.append(f"{self.label():<24}", style="bold" if self.has_focus else R.DIM)
        tx.append("◂ ", style=R.DIM)
        tx.append(centered(self.options[self.index][1](), self.value_width),
                  style=f"bold {R.GREEN}" if self.has_focus else "")
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
    #setup-scroll { width: 100%; height: 100%; align: center top; }
    #setup-body { width: 110; max-width: 100%; height: auto; padding: 0 2; }
    #setup-head { margin: 1 0 0 0; }
    #setup-tagline { height: auto; margin: 0 0 1 0; }
    #setup-first { height: auto; margin: 0 0 1 0; }
    #setup-views { height: auto; max-height: 10; margin: 1 0; border: round $panel-lighten-2; text-wrap: nowrap; }
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
        self._theme_timer = None
        self._inked: int | None = None  # the row drawn in the cursor's ink, see fill()

    def compose(self) -> ComposeResult:
        with SetupScroll(id="setup-scroll"):
            yield Masthead(id="masthead")
            with Vertical(id="setup-body"):
                yield Static(self.head(), id="setup-head")
                yield Static(self.tagline(), id="setup-tagline")
                first = Static(t("setup_first") if self.first_run else "", id="setup-first")
                first.display = self.first_run
                yield first
                yield ViewList(id="setup-views")
                c = self.current
                yield Choice(lambda: t("set_lang"), [("auto", lambda: t("lang_auto")), ("en", lambda: "English"),
                                                      ("es", lambda: "Español")], c.lang, id="set-lang")
                hours = sorted(set(HOURS) | {int(c.hours) if c.hours == int(c.hours) else c.hours})
                yield Choice(lambda: t("set_hours"), [(h, lambda h=h: t("hours_n", h=f"{h:g}")) for h in hours],
                             c.hours, id="set-hours")
                yield Choice(lambda: t("set_notify"), [(True, lambda: t("on")), (False, lambda: t("off"))],
                             c.notify, id="set-notify")
                yield Choice(lambda: t("set_theme"), [(name, lambda n=name: n) for name in self.themes], c.theme,
                             id="set-theme")
                yield Static("", id="setup-note")
                yield Static(Text(t("setup_hint"), style=R.DIM), id="setup-hint")

    def head(self) -> Text:
        head = Text(t("setup_title"), style="bold")
        if self.found:
            head.append("    " + t("picker_found", where=", ".join(self.found)), style=R.DIM)
        return head

    def tagline(self) -> Text:
        return Text(f"{t('tagline')}  ·  v{__version__}", style=R.DIM)

    def relabel(self) -> None:
        """Redraw every text in the current language without rebuilding the screen (the scene keeps going)."""
        self.query_one("#setup-head", Static).update(self.head())
        self.query_one("#setup-tagline", Static).update(self.tagline())
        first = self.query_one("#setup-first", Static)
        first.update(t("setup_first") if self.first_run else "")
        first.display = self.first_run
        self.query_one("#setup-hint", Static).update(Text(t("setup_hint"), style=R.DIM))
        self.fit_choices()
        self.fill(self.runs)

    def fit_choices(self) -> None:
        """Every selector is as wide as the widest option of any selector, in the current language."""
        choices = list(self.query(Choice))
        width = max((cell_len(text) for choice in choices for text in choice.option_texts()), default=0)
        for choice in choices:
            choice.value_width = width
            choice.refresh()

    def on_mount(self) -> None:
        self.fit_scene()
        self.fit_choices()
        self.fill(self.runs)
        self.query_one("#setup-views", OptionList).focus()

    def on_resize(self) -> None:
        if self.is_mounted:  # the description takes whatever room the width leaves, measured once the layout is done
            self.fit_scene()
            self.call_after_refresh(self.fill, self.runs)

    def fit_scene(self) -> None:
        """Give the choices and help room before allocating rows to the scene."""
        scene = self.query_one("#masthead", Masthead)
        height = max(0, min(16, self.size.height - 24))
        scene.display = height > 0
        scene.styles.height = height

    def fill(self, runs: list[Run] | None) -> None:
        """One row per view: name (as wide as the longest name), description (the room left, cut with an ellipsis)
        and the counts, right-aligned to the widest counts text. Nothing wraps, whatever the width."""
        self.runs = runs
        ol = self.query_one("#setup-views", OptionList)
        ids = [v.id for v in self.views]
        keep = ol.highlighted
        target = keep if keep is not None else (ids.index(self.current.view) if self.current.view in ids else 0)
        self._inked = target  # set before clearing, so the rebuild does not ask for another rebuild
        # The highlighted row is drawn in the cursor's own ink: its explicit colours would otherwise win over it.
        ink = self.app.get_css_variables()["block-cursor-foreground"]
        ol.clear_options()
        counts_of = []
        for v in self.views:
            if runs is None:
                counts_of.append((t("picker_loading"), R.DIM))
            else:
                n, live = counts(v, runs)
                counts_of.append((t("picker_counts", n=n, live=live), "" if n else R.DIM))
        names = [view_name(v) for v in self.views]
        name_w = max((cell_len(name) for name in names), default=0)
        count_w = max((cell_len(text) for text, _ in counts_of), default=0)
        width = ol.scrollable_content_region.width or max(20, self.size.width - 4)
        name_w = min(name_w, max(4, width - 6 - count_w))  # too narrow for every column: the name gives way first
        room = max(0, width - 2 - name_w - 2 - 2 - count_w)  # marker, name, gap, gap, counts
        for i, (v, name, (text, style)) in enumerate(zip(self.views, names, counts_of, strict=True)):
            on = i == target
            line = Text(no_wrap=True)
            line.append("▸ " if v.id == self.current.view else "  ", style=f"bold {ink if on else R.GREEN}")
            line.append_text(fit_cells(name, name_w, f"bold {ink}" if on else "bold"))
            line.append("  ")
            line.append_text(fit_cells(view_desc(v), room, ink if on else R.DIM))
            line.append("  ")
            line.append(" " * (count_w - cell_len(text)) + text, style=ink if on else style)
            ol.add_option(Option(line, id=v.id))
        ol.highlighted = target
        note = self.query_one("#setup-note", Static)
        note.update(Text(t("picker_nothing"), style=R.YELLOW) if runs is not None and not runs else "")

    @on(Choice.Changed)
    def preview(self, ev: Choice.Changed) -> None:
        if ev.choice.id == "set-theme":
            # Restyling the whole app is the expensive part: wait until the arrow key rests.
            if self._theme_timer:
                self._theme_timer.stop()
            value = ev.choice.value
            self._theme_timer = self.set_timer(0.18, lambda: setattr(self.app, "theme", value))
        elif ev.choice.id == "set-lang":
            lang = ev.choice.value
            i18n.set_lang(None if lang == "auto" else lang)
            self.relabel()
            self.app._stale = True  # the hidden main screen catches up when we leave

    @on(OptionList.OptionSelected)
    def chosen(self, ev: OptionList.OptionSelected) -> None:
        self.action_save()

    @on(OptionList.OptionHighlighted, "#setup-views")
    def highlight_moved(self, ev: OptionList.OptionHighlighted) -> None:
        if ev.option_index is not None and ev.option_index != self._inked:
            self.fill(self.runs)  # the new highlighted row takes the cursor's ink

    def result(self) -> Settings:
        ol = self.query_one("#setup-views", OptionList)
        view = self.views[ol.highlighted].id if ol.highlighted is not None else self.current.view
        return Settings(view=view, lang=self.query_one("#set-lang", Choice).value,
                        hours=self.query_one("#set-hours", Choice).value,
                        notify=self.query_one("#set-notify", Choice).value,
                        theme=self.query_one("#set-theme", Choice).value)

    def action_save(self) -> None:
        if self._theme_timer:
            self._theme_timer.stop()
        result = self.result()
        self.app.theme = result.theme
        self.dismiss(result)

    def action_cancel(self) -> None:
        if self.first_run:  # Esc on the first run keeps what is on screen
            self.action_save()
            return
        if self._theme_timer:
            self._theme_timer.stop()
        self.app.theme = self.original_theme
        i18n.set_lang(self.original_lang)
        self.dismiss(None)
