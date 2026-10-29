"""What every agent provider implements."""
from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from bitacora.model import ProcInfo, Run, Session
from bitacora.procs import Proc

# Values read from a transcript must prove they are one inert argument before they reach a command line.
RESUME_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,127}$")
BARE_ARG = re.compile(r"^[A-Za-z0-9._:@/+=+-]+$")


def resume_value(value: object) -> str | None:
    """A session id, model, effort or sandbox that is safe as a single shell argument."""
    return value if isinstance(value, str) and RESUME_VALUE.fullmatch(value) else None


def resume_cwd(value: object) -> str | None:
    """A working directory safe to quote into a resume command."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    return value


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def render_resume(spec: dict, shell: str | None = None) -> str:
    """Render a validated resume spec for one shell. The app only copies this text; it never runs it."""
    shell = shell or ("powershell" if os.name == "nt" else "sh")
    argv = list(spec.get("argv") or ())
    cwd = spec.get("cwd")
    if shell == "powershell":
        args = " ".join(arg if BARE_ARG.fullmatch(arg) else _powershell_quote(arg) for arg in argv)
        return f"Set-Location -LiteralPath {_powershell_quote(cwd)}; {args}" if cwd else args
    args = shlex.join(argv)
    return f"cd -- {shlex.quote(cwd)} && {args}" if cwd else args


@dataclass(frozen=True)
class Capabilities:
    """What a provider promises to answer; callers use these instead of probing."""
    processes: bool = True
    history: bool = True
    resume: bool = True
    quota: bool = True
    titles: bool = False


class Provider:
    name = ""  # stable id used in config and cache keys
    label = ""  # shown in the UI
    marker = ""  # substring that identifies the agent in a parent shell's command line
    session_cls: type[Session] = Session
    capabilities = Capabilities()
    process_names: tuple[str, ...] = ()  # executable prefixes worth reading; empty means the shared default

    def home(self) -> Path:
        raise NotImplementedError

    def available(self) -> bool:
        return self.home().exists()

    def procs(self, snap: list[Proc]) -> list[ProcInfo]:
        """Agent processes in the snapshot that own a session."""
        return []

    def match(self, pi: ProcInfo, claimed: set[Path], peers: Iterable[ProcInfo] = ()) -> Path | None:
        """The session file a live process is writing to. `peers` are the provider's other live processes."""
        return None

    def history(self, since: float) -> Iterable[Path]:
        """Session files touched after `since` (epoch seconds)."""
        return []

    def resume(self, run: Run) -> dict | None:
        """The argv and cwd that resume this run, already validated for rendering."""
        return None

    def resume_cmd(self, run: Run, shell: str | None = None) -> str | None:
        if not self.capabilities.resume:
            return None
        spec = self.resume(run)
        return render_resume(spec, shell) if spec else None

    def title(self, session: Session) -> str | None:
        """A name for the session kept outside its file (thread index, rename log...)."""
        return None

    def quota(self, sessions: Iterable[Session]) -> dict | None:
        """Return normalized usage metadata: reporting is windows, limit-only, or none.

        Windows carry id, name, minutes, scope, used_percent, resets_at, as_of, and source. Limits carry
        window, name, scope, reached_at, resets_at, reset_text, and source.
        """
        return None
