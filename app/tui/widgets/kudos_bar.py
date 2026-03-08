"""Kudos balance widget."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label


class KudosBar(Widget):
    """Displays kudos balance and session usage."""

    DEFAULT_CSS = """
    KudosBar {
        height: 1;
        padding: 0 1;
    }
    KudosBar .low {
        color: $error;
    }
    """

    balance: reactive[int | None] = reactive(None)
    session_spent: reactive[int] = reactive(0)

    def compose(self) -> ComposeResult:
        yield Label("", id="kudos-label")

    def watch_balance(self, value: int | None) -> None:
        self._refresh_label()

    def watch_session_spent(self, value: int) -> None:
        self._refresh_label()

    def _refresh_label(self) -> None:
        label = self.query_one("#kudos-label", Label)
        bal = self.balance
        spent = self.session_spent
        if bal is None:
            label.update("Kudos: ...")
        else:
            text = f"Kudos: {bal:,}  (session: -{spent:,})"
            label.update(text)
            if bal < 100:
                label.add_class("low")
            else:
                label.remove_class("low")

    def add_spent(self, amount: int) -> None:
        self.session_spent += amount
