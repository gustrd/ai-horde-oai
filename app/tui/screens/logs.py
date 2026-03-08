"""Request log viewer screen."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label


@dataclass
class RequestLogEntry:
    timestamp: datetime
    method: str
    path: str
    status: int
    duration: float


class LogsScreen(Screen):
    """Live request log viewer."""

    TITLE = "Request Log"
    BINDINGS = [
        ("d", "switch_screen('dashboard')", "Dash"),
        ("s", "switch_screen('config')", "Set"),
        ("m", "switch_screen('models')", "Mod"),
        ("c", "switch_screen('chat')", "Chat"),
        ("l", "switch_screen('logs')", "Log"),
        ("h", "switch_screen('history')", "Hist"),
        ("q", "quit", "Quit"),
        ("x", "clear", "Clear"),
    ]

    DEFAULT_CSS = """
    LogsScreen DataTable {
        height: 1fr;
    }
    LogsScreen #info {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="log-table")
        yield Label("No requests yet.", id="info", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Time", "Method", "Path", "Status", "Duration")
        # Load existing entries from the app log
        for entry in self.app.request_log:
            self._add_row(entry)

    def _add_row(self, entry: RequestLogEntry) -> None:
        table = self.query_one(DataTable)
        table.add_row(
            entry.timestamp.strftime("%H:%M:%S"),
            entry.method,
            entry.path,
            str(entry.status),
            f"{entry.duration:.2f}s",
        )
        self.query_one("#info", Label).update(f"{table.row_count} requests logged")

    def add_entry(self, entry: RequestLogEntry) -> None:
        self._add_row(entry)

    def action_clear(self) -> None:
        self.app.request_log.clear()
        self.query_one(DataTable).clear()
        self.query_one("#info", Label).update("Log cleared.")
