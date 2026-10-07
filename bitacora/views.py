"""Views: saved filters over the sessions Bitácora finds."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bitacora.model import Run


@dataclass(frozen=True)
class View:
    id: str
    name: str  # i18n key for built-in views, literal text for custom ones
    providers: tuple[str, ...] | None = None  # None = every provider
    headless: bool | None = None  # True = background runs, False = interactive, None = both
    live_only: bool = False
    builtin: bool = True

    def matches(self, run: Run) -> bool:
        if self.providers is not None and run.provider not in self.providers:
            return False
        if self.headless is not None and run.headless != self.headless:
            return False
        return run.live or not self.live_only


BUILTIN = [
    View("background", "view_background", headless=True),
    View("everything", "view_everything"),
    View("interactive", "view_interactive", headless=False),
    View("live", "view_live", live_only=True),
]
DEFAULT_VIEW = "background"


def custom_view(vid: str, raw: dict) -> View | None:
    providers = raw.get("providers")
    if providers is not None and not (isinstance(providers, list) and all(isinstance(p, str) for p in providers)):
        return None
    kind = raw.get("kind", "all")
    headless = {"background": True, "interactive": False}.get(kind)
    return View(vid, str(raw.get("name") or vid), tuple(providers) if providers else None,
                headless, bool(raw.get("live_only", False)), builtin=False)


def all_views(custom: dict[str, dict]) -> list[View]:
    views = list(BUILTIN)
    taken = {v.id for v in views}
    for vid, raw in custom.items():
        v = custom_view(vid, raw)
        if v and vid not in taken:
            views.append(v)
    return views


def counts(view: View, runs: Iterable[Run]) -> tuple[int, int]:
    """(sessions, live sessions) the view would show."""
    shown = [r for r in runs if view.matches(r)]
    return len(shown), sum(1 for r in shown if r.live)
