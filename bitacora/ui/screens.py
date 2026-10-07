"""Kill confirmation dialog."""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from bitacora.i18n import t
from bitacora.model import Run


class ConfirmKill(ModalScreen[bool]):
    BINDINGS = [Binding("y,s", "yes", show=False), Binding("n,escape", "no", show=False)]

    def __init__(self, run: Run):
        super().__init__()
        self.run_ = run

    def compose(self) -> ComposeResult:
        with Grid(id="dialog"):
            yield Label(t("kill_q", label=self.run_.label, pid=self.run_.proc.pid), id="question")
            yield Button(t("kill_yes"), variant="error", id="yes")
            yield Button(t("kill_no"), variant="primary", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed)
    def pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(ev.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)
