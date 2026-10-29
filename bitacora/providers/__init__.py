"""Agent providers. Each one knows where its agent keeps sessions and how its processes look."""
from __future__ import annotations

from pathlib import Path

from bitacora.providers.base import Provider
from bitacora.providers.claude import ClaudeProvider
from bitacora.providers.codex import CodexProvider


def all_providers(checkpoint_dir: Path | None = None) -> list[Provider]:
    checkpoint = checkpoint_dir / CodexProvider.CHECKPOINT_NAME if checkpoint_dir else None
    return [CodexProvider(checkpoint=checkpoint), ClaudeProvider()]


__all__ = ["Provider", "CodexProvider", "ClaudeProvider", "all_providers"]
