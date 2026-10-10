"""Shared, stable ordering for the run tables."""
from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Sequence

from bitacora.model import Run

RUN_COLUMNS = (
    "status", "agent", "name", "model", "repo", "now", "last", "dur", "ctx", "tok", "cmds", "files", "mode",
    "pid",
)
SORT_COLUMNS = frozenset(RUN_COLUMNS)
# Keep these names for callers that distinguish interface responsibilities. Both interfaces now sort every column.
TEXTUAL_SORT_COLUMNS = SORT_COLUMNS
DESKTOP_SORT_COLUMNS = SORT_COLUMNS
STATUS_RANK = {
    "asking": 0,
    "running": 1,
    "starting": 2,
    "quiet": 3,
    "your_turn": 4,
    "limited": 5,
    "failed": 6,
    "stopped": 7,
    "done": 8,
    "unknown": 9,
}


def cycle_sort(current_column: str | None, current_direction: str | None,
               column: str) -> tuple[str | None, str | None]:
    """Cycle a selected heading through ascending, descending, and the default order."""
    if column not in SORT_COLUMNS:
        return current_column, current_direction
    if current_column != column:
        return column, "asc"
    if current_direction == "asc":
        return column, "desc"
    return None, None


def step_sort(current_column: str | None, shown: Collection[str], step: int,
              order: Sequence[str]) -> tuple[str, str] | None:
    """The next (step 1) or previous (step -1) shown column, ascending, for the < and > keys.

    The walk follows `order` and wraps around. Without a sort it starts at the ends, so the first > sorts by the
    first column and the first < by the last one. A sorted column that is hidden at this width still steps from
    its place in `order`.
    """
    here = order.index(current_column) if current_column in order else (-1 if step > 0 else len(order))
    for k in range(1, len(order) + 1):
        column = order[(here + step * k) % len(order)]
        if column in shown:
            return column, "asc"
    return None


def sort_runs(runs: Iterable[Run], column: str | None = None,
              direction: str | None = None) -> list[Run]:
    """Sort runs with unknown values last and the default order as a stable tie-break."""
    baseline = sorted(runs, key=_default_key)
    if column not in SORT_COLUMNS or direction not in ("asc", "desc"):
        return baseline

    known: list[tuple[Run, str | float | int]] = []
    unknown: list[Run] = []
    for run in baseline:
        value = _sort_value(run, column)
        if value is None:
            unknown.append(run)
        else:
            known.append((run, value))
    known.sort(key=lambda item: item[1], reverse=direction == "desc")
    return [run for run, _ in known] + unknown


def _default_key(run: Run) -> tuple:
    start = run.start
    return (run.status != "asking", not run.live, start is None, -(start or 0), run.key.casefold(), run.key)


def _number(value) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value) else None


def _text(value) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result.casefold() if result and result not in ("-", "?") else None


def _sort_value(run: Run, column: str) -> str | int | float | None:
    session = run.session
    if column == "status":
        return None if run.status == "unknown" else STATUS_RANK.get(run.status)
    if column == "agent":
        from bitacora.ui.render import agent_table_text

        return _text(agent_table_text(run).plain)
    if column == "name":
        return _text(run.label)
    if column == "repo":
        return _text(run.repo)
    if column == "model":
        return _text(f"{run.model or '-'} {run.effort}" if run.effort else run.model)
    if column == "now":
        from bitacora.ui.render import activity

        return _text(activity(session, run, limit=None).plain)
    if column == "mode":
        return _text(run.sandbox)
    if column == "dur":
        return _number(run.duration)
    if column == "last":
        return _number(session.last_ts if session else None)
    if column == "cmds":
        return _number(session.n_cmds if session else None)
    if column == "files":
        return _number(len(session.files) if session else None)
    if column == "tok":
        return _number(session.tokens if session and session.tokens else None)
    if column == "ctx":
        if session is None:
            return None
        used = _number(session.context_tokens)
        window = _number(session.ctx_window)
        if used is None or window is None or window <= 0:
            return None
        return 100 * used / window
    if column == "pid":
        return _number(run.proc.pid if run.proc else None)
    return None
