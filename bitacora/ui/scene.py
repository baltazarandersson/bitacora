"""The masthead scene: sky by local time, moon or sun, a lighthouse, its beam and a sea of code.

Everything except the falling code is drawn on a pixel grid at twice the terminal's vertical resolution
(half blocks: two square pixels per cell), so curves and gradients stay smooth. Animation is driven by
time, not by frame count, so it moves at the same speed at any frame rate.

The scene keeps its own palettes, chosen for night, dawn, day and dusk, instead of following the app
theme: a lighthouse only makes sense against a sky.
"""
from __future__ import annotations

import math
import random
from bisect import bisect_left, bisect_right
from datetime import UTC, datetime

from rich.color import Color
from rich.segment import Segment
from rich.style import Style

Rgb = tuple[int, int, int]

RAIN = "01ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎ:.=+*<>"
SYNODIC = 29.530588853
NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=UTC).timestamp()  # approximate reference in UTC


def hexrgb(h: str) -> Rgb:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def lerp(a: Rgb, b: Rgb, k: float) -> Rgb:
    if k <= 0:
        return a
    if k >= 1:
        return b
    return (int(a[0] + (b[0] - a[0]) * k + 0.5), int(a[1] + (b[1] - a[1]) * k + 0.5),
            int(a[2] + (b[2] - a[2]) * k + 0.5))


def luminance(c: Rgb) -> float:
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255


def smooth(e0: float, e1: float, x: float) -> float:
    k = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return k * k * (3 - 2 * k)


# ---------------------------------------------------------------------------------------------- palettes
# Each key holds (night, day) colors; dawn and dusk tint the horizon with WARM.
SKY_TOP = (hexrgb("#060a17"), hexrgb("#3d7fc0"))
SKY_LOW = (hexrgb("#13213b"), hexrgb("#a5cfee"))
WARM_LOW, WARM_TOP = hexrgb("#f0905a"), hexrgb("#3b3a6b")
SEA_TOP = (hexrgb("#0d1b33"), hexrgb("#2a76a6"))
SEA_LOW = (hexrgb("#08111f"), hexrgb("#1c5a83"))
FOAM = (hexrgb("#3f5f8f"), hexrgb("#d3ecfb"))
RAIN_HEAD = (hexrgb("#a8e3c2"), hexrgb("#0f3f2b"))
RAIN_TRAIL = (hexrgb("#4f9f78"), hexrgb("#1f5c42"))
NAME_TOP = (hexrgb("#d3f2e1"), hexrgb("#0f5a43"))
NAME_LOW = (hexrgb("#62a685"), hexrgb("#0a3a2c"))
SHADOW = (hexrgb("#02040a"), hexrgb("#2e6390"))
TAG = (hexrgb("#c3c9e4"), hexrgb("#16324a"))
STAR = hexrgb("#e8ecff")
MOON_LIT, MOON_DARK = hexrgb("#f4efd8"), hexrgb("#1b2440")
SUN, SUN_GLOW = hexrgb("#fff3c4"), hexrgb("#ffd36b")
LIGHT = hexrgb("#ffd98e")
RED, WHITE, ROOF = hexrgb("#c8505f"), hexrgb("#ece7da"), hexrgb("#8e3341")
IRON, ROCK, ROCK_DARK, DOOR = hexrgb("#3d4256"), hexrgb("#5d6378"), hexrgb("#41465a"), hexrgb("#24273a")
GLASS_DAY = hexrgb("#a9bfd4")
BLACK = (0, 0, 0)

FONT = {
    "B": ["     ", "████ ", "█   █", "█   █", "████ ", "█   █", "█   █", "████ "],
    "I": ["   ", "███", " █ ", " █ ", " █ ", " █ ", " █ ", "███"],
    "T": ["     ", "█████", "  █  ", "  █  ", "  █  ", "  █  ", "  █  ", "  █  "],
    "Á": ["   ██", " ███ ", "█   █", "█   █", "█████", "█   █", "█   █", "█   █"],
    "C": ["     ", " ████", "█    ", "█    ", "█    ", "█    ", "█    ", " ████"],
    "O": ["     ", " ███ ", "█   █", "█   █", "█   █", "█   █", "█   █", " ███ "],
    "R": ["     ", "████ ", "█   █", "█   █", "████ ", "█ █  ", "█  █ ", "█   █"],
    "A": ["     ", " ███ ", "█   █", "█   █", "█████", "█   █", "█   █", "█   █"],
}


def title_bitmap(scale_x: int, word: str = "BITÁCORA") -> list[list[bool]]:
    """The name as a pixel bitmap: each font dot is scale_x columns wide and two pixels (one row) tall."""
    rows = [""] * 8
    for i, ch in enumerate(word):
        for r in range(8):
            rows[r] += ("  " if i else "") + FONT[ch][r]
    bitmap = []
    for r in rows:
        line = [c != " " for c in r for _ in range(scale_x)]
        bitmap += [line, line]
    return bitmap


def lighthouse() -> list[tuple[int, int, str, float]]:
    """Pixels of the tower as (x, y, part, shade 0..1 from its left to its right edge)."""
    px: list[tuple[int, int, str, float]] = []

    def span(y: int, x0: int, x1: int, part: str) -> None:
        for x in range(x0, x1 + 1):
            px.append((x, y, part, (x - x0) / max(1, x1 - x0)))

    span(0, 6, 6, "roof")
    span(1, 5, 7, "roof")
    span(2, 4, 8, "roof")
    for y in (3, 4, 5):
        span(y, 4, 4, "iron")
        span(y, 5, 7, "lamp")
        span(y, 8, 8, "iron")
    span(6, 3, 9, "iron")
    span(7, 4, 8, "iron")
    for y in range(8, 24):
        half = 2 + round((y - 8) * 2.4 / 15)
        part = "white" if (y - 8) // 4 % 2 == 0 else "red"
        span(y, 6 - half, 6 + half, part)
    for y in (13, 14, 18, 19):
        px.append((6, y, "door", 0.5))  # slit windows
    for y in (21, 22, 23):
        px.append((6, y, "door", 0.5))
    span(24, 1, 11, "rock")
    span(25, 0, 12, "rock")
    return px


TOWER = lighthouse()
TOWER_W, TOWER_H, LAMP = 13, 26, (6, 4)


def moon_phase(when: datetime) -> float:
    """0 = new, 0.25 = first quarter, 0.5 = full, 0.75 = last quarter."""
    return ((when.timestamp() - NEW_MOON) / 86400 / SYNODIC) % 1.0


def daylight(hour: float) -> tuple[float, float]:
    """(day 0..1, warmth 0..1) for a local hour: dawn around 6:30, dusk around 19:00."""
    day = smooth(5.6, 7.6, hour) * (1 - smooth(18.0, 20.0, hour))
    warm = max(math.exp(-((hour - 6.6) / 0.9) ** 2), math.exp(-((hour - 19.0) / 0.9) ** 2))
    return day, warm


class Scene:
    """Pure computation: a frame is a list of rows of rich Segments, one Strip per row."""

    def __init__(self, w: int, h: int, seed: int = 7):
        self.w, self.h = w, h
        self.rng = random.Random(seed)
        r = self.rng
        self.ph = h * 2  # pixel rows
        self.horizon = self.ph - 4
        self.cols = [[r.uniform(-h, h), r.uniform(1.6, 5.0), r.uniform(4, max(5, h))] for _ in range(w)]
        self.glyphs = [[r.choice(RAIN) for _ in range(w)] for _ in range(h)]
        self.stars = [(r.randrange(w), r.randrange(max(1, self.horizon - 6)), r.uniform(0, 6.28), r.uniform(0.5, 1))
                      for _ in range(max(6, w * h // 55))]
        self.styles: dict = {}
        self.colors: dict = {}
        self.cell_styles: dict = {}
        self.last_t: float | None = None
        self.static_key: tuple | None = None
        self._layout()

    # ------------------------------------------------------------------ layout
    def _layout(self) -> None:
        w = self.w
        big = title_bitmap(2)
        small = title_bitmap(1)
        self.show_tower = w >= len(small[0]) + TOWER_W + 12 and self.ph >= TOWER_H + 6
        self.lx = 2
        self.ly = self.horizon - TOWER_H  # pixel row of the tower's top
        self.lamp = (self.lx + LAMP[0], self.ly + LAMP[1])
        room = w - (self.lx + TOWER_W + 6 if self.show_tower else 2) - 2
        self.title = big if len(big[0]) <= room else small if len(small[0]) <= room else []
        tw = len(self.title[0]) if self.title else 0
        self.tx = (self.lx + TOWER_W + 6) if self.show_tower else max(1, (w - tw) // 2)
        self.ty = max(2, self.lamp[1] + 4)  # pixel row; the beam sweeps down across the name
        if self.ty + 16 + 3 > self.horizon:
            self.ty = max(0, self.horizon - 19)
        self.tag_row = (self.ty + 16) // 2 + 1
        # Moon over the sky to the left of the name, clear of the tower.
        self.moon: tuple[int, int] | None = None
        self.title_px: dict[tuple[int, int], int] = {}  # (x, y) -> row inside the title (for its gradient)
        self.shadow_px: set[tuple[int, int]] = set()
        for y, line in enumerate(self.title):
            for x, on in enumerate(line):
                if on:
                    self.title_px[(self.tx + x, self.ty + y)] = y
        for (x, y) in self.title_px:
            if (x + 1, y + 1) not in self.title_px:
                self.shadow_px.add((x + 1, y + 1))
        self.tower_px = {(self.lx + x, self.ly + y): (part, shade) for x, y, part, shade in TOWER} \
            if self.show_tower else {}
        self.lamp_px = [(self.lx + x, self.ly + y) for x, y, part, _ in TOWER if part == "lamp"] \
            if self.show_tower else []
        # Geometry of every pixel seen from the lamp: only the beam's direction changes per frame.
        lx, ly = self.lamp
        self.angle = [[math.atan2(y - ly, x - lx) if x > lx else None for x in range(w)] for y in range(self.ph)]
        self.angle_cols = [tuple(column) for column in zip(*self.angle, strict=True)]  # per column, rising with y
        # Rain mask: the code falls over the open sea, thinning out towards the tower.
        self.mask = [smooth(self.tx + 4, self.tx + w * 0.45, x) * 0.85 + 0.15 * smooth(self.lx + TOWER_W, w, x)
                     for x in range(w)]

    # ------------------------------------------------------------------ helpers
    def style(self, fg: Rgb, bg: Rgb, bold: bool = False) -> Style:
        key = (fg, bg, bold)
        st = self.styles.get(key)
        if st is None:
            if len(self.styles) > 60000:
                self.styles.clear()
                self.colors.clear()
            colors = self.colors
            fg_color = colors.get(fg)
            if fg_color is None:
                fg_color = colors[fg] = Color.from_rgb(*fg)
            bg_color = colors.get(bg)
            if bg_color is None:
                bg_color = colors[bg] = Color.from_rgb(*bg)
            st = self.styles[key] = Style(color=fg_color, bgcolor=bg_color, bold=bold)
        return st

    def beam(self, t: float) -> tuple[float, float]:
        """Direction (radians, in square pixels) and strength of the beam, smooth in time."""
        phase = t / 9.0 * 2 * math.pi
        return 0.2 + 0.36 * math.sin(phase), 0.82 + 0.18 * math.sin(phase * 2.0)

    # ------------------------------------------------------------------ frame
    def _static(self, now: datetime) -> None:
        """Everything that only changes with the clock: sky, moon or sun, tower and the unlit name."""
        w, ph = self.w, self.ph
        hour = now.hour + now.minute / 60
        day, warm = daylight(hour)
        night = 1 - day
        self.day, self.warm, self.night = day, warm, night
        self.lit_mix = night * (1 - 0.5 * warm)
        rows: list[Rgb] = []
        for y in range(ph):
            if y < self.horizon:
                k = y / max(1, self.horizon - 1)
                top = lerp(lerp(SKY_TOP[0], SKY_TOP[1], day), WARM_TOP, warm * 0.6)
                low = lerp(lerp(SKY_LOW[0], SKY_LOW[1], day), WARM_LOW, warm * 0.85)
                rows.append(lerp(top, low, k ** 1.4))
            else:
                k = (y - self.horizon) / 3
                rows.append(lerp(lerp(SEA_TOP[0], SEA_TOP[1], day), lerp(SEA_LOW[0], SEA_LOW[1], day), k))
        px = [[c] * w for c in rows]
        occ = [bytearray(w) for _ in range(ph)]
        self.sky_rows = rows
        # Moon and sun belong to the sky: light, code and the name all pass in front of them.
        self.bodies: set[tuple[int, int]] = set()
        self.moon = None
        if night > 0.25:
            self._moon(px, now, night, self.bodies)
        if day > 0.05:
            self._sun(px, hour, day, self.bodies)
        for (x, y), (part, shade) in self.tower_px.items():
            if part == "lamp":
                continue  # drawn per frame: it pulses with the beam
            if part == "door":
                c = DOOR
            else:
                base = {"roof": ROOF, "iron": IRON, "rock": ROCK if shade > 0.3 else ROCK_DARK,
                        "red": RED, "white": WHITE}[part]
                c = lerp(lerp(base, BLACK, 0.5), base, 0.3 + 0.7 * shade)
                if shade > 0.7 and part in ("red", "white"):
                    c = lerp(c, (255, 255, 255), 0.08)
                c = lerp(c, rows[0], 0.12 * night)  # sits in the night air
            px[y][x] = c
            occ[y][x] = 1
        for (x, y) in self.tower_px:
            occ[y][x] = 1
        name_top, name_low = lerp(NAME_TOP[0], NAME_TOP[1], day), lerp(NAME_LOW[0], NAME_LOW[1], day)
        shadow = lerp(SHADOW[0], SHADOW[1], day)
        for (x, y) in self.shadow_px:
            if 0 <= y < ph and 0 <= x < w:
                px[y][x] = lerp(px[y][x], shadow, 0.75)
                occ[y][x] = 1
        span = max(1, len(self.title) - 1)
        self.name_color = {}
        for (x, y), ry in self.title_px.items():
            c = lerp(name_top, name_low, ry / span)
            self.name_color[(x, y)] = c
            px[y][x] = c
            occ[y][x] = 1
        self.base, self.occ = px, occ
        self.occ_rows = [bool(any(row)) for row in occ]  # skipped rows avoid two bytearray probes per cell
        self.foam = lerp(FOAM[0], FOAM[1], day)
        self.head_c = lerp(RAIN_HEAD[0], RAIN_HEAD[1], day)
        self.trail_c = lerp(RAIN_TRAIL[0], RAIN_TRAIL[1], day)
        self.static_key = (now.year, now.month, now.day, now.hour, now.minute)

    def frame(self, t: float, now: datetime, tagline: str = "") -> list[list[Segment]]:
        w, h = self.w, self.h
        dt = 0.0 if self.last_t is None else min(0.25, t - self.last_t)
        self.last_t = t
        if self.static_key != (now.year, now.month, now.day, now.hour, now.minute):
            self._static(now)
        night = self.night
        px = [row[:] for row in self.base]
        occ = self.occ

        # stars twinkle
        if night > 0.05:
            for sx, sy, ph0, br in self.stars:
                if not occ[sy][sx] and (sx, sy) not in self.bodies:
                    tw = 0.55 + 0.45 * math.sin(t * 1.7 + ph0)
                    px[sy][sx] = lerp(px[sy][sx], STAR, night * br * tw * 0.9)

        # the beam: a cone of warm haze leaving the lantern; only the cone's pixels are visited
        angle, pulse = self.beam(t)
        strength = pulse * self.lit_mix
        light: list[dict[int, float] | None] = [None] * self.ph  # per pixel row: x -> glow, sparse
        lx, ly = self.lamp
        if self.show_tower and strength > 0.02:
            lo, hi = math.tan(angle - 0.16), math.tan(angle + 0.16)
            angle_cols, name_color = self.angle_cols, self.name_color
            low_angle, high_angle = angle - 0.16, angle + 0.16
            blend = lerp
            for x in range(lx + 1, w):
                dx = x - lx
                y0 = max(0, int(ly + lo * dx) - 1)
                y1 = min(self.horizon - 1, int(ly + hi * dx) + 1)
                reach = max(0.35, 1 - dx / (w * 1.15)) * strength
                column = angle_cols[x]  # the cone is one contiguous run of rows: bisect beats scanning
                start = bisect_left(column, low_angle, y0, y1 + 1)
                end = bisect_right(column, high_angle, start, y1 + 1)
                for y in range(start, end):
                    d = abs(column[y] - angle)
                    li = (max(1 - d / 0.045, 0.0) if d < 0.045 else 0.0)
                    li = max(li, (1 - d / 0.16) * 0.35) * reach
                    row_light = light[y]
                    if row_light is None:
                        row_light = light[y] = {}
                    row_light[x] = li
                    occ_y = occ[y]
                    if not occ_y[x]:
                        px_y = px[y]
                        px_y[x] = blend(px_y[x], LIGHT, li * 0.42)
                    elif (x, y) in name_color:
                        px[y][x] = blend(name_color[(x, y)], (255, 244, 214), min(1.0, li * 1.3))
            for y in range(max(0, ly - 4), min(self.horizon, ly + 5)):  # glow at the lantern
                for x in range(max(0, lx - 7), min(w, lx + 8)):
                    if occ[y][x]:
                        continue
                    g = (1 - math.hypot(x - lx, (y - ly) * 1.2) / 7) * strength * 0.5
                    if g > 0:
                        px[y][x] = lerp(px[y][x], LIGHT, g)
        glass = lerp(GLASS_DAY, LIGHT, self.lit_mix * (0.6 + 0.4 * pulse))
        for x, y in self.lamp_px:
            px[y][x] = glass

        # sea: moving foam and the moon's glitter
        for y in range(self.horizon, self.ph):
            depth = y - self.horizon
            row = px[y]
            thr = 1.35 - depth * 0.1
            for x in range(w):
                wave = math.sin(x * 0.45 - t * 1.4 + depth * 1.7) + math.sin(x * 0.17 + t * 0.6)
                if wave > thr:
                    row[x] = lerp(row[x], self.foam, 0.35 + 0.2 * (wave - thr))
            if night > 0.25 and self.moon is not None:
                gx = self.moon[0]
                for x in range(gx - 2, gx + 3):
                    if 0 <= x < w and math.sin(t * 3 + x * 2.3 + depth) > 0.2:
                        row[x] = lerp(row[x], MOON_LIT, 0.35 * night)

        # the code
        density = 0.25 + 0.75 * night
        r = self.rng
        for col in self.cols:
            col[0] += col[1] * dt
            if col[0] - col[2] > h:
                col[0], col[1], col[2] = -r.uniform(0, h), r.uniform(1.6, 5.0), r.uniform(4, max(5, h))
        for _ in range(max(1, int(w * h * dt * 0.5))):
            self.glyphs[r.randrange(h)][r.randrange(w)] = r.choice(RAIN)

        tag_x0, tag_x1 = self.tx, self.tx + len(tagline)
        head_c, trail_c = self.head_c, self.trail_c
        style = self.style
        styles_get = self.styles.get
        cells = self.cell_styles
        cells_get = cells.get
        if len(cells) > 80000:
            cells.clear()
        cols, mask, glyphs = self.cols, self.mask, self.glyphs
        tag_row, occ_rows = self.tag_row, self.occ_rows
        blend = lerp  # local: the cell loop is the hot path
        lum = luminance
        seg_cls = Segment
        base_kq = [int(0.1 * value * density * 12) for value in mask]  # columns without a trail
        range_w = range(w)
        out: list[list[Segment]] = []
        last_a = last_b = None  # last background pair, for the style reuse below
        last_st = None
        for cy in range(h):
            y0 = cy * 2
            top, low, otop, olow = px[y0], px[y0 + 1], occ[y0], occ[y0 + 1]
            glyph_row = glyphs[cy]
            in_sea = y0 >= self.horizon
            row_empty = not in_sea and not occ_rows[y0] and not occ_rows[y0 + 1]
            tx0, tx1 = (tag_x0, tag_x1) if cy == tag_row else (0, 0)
            lit_row = light[y0]
            segs: list[Segment] = []
            seg_append = segs.append
            run, run_style = "", None
            for x in range_w:
                a, b = top[x], low[x]
                if tx0 <= x < tx1:
                    bg = blend(a, b, 0.5)
                    ink = TAG[1] if lum(bg) > 0.42 else TAG[0]  # readable over any sky
                    ch, st = tagline[x - tag_x0], style(ink, bg)
                elif not row_empty and (in_sea or otop[x] or olow[x]):
                    if a == last_a and b == last_b:
                        st = last_st
                    elif a != b:
                        st = styles_get((a, b, False))
                        if st is None:
                            st = style(a, b)
                        last_a, last_b, last_st = a, b, st
                    else:
                        st = styles_get((a, a, False))
                        if st is None:
                            st = style(a, a)
                        last_a, last_b, last_st = a, a, st
                    ch = "▀" if a != b else " "
                else:
                    col = cols[x]
                    head, length = col[0], col[2]
                    dd = head - cy
                    if 0 <= dd < length:
                        k = 1.0 if dd < 1 else 0.25 + 0.65 * (1 - dd / length)
                        kq = int(k * mask[x] * density * 12)  # quantized: styles come from a cache
                        is_head = dd < 1
                    else:
                        kq = base_kq[x]
                        is_head = False
                    lq = int(lit_row.get(x, 0.0) * 12) if lit_row is not None else 0
                    if kq == 0 and lq == 0:
                        if a == last_a and b == last_b:
                            st = last_st
                        elif a != b:
                            st = styles_get((a, b, False))
                            if st is None:
                                st = style(a, b)
                            last_a, last_b, last_st = a, b, st
                        else:
                            st = styles_get((a, a, False))
                            if st is None:
                                st = style(a, a)
                            last_a, last_b, last_st = a, a, st
                        ch = "▀" if a != b else " "
                    else:
                        key = (a, b, kq, lq, is_head)
                        st = cells_get(key)
                        if st is None:
                            bg = blend(a, b, 0.5)
                            fg = blend(bg, head_c if is_head else trail_c, min(1.0, kq / 12))
                            if lq:
                                fg = blend(fg, LIGHT, min(1.0, lq / 12 * 1.6))
                            st = cells[key] = style(fg, bg, lq > 6)
                        ch = glyph_row[x]
                if st is run_style:
                    run += ch
                else:
                    if run:
                        seg_append(seg_cls(run, run_style))
                    run, run_style = ch, st
            if run:
                seg_append(seg_cls(run, run_style))
            out.append(segs)
        return out

    def arc(self, k: float) -> tuple[float, float]:
        """A point on the sky's arc: k=0 rises on the left, k=1 sets on the right."""
        x0, x1 = self.lx + TOWER_W + 3, self.w - 6
        cx = x0 + k * max(1, x1 - x0)
        cy = (self.horizon - 3) - math.sin(k * math.pi) * (self.horizon - 7)
        return cx, cy

    def moon_pos(self, hour: float) -> tuple[float, float] | None:
        """The moon runs the same arc as the sun, twelve hours apart: one rises as the other sets."""
        k = ((hour - 18.0) % 24) / 13.5
        return self.arc(k) if 0 <= k <= 1 else None

    def sun_pos(self, hour: float) -> tuple[float, float] | None:
        k = (hour - 6.0) / 13.5
        return self.arc(k) if 0 <= k <= 1 else None

    @staticmethod
    def coverage(x: int, y: int, inside) -> float:
        """Share of a pixel inside a shape (4x4 samples), for smooth round edges."""
        hits = 0
        for sy in (0.125, 0.375, 0.625, 0.875):
            for sx in (0.125, 0.375, 0.625, 0.875):
                hits += inside(x + sx - 0.5, y + sy - 0.5)
        return hits / 16

    def _moon(self, px, now: datetime, night: float, bodies: set) -> None:
        pos = self.moon_pos(now.hour + now.minute / 60)
        if pos is None:
            return
        cx, cy = round(pos[0] * 2) / 2, round(pos[1] * 2) / 2  # half-pixel grid keeps the disc symmetric
        self.moon = (int(cx), int(cy))
        rad = 3.3
        phase = moon_phase(now)
        term = math.cos(phase * 2 * math.pi)
        waxing = phase < 0.5

        def disc(x: float, y: float) -> bool:
            return (x - cx) ** 2 + (y - cy) ** 2 <= rad * rad

        def lit(x: float, y: float) -> bool:
            dx, dy = (x - cx) / rad, (y - cy) / rad
            if dx * dx + dy * dy > 1:
                return False
            edge = term * math.sqrt(max(0.0, 1 - dy * dy))
            return dx > edge if waxing else dx < -edge

        for y in range(int(cy - rad) - 2, int(cy + rad) + 3):
            for x in range(int(cx - rad) - 2, int(cx + rad) + 3):
                if not (0 <= y < self.horizon and 0 <= x < self.w):
                    continue
                body = self.coverage(x, y, disc)
                d = math.hypot(x - cx, y - cy) / rad
                if body == 0:
                    halo = max(0.0, 1 - (d - 1) / 0.8) * 0.16 * night
                    if halo > 0:
                        px[y][x] = lerp(px[y][x], MOON_LIT, halo)
                    continue
                shine = self.coverage(x, y, lit)
                dark = lerp(MOON_DARK, px[y][x], 0.35)
                c = lerp(dark, MOON_LIT, shine / body)
                px[y][x] = lerp(px[y][x], c, body * night)
                bodies.add((x, y))

    def _sun(self, px, hour: float, day: float, bodies: set) -> None:
        pos = self.sun_pos(hour)
        if pos is None:
            return
        cx, cy = round(pos[0] * 2) / 2, round(pos[1] * 2) / 2
        rad = 2.6

        def disc(x: float, y: float) -> bool:
            return (x - cx) ** 2 + (y - cy) ** 2 <= rad * rad

        for y in range(int(cy - rad * 3), int(cy + rad * 3) + 1):
            for x in range(int(cx - rad * 3), int(cx + rad * 3) + 1):
                if not (0 <= y < self.horizon and 0 <= x < self.w):
                    continue
                d = math.hypot(x - cx, y - cy) / rad
                if d < 3:
                    px[y][x] = lerp(px[y][x], SUN_GLOW, max(0.0, 1 - (d - 1) / 2) ** 2 * 0.45 * day)
                body = self.coverage(x, y, disc) if d < 1.6 else 0.0
                if body:
                    px[y][x] = lerp(px[y][x], SUN, body * day)
                    bodies.add((x, y))
