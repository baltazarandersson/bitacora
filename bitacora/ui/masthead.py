"""The Bitácora masthead: a lighthouse whose beam sweeps across a field of falling code and lights up the name.

Drawn with Textual's line API from precomputed styles, so a frame costs a few thousand cheap lookups.
Colors come from the active theme.
"""
from __future__ import annotations

import math
import random

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from bitacora import __version__
from bitacora.i18n import t
from bitacora.ui import render as R
from bitacora.ui.render import mix

RAIN = "01ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎ:.=+*<>"
LEVELS = 8  # light levels, 0 = dark

# 7-row pixel font; the first row holds the accent.
FONT = {
    "B": ["     ", "████ ", "█   █", "█   █", "████ ", "█   █", "█   █", "████ "],
    "I": ["   ", "███", " █ ", " █ ", " █ ", " █ ", " █ ", "███"],
    "T": ["     ", "█████", "  █  ", "  █  ", "  █  ", "  █  ", "  █  ", "  █  "],
    "Á": ["   ██", " ███ ", "█   █", "█   █", "█████", "█   █", "█   █", "█   █"],
    "A": ["     ", " ███ ", "█   █", "█   █", "█████", "█   █", "█   █", "█   █"],
    "C": ["     ", " ████", "█    ", "█    ", "█    ", "█    ", "█    ", " ████"],
    "O": ["     ", " ███ ", "█   █", "█   █", "█   █", "█   █", "█   █", " ███ "],
    "R": ["     ", "████ ", "█   █", "█   █", "████ ", "█ █  ", "█  █ ", "█   █"],
}

# Tower rows and their part: cap, iron, white, red, rock. "▒" is the lamp, "▪" the door.
LIGHTHOUSE = [
    ("      ▄▄▄      ", "cap"),
    ("     ▟███▙     ", "cap"),
    ("     █▒▒▒█     ", "iron"),
    ("    ▀█████▀    ", "iron"),
    ("     ▐███▌     ", "white"),
    ("     ▐███▌     ", "white"),
    ("     █████     ", "red"),
    ("     █████     ", "red"),
    ("    ▐█████▌    ", "white"),
    ("    ▐█████▌    ", "white"),
    ("    ███████    ", "red"),
    ("    ███▪███    ", "red"),
    ("  ▄█████████▄  ", "rock"),
]
LAMP_ROW = 2


def title_rows(word: str = "BITÁCORA", gap: int = 2) -> list[str]:
    rows = ["" for _ in range(8)]
    for i, ch in enumerate(word):
        for r in range(8):
            rows[r] += (" " * gap if i else "") + FONT[ch][r]
    return rows


TITLE = title_rows()


class Palette:
    """Every style the masthead needs, built once per theme."""

    def __init__(self) -> None:
        bg, fg, dark = R.BG, R.FG, R.DARK
        green, warm = mix(R.GREEN, fg, 0.25), R.YELLOW
        white = "#ffffff" if dark else "#000000"
        self.light = mix(warm, white, 0.35 if dark else 0.0)
        field = mix(bg, green, 0.09 if dark else 0.06)
        trail = mix(bg, green, 0.42 if dark else 0.3)
        head = mix(green, fg, 0.35)
        # rain[level][light]: level 0 = resting glyph, 1..5 = trail, 6 = head
        bases = [field] + [mix(field, trail, k / 5) for k in range(1, 6)] + [head]
        self.rain = [[Style(color=mix(b, self.light, li / (LEVELS - 1)), bold=li >= LEVELS - 2)
                      for li in range(LEVELS)] for b in bases]
        self.shade = [[Style(color=mix(mix(b, bg, 0.5), self.light, li / (LEVELS - 1))) for li in range(LEVELS)]
                      for b in bases]  # quieter code behind the name
        top, bottom = mix(R.GREEN, fg, 0.55), mix(R.GREEN, bg, 0.3)
        name_light = mix(warm, white, 0.6 if dark else 0.1)
        self.name = [[Style(color=mix(mix(top, bottom, r / 7), name_light, li / (LEVELS - 1) * 0.85))
                      for li in range(LEVELS)] for r in range(8)]
        red = mix(R.RED, bg, 0.12)
        parts = {"cap": mix(red, bg, 0.25), "red": red, "rock": mix(bg, fg, 0.35), "iron": mix(bg, fg, 0.5),
                 "white": mix(fg, white, 0.55) if dark else mix(bg, fg, 0.22)}
        # Light comes from the right: the left side of the tower falls into shade.
        self.tower = {p: [Style(color=mix(mix(c, bg, 0.55), c, k / 4)) for k in range(5)] for p, c in parts.items()}
        self.door = Style(color=mix(bg, fg, 0.12 if dark else 0.7))
        lamp_off = mix(warm, bg, 0.45)
        self.lamp = [Style(color=mix(lamp_off, self.light, li / (LEVELS - 1)), bold=True) for li in range(LEVELS)]
        self.sea = [Style(color=mix(bg, R.CYAN, k)) for k in (0.35, 0.5, 0.65)]
        self.tag = Style(color=R.DIM)


class Masthead(Widget):
    DEFAULT_CSS = "Masthead { height: 16; }"

    def __init__(self, fps: float = 8, **kw):
        super().__init__(**kw)
        self.fps = fps
        self.frame = 0
        self.rng = random.Random(7)
        self.size_key: tuple[int, int] | None = None
        self.palette_key: tuple | None = None
        self.pal: Palette | None = None
        self.strips: list[Strip] = []

    def on_mount(self) -> None:
        self.set_interval(1 / self.fps, self.tick)

    def _layout(self, w: int, h: int) -> None:
        """Geometry that only changes with the widget size."""
        r = self.rng
        self.cols = [[r.uniform(-h, h), r.uniform(0.2, 0.7), r.randint(4, max(5, h))] for _ in range(w)]
        self.glyphs = [[r.choice(RAIN) for _ in range(w)] for _ in range(h)]
        tw, lw = len(TITLE[0]), len(LIGHTHOUSE[0][0])
        self.show_lh = w >= tw + lw + 10 and h >= len(LIGHTHOUSE) + 2
        self.big = w >= tw + 4 and h >= 11
        lx, base = 2, h - 2
        top = base - len(LIGHTHOUSE) + 1
        self.lamp = (lx + lw // 2, top + LAMP_ROW)
        self.tx = (lx + lw + 6) if self.show_lh else max(0, (w - tw) // 2)
        self.ty = max(0, self.lamp[1] - 1) if self.show_lh else max(0, (h - 10) // 2)
        self.name_box = (self.tx - 1, self.tx + tw, self.ty, self.ty + 8)
        self.name_cells = {(self.tx + j, self.ty + i) for i, row in enumerate(TITLE)
                     for j, ch in enumerate(row) if ch != " "} if self.big else set()
        self.tower: dict[tuple[int, int], tuple[str, str, int]] = {}
        if self.show_lh:
            for i, (row, part) in enumerate(LIGHTHOUSE):
                filled = [j for j, ch in enumerate(row) if ch != " "]
                first, last = filled[0], filled[-1]
                for j in filled:
                    ch = row[j]
                    step = round(4 * (j - first) / max(1, last - first))
                    kind = "lamp" if ch == "▒" else "door" if ch == "▪" else part
                    glyph = "█" if ch == "▒" else "▄" if ch == "▪" else ch
                    self.tower[(lx + j, top + i)] = (kind, glyph, step)
        # Angle and reach of every cell seen from the lamp; per frame only the beam direction changes.
        lx0, ly0 = self.lamp
        self.geo = [[(math.atan2((y - ly0) * 2.1, x - lx0), max(0.7, 1 - (x - lx0) / (w * 1.6)))
                     if x > lx0 and self.show_lh else (None, 0.0) for x in range(w)] for y in range(h)]

    def tick(self) -> None:
        self.frame += 1
        w, h = self.size.width, self.size.height
        if w <= 0 or h <= 0:
            return
        if self.size_key != (w, h):
            self.size_key = (w, h)
            self._layout(w, h)
        theme_key = (R.BG, R.FG, R.GREEN, R.YELLOW, R.RED, R.CYAN)
        if self.palette_key != theme_key:
            self.palette_key, self.pal = theme_key, Palette()
        self._step(w, h)
        self.strips = self._draw(w, h)
        self.refresh()

    def _step(self, w: int, h: int) -> None:
        r = self.rng
        for col in self.cols:
            col[0] += col[1]
            if col[0] - col[2] > h:
                col[0], col[1], col[2] = -r.randint(0, h), r.uniform(0.2, 0.7), r.randint(4, max(5, h))
        for _ in range(max(1, w * h // 40)):
            self.glyphs[r.randrange(h)][r.randrange(w)] = r.choice(RAIN)

    def beam(self) -> tuple[float, float]:
        """Direction and strength: the beam scans down across the name and back up."""
        phase = (self.frame % 96) / 96 * 2 * math.pi
        return 0.13 + 0.2 * math.sin(phase), 0.85 + 0.15 * math.sin(phase * 2)

    def _draw(self, w: int, h: int) -> list[Strip]:
        pal = self.pal
        angle, strength = self.beam()
        top_level = LEVELS - 1
        lamp_level = round(top_level * (0.35 + 0.65 * strength))
        lx0, ly0 = self.lamp
        x0, x1, y0, y1 = self.name_box
        tag = f"{t('tagline')}  ·  v{__version__}"
        tag_y = self.ty + 9 if self.big else self.ty + 3
        strips = []
        for y in range(h):
            segs: list[Segment] = []
            run_text, run_style = "", None
            geo_row, glyph_row = self.geo[y], self.glyphs[y]
            near_lamp = abs(y - ly0) <= 1
            in_band = self.big and y0 <= y < y1
            for x in range(w):
                ch = glyph_row[x]
                a, reach = geo_row[x]
                li = 0
                if a is not None:
                    d = abs(a - angle)
                    lit = 0.0
                    if d < 0.2:
                        core = (1 - d / 0.075) ** 0.8 if d < 0.075 else 0.0
                        lit = max(core, (1 - d / 0.2) * 0.3) * reach * strength
                    if near_lamp and x - lx0 < 5:
                        lit = max(lit, 0.7 * strength)  # the glow right at the lantern
                    li = min(top_level, round(lit * top_level))
                cell = self.tower.get((x, y)) if self.show_lh else None
                if cell is not None:
                    kind, ch, step = cell
                    style = pal.lamp[lamp_level] if kind == "lamp" else pal.door if kind == "door" \
                        else pal.tower[kind][step]
                elif in_band and (x, y) in self.name_cells:
                    ch, style = "█", pal.name[y - self.ty][li]
                elif y == tag_y and self.tx - 1 <= x < self.tx + len(tag) + 1:
                    i = x - self.tx
                    ch, style = (tag[i] if 0 <= i < len(tag) else " "), pal.tag
                elif y == h - 1 and self.show_lh:
                    k = (x + self.frame // 3) % 7
                    ch = "~" if k in (0, 3) else "-" if k == 5 else " "
                    style = pal.sea[(x + self.frame // 5) % 3]
                else:
                    head, _, length = self.cols[x]
                    dd = head - y
                    level = 6 if 0 <= dd < 1 else (1 + int((1 - dd / length) * 4.99)) if 0 < dd < length else 0
                    table = pal.shade if (in_band and x0 <= x <= x1) else pal.rain
                    style = table[level][li]
                if style is run_style:
                    run_text += ch
                else:
                    if run_text:
                        segs.append(Segment(run_text, run_style))
                    run_text, run_style = ch, style
            if run_text:
                segs.append(Segment(run_text, run_style))
            strips.append(Strip(segs, w))
        return strips

    def render_line(self, y: int) -> Strip:
        if y < len(self.strips):
            return self.strips[y]
        return Strip.blank(self.size.width)


class Beacon(Widget):
    """The one-line brand strip on the main screen.

    The beam sweeps while agents work (or while sessions load), at a smooth frame rate, and costs almost
    nothing because only this line is redrawn. When the header is folded it also carries the summary.
    """

    DEFAULT_CSS = "Beacon { height: 1; padding: 0 1; }"
    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    class Clicked(Message):
        pass

    def __init__(self, fps: float = 15, **kw):
        super().__init__(**kw)
        self.fps = fps
        self.frame = 0
        self.working = False
        self.summary: list[tuple[str, Style]] = []
        self._styles_key: tuple | None = None

    def on_mount(self) -> None:
        self.set_interval(1 / self.fps, self.tick)

    def tick(self) -> None:
        if self.working:
            self.frame += 1
            self.refresh()

    def set_state(self, working: bool, summary: list[tuple[str, str]] | None = None) -> None:
        summary_styled = [(text, Style.parse(style) if style else Style()) for text, style in (summary or [])]
        if working != self.working or summary_styled != self.summary:
            self.working, self.summary = working, summary_styled
            self.refresh()

    def on_click(self) -> None:
        self.post_message(self.Clicked())

    def _styles(self) -> None:
        key = (R.BG, R.FG, R.YELLOW, R.GREEN)
        if key == self._styles_key:
            return
        self._styles_key = key
        warm, line = R.YELLOW, mix(R.BG, R.FG, 0.18)
        self.beam_styles = [Style(color=mix(line, warm, k / 4)) for k in range(5)]
        self.lamp_on = Style(color=warm, bold=True)
        self.lamp_off = Style(color=mix(warm, R.BG, 0.45), bold=True)
        self.brand = Style(color=mix(R.GREEN, R.FG, 0.4), bold=True)
        self.spin = Style(color=R.GREEN)

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        if y or w <= 0:
            return Strip.blank(w)
        self._styles()
        segs = [Segment("▲", self.lamp_on if self.working and self.frame % 12 < 9 else self.lamp_off),
                Segment(" bitácora ", self.brand)]
        used = 11
        if self.summary:
            if self.working:
                segs.append(Segment(self.SPINNER[self.frame % len(self.SPINNER)] + " ", self.spin))
                used += 2
            for text, style in self.summary:
                segs.append(Segment(text, style))
                used += cell_len(text)
            segs.append(Segment(" ", None))
            used += 1
        span = max(0, w - used)
        pos = (self.frame * 1.6) % (span + 30) - 15 if self.working else -100.0
        run, cur = "", None
        for x in range(span):
            k = max(0.0, 1 - abs(x - pos) / 10)
            ch, st = ("━" if k > 0.3 else "─"), self.beam_styles[round(k * 4)]
            if st is not cur and run:
                segs.append(Segment(run, cur))
                run = ""
            cur = st
            run += ch
        if run:
            segs.append(Segment(run, cur))
        return Strip(segs).crop(0, w)
