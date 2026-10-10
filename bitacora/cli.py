"""Command line entry point for the terminal app and optional desktop window."""
from __future__ import annotations

import argparse
import json
import sys

from bitacora import __version__, i18n
from bitacora.store import config_path, state_path
from bitacora.views import BUILTIN


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="bitacora", description="Live dashboard for coding agents (Codex, Claude Code).")
    ap.add_argument("--view", help="start in this view: " + ", ".join(v.id for v in BUILTIN)
                    + " or one from config.toml (skips the first-run picker)")
    ap.add_argument("--live", action="store_true", help="show live sessions only")
    ap.add_argument("--hours", type=float, help="history window in hours (default 24)")
    ap.add_argument("--lang", choices=i18n.LANGS, help="interface language (default: system)")
    ap.add_argument("--demo", action="store_true", help="run on generated sample sessions")
    ap.add_argument("--paths", action="store_true", help="print where config and state live, and exit")
    ap.add_argument("--window", action="store_true", help="open the desktop app (needs bitacora[window])")
    ap.add_argument("--time", metavar="HH:MM", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=f"bitacora {__version__}")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.paths:
        print(json.dumps({"config": str(config_path()), "state": str(state_path()),
                          "sessions": str(state_path().with_name("sessions.json"))}, indent=1))
        return
    if args.window:
        from bitacora.window import open_window
        raise SystemExit(open_window(args))
    from bitacora.runtime import prepare
    from bitacora.ui.app import create_app

    with prepare(args) as runtime:
        app = create_app(runtime.providers, runtime.config, runtime.state, view=args.view, live_only=args.live,
                         scan_procs=not args.demo, lang=runtime.lang, reader="process")
        app.scene_time = args.time
        try:
            app.run()
        finally:
            app.source.stop()


if __name__ == "__main__":
    main()
