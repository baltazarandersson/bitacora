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

ROOT = Path(__file__).resolve().parent.parent
FONT_FACE = re.compile(r"@font-face\s*\{[^{}]*\}", re.IGNORECASE | re.DOTALL)
GENERATED_COMMENT = re.compile(r"<!--\s*Generated with Rich\b.*?-->", re.IGNORECASE | re.DOTALL)
REMOTE_REFERENCE = re.compile(
    r"url\s*\([^)]*https?://|(?:xlink:)?href\s*=\s*[^>]*https?://|\bsrc\s*=\s*[^>]*https?://|@import[^;]*https?://",
    re.IGNORECASE,
)
SCRIPT_TAG = re.compile(r"<script\b", re.IGNORECASE)


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
        environment = {
            "CODEX_HOME": str(data["codex_home"]),
            "CLAUDE_CONFIG_DIR": str(data["claude_home"]),
            "BITACORA_HOME": str(tmp / "home"),
        }
        with patch.dict(os.environ, environment):
            os.environ.pop("NO_COLOR", None)  # documentation captures show the default color presentation
            await render(lang, data)


async def render(lang: str, data: dict) -> None:
    i18n.set_lang(lang)
    from bitacora.providers import all_providers
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
