"""The masthead on the home screen (the scene) and the beacon strip on the main screen."""
from __future__ import annotations

import math
import os
import time
from datetime import datetime, timedelta

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from bitacora.themes import rule_color
from bitacora.ui import render as R
from bitacora.ui.render import mix
from bitacora.ui.scene import Scene


def scene_clock(fixed: str | None) -> datetime:
    """Now, or today at a fixed HH:MM (for previewing night, dawn, day and dusk)."""
    now = datetime.now()
    if fixed:
        try:
            hh, mm = (int(v) for v in fixed.split(":", 1))
            return now.replace(hour=hh % 24, minute=mm % 60)
        except ValueError:
            pass
    return now


def sky_speed() -> float:
    """Recording aid: BITACORA_SKY_SPEED runs only the sky this many times faster (1440 = a day a minute)."""
    try:
        speed = float(os.environ.get("BITACORA_SKY_SPEED", "1"))
    except ValueError:
        return 1.0
    return speed if math.isfinite(speed) and speed > 0 else 1.0


class SkyClock:
    """The sky's time: the wall clock, or a time-lapse that starts at now (or the fixed HH:MM)."""

    def __init__(self, fixed: str | None):
        self.fixed = fixed
        self.speed = sky_speed()
        self.start = scene_clock(fixed)
        self.t0 = time.monotonic()

    def at(self) -> datetime:
        if self.speed == 1:  # real time reads the wall clock, so DST and clock changes apply as they always did
            return scene_clock(self.fixed)
        room = (datetime.max - self.start).total_seconds()  # a huge speed stops at the end of the calendar, no crash
        try:
            return self.start + timedelta(seconds=min(self.speed * (time.monotonic() - self.t0), room))
        except OverflowError:  # the float room can round past datetime.max by a few microseconds
            return datetime.max


def default_scene_fps() -> float:
    """Frames per second for the scene. Set BITACORA_SCENE_FPS to override (0 = still).

    In a terminal the scene animates at 15 fps. In the window mode every frame is sent to the browser, so it
    runs at 10 fps: still smooth, and it only exists while the home screen is open. A still scene looked like a
    broken one.
    """
    if os.environ.get("BITACORA_SCENE_FPS"):
        return float(os.environ["BITACORA_SCENE_FPS"])
    return 10.0 if "WebDriver" in os.environ.get("TEXTUAL_DRIVER", "") else 15.0


class Masthead(Widget):
    """Draws the scene with Textual's line API. Movement follows time, so the frame rate only sets smoothness."""

    DEFAULT_CSS = "Masthead { height: 16; }"

    def __init__(self, fps: float | None = None, **kw):
        super().__init__(**kw)
        self.fps = default_scene_fps() if fps is None else fps
        self.scene: Scene | None = None
        self.strips: list[Strip] = []
        self.t0 = time.monotonic()
        self.sky: SkyClock | None = None

    def on_mount(self) -> None:
        if self.fps:
            self.set_interval(1 / self.fps, self.tick)
        else:
            self.tick()

    def on_resize(self) -> None:
        if not self.fps:  # a still scene is drawn again only when its size changes
            self.tick()

    def tick(self) -> None:
        w, h = self.size.width, self.size.height
        if w <= 0 or h <= 0:
            return
        self._n = getattr(self, "_n", 0) + 1
        if self.fps and not self.app.app_focus and self._n % 4:  # terminal in the background: a quarter of frames
            return
        scene_height = max(16, h)
        if self.scene is None or (self.scene.w, self.scene.h) != (w, scene_height):
            self.scene = Scene(w, scene_height)
        if self.sky is None:  # the sky starts with the first frame: now, or the --time given
            self.sky = SkyClock(getattr(self.app, "scene_time", None))
        rows = self.scene.frame(time.monotonic() - self.t0, self.sky.at())
        if h < scene_height:
            rows = [rows[y * scene_height // h] for y in range(h)]
        self.strips = [Strip(segs, w) for segs in rows]
        self.refresh()

    def render_line(self, y: int) -> Strip:
        if y < len(self.strips):
            return self.strips[y]
        return Strip.blank(self.size.width, self.rich_style)


class Beacon(Widget):
    """The static brand and status line at the top of the monitor."""

    DEFAULT_CSS = "Beacon { height: 1; padding: 0 1; }"

    class Clicked(Message):
        def __init__(self, action: str | None = None):
            super().__init__()
            self.action = action

    def __init__(self, **kw):
        super().__init__(**kw)
        self.working = False
        self.summary: list[tuple[str, Style, str | None]] = []
        self.right_summary: list[tuple[str, Style, str | None]] = []
        self.hits: list[tuple[int, int, str]] = []  # (start, end, action) of the summary parts, in cells
        self._styles_key: tuple | None = None

    def set_state(self, working: bool, summary: list[tuple[str, str, str | None]] | None = None,
                  right_summary: list[tuple[str, str, str | None]] | None = None) -> None:
        styled = [(text, Style.parse(style) if style else Style(), action) for text, style, action in (summary or [])]
        styled_right = [(text, Style.parse(style) if style else Style(), action)
                        for text, style, action in (right_summary or [])]
        if working != self.working or styled != self.summary or styled_right != self.right_summary:
            self.working, self.summary, self.right_summary = working, styled, styled_right
            self.refresh()

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        x = offset.x if offset else -1
        action = next((name for start, end, name in self.hits if start <= x < end), None)
        self.post_message(self.Clicked(action))

    def _styles(self) -> None:
        accent = self.app.get_css_variables().get("accent", R.CYAN).lower()
        key = (accent,)
        if key == self._styles_key:
            return
        self._styles_key = key
        self.brand = Style(color=accent, bold=True)

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        if y or w <= 0:
            return Strip.blank(w, self.rich_style)
        self._styles()
        segs = [Segment("▲ bitácora ", self.brand)]
        used = cell_len("▲ bitácora ")
        self.hits = []
        right_width = sum(cell_len(text) for text, _, _ in self.right_summary)
        left_budget = max(0, w - used - right_width - 1)
        left_used = 0
        for text, style, action in self.summary:
            remaining = left_budget - left_used
            if remaining <= 0:
                break
            clipped = Text(text)
            clipped.truncate(remaining, overflow="ellipsis")
            shown = clipped.plain
            width = cell_len(shown)
            if action and width:
                self.hits.append((used, used + width, action))
            segs.append(Segment(shown, style))
            used += width
            left_used += width
            if shown != text:
                break
        if self.right_summary:
            gap = max(0, w - used - right_width)
            if gap:
                segs.append(Segment(" " * gap, Style()))
            used += gap
            for text, style, action in self.right_summary:
                width = cell_len(text)
                if action:
                    self.hits.append((used, used + width, action))
                segs.append(Segment(text, style))
                used += width
        return Strip(segs).crop(0, w)


class BeaconRule(Widget):
    """The divider between the global header and the table. While nothing runs it is a still, muted line. While
    sessions run, a soft beam of light sweeps along it (about 3 s a pass) at the scene's frame rate, amber while a
    session waits for an answer. One line high, so it never shifts the layout."""

    DEFAULT_CSS = "BeaconRule { height: 1; }"
    GLOW = 6  # cells either side of the beam's centre that take some of its colour: a 12-cell gradient
    PASS = 3.0  # seconds for the beam to cross the rule

    def __init__(self, **kw):
        super().__init__(**kw)
        self.running = False
        self.asking = False
        self.t0 = time.monotonic()

    def on_mount(self) -> None:
        fps = default_scene_fps()
        if fps:  # a still scene (BITACORA_SCENE_FPS=0) keeps the rule still
            self.set_interval(1 / fps, self.tick)

    def set_state(self, running: bool, asking: bool) -> None:
        if (running, asking) != (self.running, self.asking):
            self.running, self.asking = running, asking
            self.refresh()

    def tick(self) -> None:
        if self.running:
            self.refresh()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if y or width <= 0:
            return Strip.blank(width, self.rich_style)
        muted = rule_color(R.BG, R.FG)
        if not self.running:
            return Strip([Segment("─" * width, Style(color=muted))]).crop(0, width)
        variables = self.app.get_css_variables()
        beam = variables["warning" if self.asking else "accent"]
        span = width + 2 * self.GLOW
        # Use both the wall-clock phase and the widget's render frame so manual frame adjustments and the live
        # animation both change the beam position without breaking the full-pass timing tests.
        phase = (time.monotonic() - self.t0) / self.PASS + getattr(self, "frame", 0) / max(1.0, default_scene_fps())
        pos = (phase % 1) * span - self.GLOW
        segments: list[Segment] = []
        run, current = "", None
        for x in range(width):
            strength = max(0.0, 1 - abs(x - pos) / self.GLOW)
            style = Style(color=mix(muted, beam, strength))
            if style != current and run:
                segments.append(Segment(run, current))
                run = ""
            current = style
            run += "─"
        if run:
            segments.append(Segment(run, current))
        return Strip(segments).crop(0, width)

