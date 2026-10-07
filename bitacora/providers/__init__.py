"""Agent providers. Each one knows where its agent keeps sessions and how its processes look."""
from __future__ import annotations

from bitacora.providers.base import Provider
from bitacora.providers.claude import ClaudeProvider
from bitacora.providers.codex import CodexProvider


def all_providers() -> list[Provider]:
    return [CodexProvider(), ClaudeProvider()]


__all__ = ["Provider", "CodexProvider", "ClaudeProvider", "all_providers"]
