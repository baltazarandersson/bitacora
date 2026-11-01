"""Shared options and isolated synthetic sessions for both presentations."""
from __future__ import annotations

import os
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from bitacora import i18n
from bitacora.providers import Provider, all_providers
from bitacora.store import Config, Memory, State, capped_hours, load_config, state_path


@dataclass
class Runtime:
    config: Config
    state: State
    providers: list[Provider]
    lang: str
    demo_root: Path | None


@contextmanager
def prepare(args):
    animator = None
    previous = {}
    providers = None
    with ExitStack() as stack:
        root = None
        if args.demo:
            from bitacora import demo

            # Read the user's switches before BITACORA_HOME moves to the demo home.
            switches = load_config().providers
            root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="bitacora-demo-")))
            data = demo.generate(root)
            providers = all_providers(root / "home", enabled=switches)
            off = sorted(name for name, on in switches.items() if on is False)
            if off:
                # The reader process loads config.toml from the demo home, so the same switches are written there.
                (root / "home").mkdir(parents=True, exist_ok=True)
                (root / "home" / "config.toml").write_text(
                    "[providers]\n" + "".join(f"{name} = false\n" for name in off), encoding="utf-8")
            env = {p.home_env: str(data["homes"][p.name]) for p in providers
                   if p.home_env and p.name in data["homes"]}
            env["BITACORA_HOME"] = str(root / "home")
            previous = {key: os.environ.get(key) for key in env}
            os.environ.update(env)
            memory = Memory()
            for key, entry in data["state"].items():
                memory.remember(key, entry)
            memory.save()
            animator = demo.Animator(data["live"], waiting=data.get("waiting"))
            animator.start()
        try:
            config, state = load_config(), State()
            lang = args.lang or state.get("lang") or config.lang or "auto"
            if lang not in (*i18n.LANGS, "auto"):
                lang = "auto"
            i18n.set_lang(None if lang == "auto" else lang)
            hours = args.hours if args.hours is not None else state.get("hours", config.hours)
            config.hours = capped_hours(hours) or 24
            config.notify = bool(state.get("notify", config.notify))
            yield Runtime(config, state, providers if providers is not None
                          else all_providers(state_path().parent, enabled=config.providers), lang, root)
        finally:
            if animator:
                animator.stop.set()
                animator.join(timeout=2)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
