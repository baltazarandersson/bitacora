"""What every agent provider implements."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from bitacora.model import ProcInfo, Run, Session
from bitacora.procs import Proc


class Provider:
    name = ""  # stable id used in config and cache keys
    label = ""  # shown in the UI
    marker = ""  # substring that identifies the agent in a parent shell's command line
    session_cls: type[Session] = Session

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

    def resume_cmd(self, run: Run) -> str | None:
        return None

    def title(self, session: Session) -> str | None:
        """A name for the session kept outside its file (thread index, rename log...)."""
        return None

    def quota(self, sessions: Iterable[Session]) -> dict | None:
        """Return normalized usage metadata: reporting is windows, limit-only, or none.

        Windows carry id, name, minutes, scope, used_percent, resets_at, as_of, and source. Limits carry
        window, name, scope, reached_at, resets_at, reset_text, and source.
        """
        return None
