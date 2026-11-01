"""What every agent provider implements."""
from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from bitacora.model import ProcInfo, Run, Session
from bitacora.procs import Proc
from bitacora.sources import FileReader, SessionReader, SessionRef, ref_key

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


# PowerShell ends a single-quoted string at any of these; doubling one makes it literal.
_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def _powershell_quote(value: str) -> str:
    return "'" + "".join(ch * 2 if ch in _PS_QUOTES else ch for ch in value) + "'"


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


class Support(StrEnum):
    """How completely a provider can answer a capability: full, partial or not at all."""
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"

    def __bool__(self) -> bool:
        return self is not Support.NONE


_SUPPORT_FIELDS = ("resume", "messages", "commands", "files", "questions", "usage", "context", "quota",
                   "titles", "subagents")


@dataclass(frozen=True)
class Capabilities:
    """What a provider promises to answer; every field must be declared explicitly."""
    processes: bool
    history: bool
    resume: Support
    messages: Support
    commands: Support
    files: Support
    questions: Support
    usage: Support
    context: Support
    quota: Support
    titles: Support
    subagents: Support

    def __post_init__(self) -> None:
        for name in ("processes", "history"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")
        for name in _SUPPORT_FIELDS:
            value = getattr(self, name)
            if isinstance(value, Support):
                continue
            if isinstance(value, bool):
                object.__setattr__(self, name, Support.FULL if value else Support.NONE)
            elif isinstance(value, str):
                object.__setattr__(self, name, Support(value))
            else:
                raise TypeError(f"{name} must be a Support value")


class Provider:
    name = ""  # stable id used in config and cache keys
    label = ""  # shown in the UI
    marker = ""  # substring that identifies the agent in a parent shell's command line
    home_env = ""  # environment variable that overrides the provider home, if any
    session_cls: type[Session] = Session
    label_key = ""  # i18n key for the full display name
    short_label_key = ""  # i18n key for the compact table name
    accent = ""  # theme role: "secondary", "accent" or "primary"
    tier = "experimental"  # verified | best-effort | experimental | unsupported
    verified_versions: tuple[str, ...] = ()
    capabilities: Capabilities | None = None
    process_names: tuple[str, ...] = ()  # executable prefixes worth reading; empty means the shared default

    def home(self) -> Path:
        raise NotImplementedError

    def available(self) -> bool:
        return self.home().exists()

    def ref_for(self, locator: object) -> SessionRef:
        """A stable reference for a source locator; file providers use the default."""
        return SessionRef(self.name, ref_key(self.name, locator), locator)

    def discover(self, since: float) -> Iterable[SessionRef]:
        """Session references touched after `since`; wraps `history` until a provider has a richer source."""
        for locator in self.history(since):
            yield self.ref_for(locator)

    def open(self, ref: SessionRef) -> SessionReader:
        return FileReader(ref, self.session_cls(ref.locator))

    def procs(self, snap: list[Proc]) -> list[ProcInfo]:
        """Agent processes in the snapshot that own a session."""
        return []

    def service_running(self, snap: list[Proc]) -> bool | None:
        """Whether a shared service this provider depends on runs, from the process snapshot. None: not applicable."""
        return None

    def match(self, pi: ProcInfo, claimed: set[Path], peers: Iterable[ProcInfo] = ()) -> Path | None:
        """The session file a live process is writing to. `peers` are the provider's other live processes."""
        return None

    def match_ref(self, pi: ProcInfo, claimed: set[Path], peers: Iterable[ProcInfo] = ()) -> SessionRef | None:
        locator = self.match(pi, claimed, peers)
        return self.ref_for(locator) if locator is not None else None

    def history(self, since: float) -> Iterable[Path]:
        """Session files touched after `since` (epoch seconds)."""
        return []

    def resume(self, run: Run) -> dict | None:
        """The argv and cwd that resume this run, already validated for rendering."""
        return None

    def resume_cmd(self, run: Run, shell: str | None = None) -> str | None:
        caps = self.capabilities
        if caps is None or not caps.resume:
            return None
        spec = self.resume(run)
        return render_resume(spec, shell) if spec else None

    def title(self, session: Session) -> str | None:
        """A name for the session kept outside its file (thread index, rename log...)."""
        return None

    def origin_kind(self, session: Session) -> str:
        """The normalized origin for a session; providers override only to map their raw origin values."""
        return session.origin_kind

    def usage_basis(self) -> str:
        """How this provider counts the cumulative token total."""
        return self.session_cls.usage_basis

    def access_kind(self, raw: str | None) -> str | None:
        """Normalize a raw sandbox or permission mode using the session class map."""
        return self.session_cls.normalize_access(raw)

    def context_window(self, model: str | None) -> int | None:
        """A context window assumed from the model name when the transcript does not report one."""
        return None

    def run_meta(self, session: Session | None, proc: ProcInfo | None = None) -> dict:
        """The provider-neutral metadata the UI consumes instead of branching on the provider name."""
        meta = {
            "label_key": self.label_key,
            "short_label_key": self.short_label_key,
            "label": self.label,
            "accent": self.accent,
            "usage_basis": self.usage_basis(),
        }
        if session is not None:
            meta["origin_kind"] = self.origin_kind(session)
        raw = (session.sandbox if session is not None else None) or (proc.sandbox if proc is not None else None)
        access = self.access_kind(raw)
        if access:
            meta["access_kind"] = access
        model = (session.model if session is not None else None) or (proc.model if proc is not None else None)
        window = self.context_window(model)
        if window:
            meta["context_window"] = window
        return meta

    def quota(self, sessions: Iterable[Session]) -> dict | None:
        """Return normalized usage metadata: reporting is windows, limit-only, or none.

        Windows carry id, name, minutes, scope, used_percent, resets_at, as_of, and source. Limits carry
        window, name, scope, reached_at, resets_at, reset_text, and source.
        """
        return None
