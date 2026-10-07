"""Rich renderables for events, the activity line and small widgets."""
from __future__ import annotations

from pathlib import Path

from rich.table import Table
from rich.text import Text

from bitacora.i18n import t
from bitacora.model import Event, Run, Session
from bitacora.util import first_line, fmt_ago, fmt_dur, hhmm

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
CMD_ICON = {"read": ("▤", "ev_read"), "search": ("⌕", "ev_search"), "list_files": ("▦", "ev_list")}
HIDDEN_TOOLS = {"wait", "write_stdin"}  # internal polling in Codex code mode


def mix(a: str, b: str, k: float) -> str:
    """Blend two #rrggbb colors; k=0 gives a, k=1 gives b."""
    k = max(0.0, min(1.0, k))
    ra, ga, ba = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    rb, gb, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return f"#{round(ra + (rb - ra) * k):02x}{round(ga + (gb - ga) * k):02x}{round(ba + (bb - ba) * k):02x}"


# Colors follow the active Textual theme; apply_theme() refreshes them. Defaults match tokyo-night.
THEME_VARS = {"background": "#1a1b26", "foreground": "#a9b1d6", "success": "#9ece6a", "error": "#f6768e",
              "warning": "#dfaf68", "primary": "#bb9af7", "secondary": "#7aa2f7", "accent": "#fe9e64"}
DARK = True
BG = FG = DIM = GREEN = RED = YELLOW = CYAN = BLUE = PURPLE = VIOLET = ""
STATUS_STYLE: dict[str, str] = {}
AGENT_STYLE: dict[str, str] = {}


def apply_theme(variables: dict[str, str] | None = None) -> None:
    global DARK, BG, FG, DIM, GREEN, RED, YELLOW, CYAN, BLUE, PURPLE, VIOLET, STATUS_STYLE, AGENT_STYLE
    v = dict(THEME_VARS)
    for k, val in (variables or {}).items():
        if k in v and isinstance(val, str) and val.startswith("#") and len(val) == 7:
            v[k] = val.lower()
    BG, FG = v["background"], v["foreground"]
    DARK = sum(int(BG[i:i + 2], 16) for i in (1, 3, 5)) < 384
    DIM = mix(FG, BG, 0.42)
    GREEN, RED, YELLOW = v["success"], v["error"], v["warning"]
    CYAN, PURPLE = v["secondary"], v["primary"]
    BLUE = mix(v["secondary"], FG, 0.35)
    VIOLET = mix(v["primary"], FG, 0.3)
    STATUS_STYLE = {"ask": f"bold {YELLOW}", "starting": CYAN, "running": f"bold {GREEN}", "idle": BLUE,
                    "active": GREEN, "done": GREEN, "aborted": RED, "interrupted": RED, "unknown": DIM}
    AGENT_STYLE = {"codex": CYAN, "claude": v["accent"]}


apply_theme()


def status_text(run: Run) -> Text:
    return Text(t(f"st_{run.status}"), style=STATUS_STYLE.get(run.status, DIM))


def agent_text(run: Run) -> Text:
    tx = Text(run.provider, style=AGENT_STYLE.get(run.provider, ""))
    if run.origin not in ("?", ""):
        tx.append(f" {run.origin}", style=DIM)
    return tx


def pct_style(frac: float) -> str:
    return GREEN if frac < 0.6 else YELLOW if frac < 0.85 else RED


def bar(frac: float | None, width: int = 10) -> Text:
    if frac is None:
        return Text("░" * width, style=DIM)
    frac = max(0.0, min(1.0, frac))
    n = round(frac * width)
    tx = Text("█" * n, style=pct_style(frac))
    tx.append("░" * (width - n), style=DIM)
    return tx


def indent_block(text: str, style: str, prefix: str = "", max_lines: int | None = None,
                 tail: bool = False) -> Text:
    lines = text.rstrip().splitlines()
    cut = 0
    if max_lines and len(lines) > max_lines:
        cut = len(lines) - max_lines
        lines = lines[-max_lines:] if tail else lines[:max_lines]
    tx = Text()
    if cut and tail:
        tx.append(f"\n{prefix}{t('ev_lines_before', n=cut)}", style=DIM)
    for ln in lines:
        tx.append(f"\n{prefix}", style=DIM)
        tx.append(ln[:400], style=style)
    if cut and not tail:
        tx.append(f"\n{prefix}{t('ev_lines_more', n=cut)}", style=DIM)
    return tx


def think_badge(think: Event | None) -> Text:
    if think is None or think.dur < 1:
        return Text("")
    return Text(f"◇{fmt_dur(think.dur)}", style=f"italic {VIOLET}")


def visible(ev: Event, verbose: bool) -> bool:
    if verbose:
        return True
    return not (ev.kind in ("think", "note") or (ev.kind == "tool" and ev.sub in HIDDEN_TOOLS))


def render_event(ev: Event, verbose: bool, think: Event | None = None) -> Table:
    """One row: time | thinking time before the action | content with a hanging indent."""
    body = Text()
    k = ev.kind
    if k == "think":
        body.append(t("ev_thinking"), style=f"italic {VIOLET}")
        info = [t("ev_steps", n=ev.count)] if ev.count > 1 else []
        if ev.dur >= 1:
            info.append(fmt_dur(ev.dur))
        if info:
            body.append("  " + " · ".join(info), style=f"italic {DIM}")
        if ev.detail:
            body.append(indent_block(ev.detail, f"italic {VIOLET}", "┆ "))
        elif ev.dur >= 1:
            body.append(t("ev_hidden"), style=DIM)
    elif k == "cmd":
        icon, verb = CMD_ICON.get(ev.sub, ("❯", ""))
        body.append(f"{icon} ", style=f"bold {CYAN}")
        if verb:
            body.append(f"{t(verb)} ", style=CYAN)
        lines = ev.text.splitlines() or [""]
        if not verbose:
            lines = [first_line(" ".join(ln.strip() for ln in lines), 150)]
        body.append(lines[0][:300] if verbose else lines[0], style="bold" if not ev.ok else "")
        dur = f" {ev.dur:.1f}s" if ev.dur >= 0.05 else ""
        if ev.ok:
            body.append(f"  ✓{dur}", style=GREEN)
        else:
            code = f" exit {ev.code}" if ev.code and ev.code != "error" else ""
            body.append(f"  ✗{code}{dur}", style=f"bold {RED}")
        if len(lines) > 1:
            body.append(indent_block("\n".join(lines[1:]), CYAN, "  ", None if verbose else 2))
        if ev.detail and (verbose or not ev.ok):
            body.append(indent_block(ev.detail, RED if not ev.ok else DIM, "│ ", 14 if verbose else 5, tail=True))
    elif k == "file":
        kind = t(f"ev_{ev.sub}") if ev.sub in ("add", "update", "delete") else ev.sub
        body.append("✎ ", style=f"bold {PURPLE}")
        body.append(f"{kind} ", style=PURPLE)
        body.append(ev.text, style="bold")
        if ev.plus:
            body.append(f"  +{ev.plus}", style=GREEN)
        if ev.minus:
            body.append(f" −{ev.minus}", style=RED)
        if verbose and ev.detail:
            for ln in ev.detail.splitlines()[:40]:
                st = GREEN if ln.startswith("+") else RED if ln.startswith("-") else \
                    CYAN if ln.startswith("@@") else DIM
                body.append("\n  ", style=DIM)
                body.append(ln[:300], style=st)
    elif k == "msg":
        lines = (ev.detail or ev.text).strip().splitlines() or [""]
        body.append("● ", style=f"bold {FG}")
        body.append(lines[0], style=f"bold {FG}")
        if len(lines) > 1:
            body.append(indent_block("\n".join(lines[1:]), FG, "  ", None if verbose else 10))
    elif k == "user":
        body.append(t("ev_prompt"), style=f"bold {BLUE}")
        body.append(ev.text, style=BLUE)
        if verbose and ev.detail:
            body.append(indent_block(ev.detail, BLUE, "  ", 40))
    elif k == "ask":
        body.append(t("ev_question"), style=f"bold black on {YELLOW}")
        body.append("  " + (ev.detail or ev.text), style=f"bold {YELLOW}")
    elif k == "turn":
        body.append(f"──── {t('ev_turn_' + ev.sub) if ev.sub else ev.text} ────", style=GREEN)
    elif k == "err":
        body.append("✗ ", style=f"bold {RED}")
        body.append(t("ev_aborted", why=ev.text) if ev.sub == "aborted" else ev.text, style=RED)
        if verbose and ev.detail:
            body.append(indent_block(ev.detail, RED, "│ ", 14, tail=True))
    elif k == "note":
        body.append(f"· {ev.text}", style=DIM)
    else:
        body.append("ƒ ", style=YELLOW)
        body.append(ev.text, style=YELLOW)
        if verbose and ev.detail:
            body.append(indent_block(ev.detail, DIM, "│ ", 8, tail=True))
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(width=8, style=DIM, no_wrap=True)
    grid.add_column(width=6, no_wrap=True, justify="right")
    grid.add_column(ratio=1)
    grid.add_row(hhmm(ev.ts), think_badge(think), body)
    return grid


def activity(s: Session | None, run: Run) -> Text:
    """What the agent is doing now, inferred from the latest events."""
    if s is None:
        return Text(t("act_wait_file"), style=DIM)
    age = fmt_ago(s.last_ts)
    if s.open_questions:
        return Text(t("act_wait_answer"), style=f"bold {YELLOW}")
    if run.status == "idle":
        return Text(t("act_your_turn", age=age), style=BLUE)
    if not run.live:
        return Text(t("act_no_proc", age=age), style=DIM)
    if s.in_flight:
        ev = list(s.in_flight.values())[-1]
        tx = Text(t("act_running"), style=DIM)
        tx.append(first_line(ev.text, 60), style=CYAN)
        tx.append(t("act_ago", age=fmt_ago(ev.ts)), style=DIM)
        return tx
    ev = s.last_event
    if ev is None:
        return Text(t("act_starting"), style=DIM)
    tx = Text()
    if ev.kind == "think":
        tx.append(t("act_thinking"), style=f"italic {VIOLET}")
        tx.append(f" · {fmt_ago(ev.anchor)}", style=DIM)
        return tx
    if ev.kind == "cmd":
        tx.append(t("act_ran"), style=DIM)
        tx.append(first_line(ev.text, 60), style=CYAN)
    elif ev.kind == "file":
        tx.append(t("act_edited"), style=DIM)
        tx.append(Path(ev.text).name, style=PURPLE)
    elif ev.kind == "msg":
        tx.append(t("act_wrote"), style=DIM)
        tx.append(first_line(ev.detail or ev.text, 60))
    elif ev.kind == "turn":
        tx.append(t("ev_turn_" + ev.sub) if ev.sub else ev.text, style=GREEN)
    else:
        tx.append(first_line(ev.text, 60))
    tx.append(t("act_ago", age=age), style=DIM)
    return tx
