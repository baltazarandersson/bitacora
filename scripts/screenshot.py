"""Render the README screenshots from demo data: python scripts/screenshot.py en|es"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

from bitacora import demo, i18n

ROOT = Path(__file__).resolve().parent.parent


async def shoot(lang: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="bitacora-shot-"))
    data = demo.generate(tmp, time.time())
    os.environ["CODEX_HOME"] = str(data["codex_home"])
    os.environ["CLAUDE_CONFIG_DIR"] = str(data["claude_home"])
    os.environ["BITACORA_HOME"] = str(tmp / "home")
    i18n.set_lang(lang)
    from bitacora.providers import all_providers
    from bitacora.store import Config, State
    from bitacora.ui.app import create_app

    state = State()
    for key, entry in data["state"].items():
        state.remember(key, entry)
    app = create_app(all_providers(), Config(), state, view="everything", scan_procs=False, refresh_secs=60)
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    async with app.run_test(size=(172, 42)) as pilot:
        while app.all_runs is None:
            await pilot.pause(0.05)
        table = app.q("#runs")
        table.move_cursor(row=[r.label for r in app.runs].index("auth-cookies"))
        await pilot.pause(0.5)
        app.save_screenshot(str(out / f"screenshot-{lang}.svg"))
        await pilot.press("s")
        await pilot.pause(2.0)  # let the rain fall and the beam reach the name
        app.save_screenshot(str(out / f"home-{lang}.svg"))


if __name__ == "__main__":
    asyncio.run(shoot(sys.argv[1] if len(sys.argv) > 1 else "en"))
