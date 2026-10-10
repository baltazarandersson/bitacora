"""Rich renderables for events, the activity line and small widgets."""
from __future__ import annotations

from pathlib import Path

from rich.table import Table
from rich.text import Text

from bitacora.i18n import t
from bitacora.model import Event, Run, Session
from bitacora.themes import TEXT_CONTRAST, contrast, mix
from bitacora.util import first_line, fmt_ago, fmt_dur, hhmm

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
CMD_ICON = {"read": ("▤", "ev_read"), "search": ("⌕", "ev_search"), "list_files": ("▦", "ev_list")}
HIDDEN_TOOLS = {"wait", "write_stdin"}  # internal polling in Codex code mode


# Colors follow the active Textual theme; apply_theme() refreshes them. Defaults match the default theme, monokai.
THEME_VARS = {"background": "#272822", "foreground": "#d6d6d6", "success": "#a5e22e", "error": "#f82672",
              "warning": "#fc971f", "primary": "#ae81ff", "secondary": "#f82672", "accent": "#66d9ef"}
DARK = True
BG = FG = DIM = GREEN = RED = YELLOW = CYAN = BLUE = PURPLE = VIOLET = ""
STATUS_STYLE: dict[str, str] = {}
AGENT_STYLE: dict[str, str] = {}


def readable(c: str, bg: str, fg: str) -> str:
    """The color itself if it reads on the background; otherwise pulled toward the text color until it does."""
    for k in range(21):
        cand = mix(c, fg, k / 20)
        if contrast(cand, bg) >= TEXT_CONTRAST:
            return cand
    return fg


def apply_theme(variables: dict[str, str] | None = None) -> None:
    global DARK, BG, FG, DIM, GREEN, RED, YELLOW, CYAN, BLUE, PURPLE, VIOLET, STATUS_STYLE, AGENT_STYLE
    v = dict(THEME_VARS)
    for k, val in (variables or {}).items():
        if k in v and isinstance(val, str) and val.startswith("#") and len(val) == 7:
            v[k] = val.lower()
    BG, FG = v["background"], v["foreground"]
    DARK = sum(int(BG[i:i + 2], 16) for i in (1, 3, 5)) < 384
    # Every color used for text must reach WCAG AA on the background. The dim tone was 3.6:1 in the default
    # theme, and several status colors were under 2:1 on light themes.
    DIM = readable(mix(FG, BG, 0.42), BG, FG)
    GREEN, RED, YELLOW = (readable(v[k], BG, FG) for k in ("success", "error", "warning"))
    CYAN, PURPLE = (readable(v[k], BG, FG) for k in ("secondary", "primary"))
    BLUE = readable(mix(v["secondary"], FG, 0.35), BG, FG)
    VIOLET = readable(mix(v["primary"], FG, 0.3), BG, FG)
    limited = readable(mix(v["warning"], v["error"], 0.48), BG, FG)
    stopped = readable(mix(v["error"], v["foreground"], 0.35), BG, FG)
    quiet = readable(mix(v["secondary"], BG, 0.35), BG, FG)
    STATUS_STYLE = {"asking": f"bold {YELLOW}", "starting": CYAN, "running": f"bold {GREEN}",
                    "your_turn": BLUE, "done": GREEN, "failed": RED, "limited": limited, "stopped": stopped,
                    "quiet": quiet, "unknown": DIM}
    AGENT_STYLE = {"codex": CYAN, "claude": v["accent"]}


apply_theme()


def status_text(run: Run) -> Text:
    return Text(t(f"st_{run.status}"), style=STATUS_STYLE.get(run.status, DIM))


def _agent_parts(run: Run) -> tuple[str, str, str]:
    provider = str(run.provider).lower().replace("_", "-")
    if provider in ("claude", "claude-code"):
        provider_key = "claude"
        style = AGENT_STYLE.get("claude", "")
    elif provider == "codex":
        provider_key = "codex"
        style = AGENT_STYLE.get("codex", "")
    else:
        provider_key = "other"
        style = AGENT_STYLE.get(run.provider, "")
    session = run.session
    kind = getattr(session, "origin_kind", getattr(run, "origin_kind", "other"))
    if kind == "other" and session:
        if provider in ("claude", "claude-code"):
            from bitacora.providers.claude import origin_kind_for_raw
            kind = origin_kind_for_raw(getattr(session, "origin", None))
        elif provider == "codex":
            from bitacora.providers.codex import origin_kind_for_raw
            kind = origin_kind_for_raw(getattr(session, "origin", None))
    kind = kind if kind in {"terminal", "background", "desktop", "ide", "subagent", "other"} else "other"
    return provider_key, kind, style


def agent_text(run: Run) -> Text:
    """The full provider and origin label used in the hero and Details."""
    provider_key, kind, style = _agent_parts(run)
    provider_label = t(f"provider_{provider_key}") if provider_key in ("claude", "codex") else str(run.provider)
    tx = Text(provider_label, style=style)
    tx.append(" · ", style=DIM)
    tx.append(t(f"origin_{kind}"))
    return tx


def agent_table_text(run: Run) -> Text:
    """The compact provider and origin label shown in the run table."""
    provider_key, kind, style = _agent_parts(run)
    provider_label = (
        t(f"provider_{provider_key}_short") if provider_key in ("claude", "codex")
        else str(run.provider)
    )
    tx = Text(provider_label, style=style)
    tx.append(" · ", style=DIM)
    tx.append(t(f"origin_short_{kind}"))
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


def ago_text(ts: float | None) -> Text:
    """' · 7s ago' after an event, or nothing when the event has no timestamp (never '- ago')."""
    return Text(t("act_ago", age=fmt_ago(ts)), style=DIM) if ts else Text("")


def activity(s: Session | None, run: Run, *, limit: int | None = 60) -> Text:
    """What the agent is doing now, inferred from the latest events."""
    def line(text: str) -> str:
        return first_line(text, limit) if limit is not None else first_line(text, max(1, len(text)))

    if s is None:
        return Text(t("act_wait_file"), style=DIM)
    age = fmt_ago(s.last_ts)
    if s.open_questions:
        question = next(iter(s.open_questions.values()), "")
        if not question:
            ask = next((event for event in reversed(s.events) if event.kind == "ask"), None)
            question = (ask.detail or ask.text) if ask else ""
        return Text(line(question), style=f"bold {YELLOW}")
    if run.status == "your_turn":
        return Text(t("act_your_turn", age=age), style=BLUE)
    if not run.live:
        return Text(t("act_no_proc", age=age), style=DIM)
    if s.in_flight:
        ev = list(s.in_flight.values())[-1]
        tx = Text(t("act_running"), style=DIM)
        tx.append(line(ev.text), style=CYAN)
        tx.append_text(ago_text(ev.ts))
        return tx
    ev = s.last_event
    if ev is None:
        return Text(t("act_starting"), style=DIM)
    tx = Text()
    if ev.kind == "think":
        tx.append(t("act_thinking"), style=f"italic {VIOLET}")
        tx.append_text(ago_text(ev.anchor))
        return tx
    if ev.kind == "cmd":
        tx.append(t("act_ran"), style=DIM)
        tx.append(line(ev.text), style=CYAN)
    elif ev.kind == "file":
        tx.append(t("act_edited"), style=DIM)
        tx.append(Path(ev.text).name, style=PURPLE)
    elif ev.kind == "msg":
        tx.append(t("act_wrote"), style=DIM)
        tx.append(line(ev.detail or ev.text))
    elif ev.kind == "turn":
        tx.append(t("ev_turn_" + ev.sub) if ev.sub else ev.text, style=GREEN)
    else:
        tx.append(line(ev.text))
    tx.append_text(ago_text(s.last_ts))
    return tx
