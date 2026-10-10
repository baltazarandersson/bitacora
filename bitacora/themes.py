"""Shared theme names, desktop color descriptors and the contrast rule for text on highlights."""
from __future__ import annotations

import math
from collections.abc import Iterable

THEMES = ["tokyo-night", "catppuccin-mocha", "nord", "gruvbox", "dracula", "monokai",
          "textual-dark", "rose-pine", "solarized-light", "textual-light"]
DEFAULT_THEME = "monokai"  # where nothing is saved yet, and where a saved name is no longer offered
TEXT_CONTRAST = 4.5  # WCAG AA for normal text
PLAIN = ("#000000", "#ffffff")  # the fallback ink: black or white, whichever reads better on the highlight


def luminance(color: str) -> float:
    """Relative luminance (WCAG 2) of a #rrggbb color."""
    def lin(v: int) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two #rrggbb colors (1 to 21)."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, k: float) -> str:
    """Blend two #rrggbb colors; k=0 gives a, k=1 gives b. Rounds half up, as the desktop's Math.round does."""
    k = max(0.0, min(1.0, k))
    channels = [int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * k for i in (1, 3, 5)]
    return "#" + "".join(f"{math.floor(v + 0.5):02x}" for v in channels)


def rule_color(background: str, foreground: str) -> str:
    """The still line under the header: the foreground blended into the background until it reads at 1.6:1.

    It is a decoration, not text, so the bar is lower than TEXT_CONTRAST. app.js (ruleColor) applies the same steps.
    """
    for step in range(18, 101):
        candidate = mix(background, foreground, step / 100)
        if contrast(candidate, background) >= 1.6:
            return candidate
    return foreground


def ink_on(background: str, text: Iterable[str]) -> str:
    """The text color for a highlight on `background`: the darkest or lightest of the theme's own text colors,
    whichever reads better, if it reaches TEXT_CONTRAST; otherwise black or white, whichever is further from the
    background. Black or white always reaches it, so the result never falls below TEXT_CONTRAST.

    desktop_assets/app.js applies the same rule (ink) to --sel-fg and --ask-fg: keep the two in step.
    """
    colors = list(text)
    theme_ink = max((min(colors, key=luminance), max(colors, key=luminance)), key=lambda c: contrast(c, background))
    if contrast(theme_ink, background) >= TEXT_CONTRAST:
        return theme_ink
    return max(PLAIN, key=lambda c: contrast(c, background))


def descriptors() -> list[dict]:
    from textual.theme import BUILTIN_THEMES

    out = []
    for name in THEMES:
        if name not in BUILTIN_THEMES:
            continue
        variables = BUILTIN_THEMES[name].to_color_system().generate()
        colors = {key: variables[key] for key in
                  ("background", "foreground", "accent", "primary", "secondary", "success", "warning", "error")}
        colors["panel"] = variables["surface"]
        colors["muted"] = colors["foreground"]
        out.append({"id": name, "name": name, "colors": colors})
    return out
