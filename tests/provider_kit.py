"""Conformance checks every agent provider must pass; new providers run the same kit."""
from __future__ import annotations

import time
from pathlib import Path

from bitacora.model import Run, Session
from bitacora.providers.base import Capabilities, Provider, render_resume


def assert_provider_contract(provider: Provider, tmp_path: Path) -> None:
    assert provider.name and provider.label
    assert isinstance(provider.capabilities, Capabilities)
    assert issubclass(provider.session_cls, Session)
    assert all(isinstance(name, str) and name for name in provider.process_names)

    since = time.time() - 3600
    files = list(provider.history(since))
    assert all(isinstance(path, Path) and path.exists() for path in files)
    assert len(files) == len(set(files))
    assert set(files) == set(provider.history(since))  # discovery is idempotent

    for path in files[:2]:
        first = provider.session_cls(path)
        second = provider.session_cls(path)
        first.update()
        second.update()
        assert first.events_total == second.events_total
        assert first.messages == second.messages
        assert first.tokens == second.tokens

    if not files:
        return
    sample = provider.session_cls(files[0])
    sample.update()
    spec = provider.resume(Run("kit", provider.name, sample, None, "kit"))
    if spec is not None:
        assert isinstance(spec.get("argv"), list) and spec["argv"]
        assert all(isinstance(arg, str) and arg for arg in spec["argv"])
        assert spec.get("cwd") is None or isinstance(spec.get("cwd"), str)
        assert render_resume(spec, "sh")

    fuzz = tmp_path / f"fuzz-{provider.name}-{files[0].name}"
    fuzz.write_bytes(files[0].read_bytes() + b'{"broken":\nnot json\n')
    parsed = provider.session_cls(fuzz)
    parsed.update()
    assert parsed.discarded >= 1  # corrupt lines stay inside the parser
