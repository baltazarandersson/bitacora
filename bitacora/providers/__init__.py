"""Agent providers. Each one knows where its agent keeps sessions and how its processes look."""
from __future__ import annotations

from pathlib import Path

from bitacora.providers.base import Provider
from bitacora.providers.claude import ClaudeProvider
from bitacora.providers.codex import CodexProvider
from bitacora.providers.opencode import OpenCodeProvider


def all_providers(checkpoint_dir: Path | None = None, enabled: dict[str, bool] | None = None) -> list[Provider]:
    """Every provider, minus those `enabled` (from config.toml [providers]) switches off."""
    checkpoint = checkpoint_dir / CodexProvider.CHECKPOINT_NAME if checkpoint_dir else None
    providers = [CodexProvider(checkpoint=checkpoint), ClaudeProvider(), OpenCodeProvider()]
    return [provider for provider in providers if (enabled or {}).get(provider.name) is not False]


__all__ = ["Provider", "CodexProvider", "ClaudeProvider", "OpenCodeProvider", "all_providers"]
