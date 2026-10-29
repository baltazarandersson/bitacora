"""Small helpers shared by the providers and the UI."""
from __future__ import annotations

import math
import os
import re
import time
from datetime import datetime
from pathlib import Path

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _format_error(exc: BaseException) -> str:
    """Reader diagnostics omit exception filenames and payloads, which can contain local session details."""
    name = type(exc).__name__
    code = getattr(exc, "errno", None)
    detail = getattr(exc, "strerror", None)
    if code is not None and detail:
        return f"{name} (errno {code}): {detail[:160]}"
    return name


def access_kind(raw: str | None, provider: str) -> str | None:
    """Normalize a provider's raw sandbox or permission mode for presentation."""
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip().casefold()
    if provider == "claude":
        return {
            "default": "prompt",
            "acceptedits": "auto-edit",
            "plan": "plan",
            "bypasspermissions": "full-access",
        }.get(value, "other")
    return {
        "read-only": "read-only",
        "workspace-write": "workspace-write",
        "prompt": "prompt",
        "auto-edit": "auto-edit",
        "plan": "plan",
        "danger-full-access": "full-access",
        "full-access": "full-access",
    }.get(value, "other")


def parse_iso(ts: str | None) -> float | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def as_epoch(value) -> float | None:
    """Normalize numeric or ISO timestamp input to an epoch value."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return parse_iso(value)
        return numeric if math.isfinite(numeric) else None
    return None


def norm_path(p: str | None) -> str | None:
    """Turn /c/Users/... (msys) or file:/// into a native path."""
    if not p:
        return None
    p = p.strip().strip("\"'")
    if p.startswith("file:///"):
        p = p[8:]
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m and os.name == "nt":
        p = f"{m.group(1).upper()}:/{m.group(2)}"
    return os.path.normpath(p)


def same_path(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(norm_path(a)) == os.path.normcase(norm_path(b))


def rel_to(path: str, base: str | None) -> str:
    if base and path.lower().startswith(base.lower()):
        return path[len(base):].lstrip("\\/") or path
    return path


def fmt_dur(sec: float | None) -> str:
    if sec is None or sec < 0:
        return "-"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h >= 48:
        return f"{h // 24}d{h % 24:02d}h"
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s" if m else f"{s}s"


def fmt_ago(ts: float | None) -> str:
    return "-" if ts is None else fmt_dur(time.time() - ts)


def fmt_tokens(n: int | None) -> str:
    if not n:
        return "-"
    for unit, div in (("M", 1_000_000), ("k", 1_000)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(n)


def hhmm(ts: float | None) -> str:
    return "--:--:--" if ts is None else datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def first_line(s: str | None, width: int = 160) -> str:
    s = (s or "").strip()
    s = s.splitlines()[0] if s else ""
    return s if len(s) <= width else s[: width - 1] + "…"


def label_from_prompt(prompt: str | None) -> str:
    s = first_line(re.sub(r"^[#>*\s]+", "", prompt or ""), 200)
    s = re.sub(r"^(task|tarea)\s*:\s*", "", s, flags=re.I)
    return first_line(s, 40)


def label_from_out(out: str | None) -> str | None:
    if not out:
        return None
    name = Path(out).name
    for suf in (".result.md", "-result.md", "_result.md", ".md", ".txt"):
        if name.lower().endswith(suf):
            return name[: -len(suf)] or name
    return name


def count_lines(text: str | None) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)
