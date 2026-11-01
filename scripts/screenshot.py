"""Render the README screenshots from synthetic demo data: python scripts/screenshot.py en|es"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from bitacora import demo, i18n
from bitacora.providers import all_providers

ROOT = Path(__file__).resolve().parent.parent
FONT_FACE = re.compile(r"@font-face\s*\{[^{}]*\}", re.IGNORECASE | re.DOTALL)
GENERATED_COMMENT = re.compile(r"<!--\s*Generated with Rich\b.*?-->", re.IGNORECASE | re.DOTALL)
REMOTE_REFERENCE = re.compile(
    r"url\s*\([^)]*https?://|(?:xlink:)?href\s*=\s*[^>]*https?://|\bsrc\s*=\s*[^>]*https?://|@import[^;]*https?://",
    re.IGNORECASE,
)
SCRIPT_TAG = re.compile(r"<script\b", re.IGNORECASE)
# Rich writes each glyph run as text. Half and full blocks in a fallback font do not fill their cell, which leaves
# dark seams in the pixel art, so they are drawn as rectangles. The baseline sits 18.5px below a row's top.
BLOCK_RUN = re.compile(r'<text class="([\w-]+)" x="([\d.]+)" y="([\d.]+)" textLength="([\d.]+)"[^>]*>([^<]*)</text>')
ROW_FILL = re.compile(r"\.([\w-]+)\s*\{\s*fill:\s*(#[0-9a-fA-F]{6})\s*\}")
LINE_HEIGHT = re.compile(r"line-height:\s*([\d.]+)px")
BASELINE_FROM_TOP = 18.5
BLOCKS = frozenset("█▀▄")


def _num(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def pixel_rects(svg: str) -> str:
    """Replace block-glyph runs with exact rectangles, one per cell, in the glyph's colour."""
    fills = dict(ROW_FILL.findall(svg))
    line = float(LINE_HEIGHT.search(svg).group(1)) if LINE_HEIGHT.search(svg) else 24.4

    def run(match: re.Match) -> str:
        css_class, x, y, width, text = match.groups()
        chars = set(text)
        if not chars & BLOCKS or not chars <= BLOCKS | {" "} or css_class not in fills:
            return match.group(0)
        cell = float(width) / len(text)
        top = float(y) - BASELINE_FROM_TOP
        rects = []
        for index, char in enumerate(text):
            if char == " ":
                continue
            left = float(x) + index * cell
            if char == "█":
                box = (top, line)
            elif char == "▀":
                box = (top, line / 2)
            else:
                box = (top + line / 2, line / 2)
            rects.append(f'<rect fill="{fills[css_class]}" x="{_num(left)}" y="{_num(box[0])}" '
                         f'width="{_num(cell)}" height="{_num(box[1])}" shape-rendering="crispEdges"/>')
        return "".join(rects)

    return BLOCK_RUN.sub(run, svg)


def local_font_face(match: re.Match) -> str:
    block = match.group(0)
    local = re.search(r"\bsrc\s*:\s*(local\([^)]*\))", block, re.IGNORECASE)
    if local is None:
        return ""
    local_src = local.group(1)
    return re.sub(r"\bsrc\s*:[^;]+;", f"src: {local_src};", block, count=1, flags=re.IGNORECASE | re.DOTALL)


def sanitize_svg(svg: str) -> bytes:
    svg = FONT_FACE.sub(local_font_face, svg)
    svg = GENERATED_COMMENT.sub("", svg)
    svg = svg.replace("\r\n", "\n").replace("\r", "\n")
    svg = pixel_rects(svg)
    if REMOTE_REFERENCE.search(svg) or SCRIPT_TAG.search(svg):
        raise ValueError("screenshot SVG contains a remote reference or script")
    ET.fromstring(svg)
    return svg.encode("utf-8")


def save_svg(app, path: Path) -> None:
    path.write_bytes(sanitize_svg(app.export_screenshot()))


async def shoot(lang: str) -> None:
    with tempfile.TemporaryDirectory(prefix="bitacora-shot-") as temp:
        tmp = Path(temp)
        data = demo.generate(tmp, time.time())
        environment = {p.home_env: str(data["homes"][p.name]) for p in all_providers()
                       if p.home_env and p.name in data["homes"]}
        environment["BITACORA_HOME"] = str(tmp / "home")
        with patch.dict(os.environ, environment):
            os.environ.pop("NO_COLOR", None)  # documentation captures show the default color presentation
            await render(lang, data)


async def render(lang: str, data: dict) -> None:
    i18n.set_lang(lang)
    from bitacora.store import Config, Memory, State
    from bitacora.ui.app import create_app

    seed = Memory()  # only generated sessions are visible to the reader
    for key, entry in data["state"].items():
        seed.remember(key, entry)
    seed.save()
    state = State()
    app = create_app(all_providers(), Config(), state, view="everything", scan_procs=False, refresh_secs=60)
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    async with app.run_test(size=(172, 42)) as pilot:
        while app.all_runs is None:
            await pilot.pause(0.05)
        table = app.q("#runs")
        table.move_cursor(row=[r.label for r in app.runs].index("auth-cookies"))
        await pilot.pause(0.5)
        save_svg(app, out / f"screenshot-{lang}.svg")
        await pilot.press("s")
        await pilot.pause(2.0)  # let the rain fall and the beam reach the name
        save_svg(app, out / f"home-{lang}.svg")


if __name__ == "__main__":
    asyncio.run(shoot(sys.argv[1] if len(sys.argv) > 1 else "en"))
