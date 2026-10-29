"""Repeatable home-scene benchmark: frame cost, cells, segments and the desktop payload estimate.

Measures Scene.frame with a fixed clock, tagline and time sequence, so runs are comparable between changes.
Prints JSON (no session data) and can also write it to a file.

    python scripts/bench_home.py --out /tmp/home.json
    python scripts/bench_home.py --frames 20
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime

from bitacora.ui.scene import Scene

SIZES = ((80, 16), (120, 16), (200, 50))
NOW = datetime(2026, 3, 14, 21, 30)  # night: stars, beam and moon are all active
TAGLINE = "2 agents running · 1 waiting"
SEED = 7
FPS = 15.0


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))], 2)


def payload(frame: list[list], colors: dict, columns: int) -> tuple[dict, float]:
    """The desktop bridge shape: palette plus per-cell runs [text, fg, bg, bold]."""
    started = time.perf_counter()
    palette: dict[str, int] = {}
    cells = []
    for row in frame:
        line = []
        for segment in row:
            text = segment.text.replace("\r", "").replace("\n", "")
            if not text:
                continue
            style = segment.style
            key = colors.get(style)
            if key is None:
                fg = style.color.get_truecolor().hex if style and style.color else "#a9b1d6"
                bg = style.bgcolor.get_truecolor().hex if style and style.bgcolor else "#060a17"
                key = colors[style] = (fg, bg, int(bool(style and style.bold)))
            fg = palette.setdefault(key[0], len(palette))
            bg = palette.setdefault(key[1], len(palette))
            if line and line[-1][1:] == [fg, bg, key[2]]:
                line[-1][0] += text
            else:
                line.append([text, fg, bg, key[2]])
        cells.append(line)
    data = {"columns": columns, "rows": len(frame), "palette": list(palette), "cells": cells}
    encoded = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return {"palette": len(palette), "rows": len(cells), "runs": sum(len(line) for line in cells),
            "bytes": encoded}, (time.perf_counter() - started) * 1000


def measure(w: int, h: int, frames: int) -> dict:
    scene = Scene(w, h, seed=SEED)
    scene.frame(0.0, NOW, TAGLINE)  # build the static layer before timing, exactly like a warm scene
    scene.last_t = 0.0
    times: list[float] = []
    segments = 0
    colors: dict = {}
    frame: list[list] = []
    payload_ms: list[float] = []
    for index in range(frames):
        started = time.perf_counter()
        frame = scene.frame((index + 1) / FPS, NOW, TAGLINE)
        times.append((time.perf_counter() - started) * 1000)
        segments += sum(len(row) for row in frame)
        if w == 120:  # the desktop bridge only ever asks for 16 rows; the payload estimate is at 120 columns
            payload_stats, took = payload(frame, colors, w)
            payload_ms.append(took)
    return {"size": f"{w}x{h}", "columns": w, "rows": h, "frames": frames, "cells": w * h,
            "ms": {"p50": percentile(times, 0.5), "p95": percentile(times, 0.95),
                   "mean": round(statistics.fmean(times), 2), "max": round(max(times), 2)},
            "segments": {"per_frame": round(segments / frames, 2), "total": segments},
            "styles_cached": len(scene.styles),
            "payload": {**payload_stats, "ms_p50": percentile(payload_ms, 0.5)} if payload_ms else None}


def run(frames: int = 40) -> dict:
    return {"python": sys.version.split()[0], "now": NOW.isoformat(), "tagline": TAGLINE, "fps": FPS,
            "seed": SEED, "results": [measure(w, h, frames) for w, h in SIZES]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=40, help="frames per size (default 40)")
    parser.add_argument("--out", help="also write the JSON here")
    args = parser.parse_args(argv)
    data = run(max(1, args.frames))
    text = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
