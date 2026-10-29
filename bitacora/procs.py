"""Process snapshot and log discovery from the parent shell's redirects."""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import psutil

from bitacora.util import label_from_out, norm_path


@dataclass
class Proc:
    pid: int
    name: str
    cmdline: list
    create: float
    ppid: int = 0

    @property
    def exe(self) -> str:
        return (self.cmdline[0] if self.cmdline else "").lower()

    def cwd(self) -> str | None:
        try:
            return psutil.Process(self.pid).cwd()
        except psutil.Error:
            return None


# Executable names worth a closer look. Reading every process's details is slow on Windows.
CANDIDATES = ("codex", "claude", "node", "bun")


def snapshot(names: Iterable[str] = CANDIDATES) -> list[Proc]:
    """Processes whose executable starts with a declared prefix; the filter avoids slow per-process reads."""
    prefixes = tuple(name.lower() for name in names if isinstance(name, str) and name) or CANDIDATES
    out = []
    for p in psutil.process_iter(["name"]):
        name = (p.info.get("name") or "").lower()
        if not name.startswith(prefixes):
            continue
        try:
            with p.oneshot():
                out.append(Proc(p.pid, name, p.cmdline() or [], p.create_time(), p.ppid()))
        except psutil.Error:
            continue
    return out


LOG_RES = [
    re.compile(r"(?<![0-9&<])>>?\|?\s*\\?[\"']?([^\s\"'\\;|&<>]+\.(?:log|txt|out))"),
    re.compile(r"-RedirectStandard(?:Output|Error)\s+[\"']?([^\"'\s]+)"),
    re.compile(r"\*>>?\s*[\"']?([^\"'\s;]+)"),
]
ASSIGN_RE = re.compile(r"\b([A-Za-z_]\w*)=\\?[\"']([^\"'\\]+)\\?[\"']")
CD_RE = re.compile(r"\bcd\s+\\?[\"']?([^\s\"'\\;&]+)")


def find_log_in_cmd(cmd: str, pcwd: str | None, out: str | None) -> str | None:
    """Find a `> run.log` style redirect in a shell command line and resolve it."""
    vars_ = dict(ASSIGN_RE.findall(cmd))

    def expand(s: str) -> str:
        return re.sub(r"\$\{?(\w+)\}?", lambda m: vars_.get(m.group(1), m.group(0)), s)

    for rx in LOG_RES:
        for m in rx.finditer(cmd):
            cand = expand(m.group(1))
            if "$" in cand:
                continue
            cand_p = norm_path(cand)
            if not os.path.isabs(cand_p):
                cdm = CD_RE.search(cmd)
                bases = [norm_path(expand(cdm.group(1))) if cdm else None,
                         os.path.dirname(out) if out else None, pcwd]
                for b in bases:
                    if b and os.path.exists(os.path.join(b, cand_p)):
                        return os.path.join(b, cand_p)
                continue
            if os.path.exists(cand_p):
                return cand_p
    return None


def find_log(pid: int, out: str | None, marker: str) -> str | None:
    """Look for the log redirect in the command lines of the agent's parent shells."""
    try:
        parents = psutil.Process(pid).parents()[:6]
    except psutil.Error:
        parents = []
    for par in parents:
        try:
            cmd = " ".join(par.cmdline())
            pcwd = par.cwd()
        except psutil.Error:
            continue
        if marker not in cmd:
            continue
        found = find_log_in_cmd(cmd, pcwd, out)
        if found:
            return found
    if out:  # heuristic: b0.result.md -> b0.log
        stem = label_from_out(out)
        for name in (f"{stem}.log", f"{Path(out).stem}.log"):
            cand = os.path.join(os.path.dirname(out), name)
            if os.path.exists(cand):
                return cand
    return None


def kill_tree(pid: int, expected_create: float | None = None) -> int:
    p = psutil.Process(pid)
    if expected_create is not None and abs(p.create_time() - expected_create) > 0.01:
        raise psutil.NoSuchProcess(pid)
    procs = p.children(recursive=True) + [p]
    killed = 0
    for c in procs:
        try:
            c.kill()
            killed += 1
        except psutil.NoSuchProcess:
            continue
        except psutil.Error:
            if c is p:
                raise
    _, alive = psutil.wait_procs(procs, timeout=3)
    if p in alive:
        raise psutil.AccessDenied(pid)
    return killed
