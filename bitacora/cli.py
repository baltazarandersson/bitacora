"""Command line entry point."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from bitacora import __version__, i18n
from bitacora.store import State, config_path, load_config, state_path
from bitacora.views import BUILTIN


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bitacora",
        description="Live dashboard for coding agents (Codex, Claude Code) working in the background.")
    ap.add_argument("--view", help="start in this view: " + ", ".join(v.id for v in BUILTIN)
                    + " or one from config.toml (skips the first-run picker)")
    ap.add_argument("--live", action="store_true", help="show live sessions only")
    ap.add_argument("--hours", type=float, help="history window in hours (default 24)")
    ap.add_argument("--lang", choices=i18n.LANGS, help="interface language (default: system)")
    ap.add_argument("--demo", action="store_true", help="run on generated sample sessions")
    ap.add_argument("--paths", action="store_true", help="print where config and state live, and exit")
    ap.add_argument("--version", action="version", version=f"bitacora {__version__}")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.paths:
        print(json.dumps({"config": str(config_path()), "state": str(state_path())}, indent=1))
        return

    animator = None
    if args.demo:
        from bitacora import demo
        root = Path(tempfile.mkdtemp(prefix="bitacora-demo-"))
        data = demo.generate(root)
        os.environ["CODEX_HOME"] = str(data["codex_home"])
        os.environ["CLAUDE_CONFIG_DIR"] = str(data["claude_home"])
        os.environ["BITACORA_HOME"] = str(root / "home")
        seed = State()
        for key, entry in data["state"].items():
            seed.remember(key, entry)
        seed.save()
        animator = demo.Animator(data["live"])

    from bitacora.providers import all_providers
    from bitacora.ui.app import create_app

    if animator:
        animator.start()
    view, collector = args.view, None
    try:
        while True:
            # Precedence: command line, then what you saved in setup, then config.toml.
            config, state = load_config(), State()
            lang = args.lang or state.get("lang") or config.lang or "auto"
            config.hours = args.hours or state.get("hours") or config.hours
            config.notify = state.get("notify", config.notify)
            i18n.set_lang(None if lang == "auto" else lang)
            app = create_app(all_providers(), config, state, view=view, live_only=args.live,
                             scan_procs=not args.demo, lang=args.lang or state.get("lang") or "auto",
                             collector=collector)
            if app.run() != "relaunch":
                break
            collector = app.collector  # keep what was already read; the new app only relabels
            view = None  # a relaunch after setup starts in the view just saved
    finally:
        if animator:
            animator.stop.set()


if __name__ == "__main__":
    sys.exit(main())
