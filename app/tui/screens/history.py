"""Chat history browser screen."""
from __future__ import annotations

import json
from pathlib import Path

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

HISTORY_DIR = Path.home() / ".ai-horde-oai" / "history"


def _load_sessions() -> list[dict]:
    if not HISTORY_DIR.exists():
        return []
    sessions = []
    for f in sorted(HISTORY_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            data["_path"] = str(f)
            sessions.append(data)
        except Exception:
            continue
    return sessions


class HistoryScreen(Screen):
    """Browse past chat sessions."""

    TITLE = "Chat History"
    BINDINGS = [
        ("d", "switch_screen('dashboard')", "Dash"),
        ("s", "switch_screen('config')", "Set"),
        ("m", "switch_screen('models')", "Mod"),
        ("c", "switch_screen('chat')", "Chat"),
        ("l", "switch_screen('logs')", "Log"),
        ("h", "switch_screen('history')", "Hist"),
        ("q", "quit", "Quit"),
        ("x", "delete_selected", "Del"),
    ]

    DEFAULT_CSS = """
    HistoryScreen DataTable {
        height: 1fr;
    }
    HistoryScreen #info {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sessions: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="history-table")
        yield Label("", id="info", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Date", "Model", "Messages", "Kudos")
        self._load()

    def _load(self) -> None:
        self._sessions = _load_sessions()
        table = self.query_one(DataTable)
        table.clear()
        for s in self._sessions:
            table.add_row(
                s.get("date", "?"),
                s.get("model", "?"),
                str(s.get("message_count", 0)),
                str(s.get("kudos_spent", 0)),
            )
        count = len(self._sessions)
        self.query_one("#info", Label).update(
            f"{count} session{'s' if count != 1 else ''} in history"
        )

    def action_delete_selected(self) -> None:
        table = self.query_one(DataTable)
        row_key = table.cursor_row
        if row_key is None or row_key >= len(self._sessions):
            return
        session = self._sessions[row_key]
        path = Path(session["_path"])
        if path.exists():
            path.unlink()
        self._load()
