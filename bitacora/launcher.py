"""Environment hygiene when opening documents from a frozen application."""
from __future__ import annotations

import os


def clean_env() -> dict[str, str]:
    """Our environment without PyInstaller's bookkeeping.

    A one-file build unpacks itself to a temporary folder and tells its children where through these
    variables. The program we start must unpack its own: this one deletes its folder when it exits.
    """
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_PYI", "_MEI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env
