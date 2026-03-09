"""Request log viewer screen (unified log & history)."""
from __future__ import annotations

import textwrap

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from app.log_store import RequestLogEntry


class LogDetailModal(ModalScreen):
    """Full detail view for a single request log entry."""

    BINDINGS = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]

    DEFAULT_CSS = """
    LogDetailModal {
        align: center middle;
    }
    LogDetailModal #detail-container {
        width: 90;
        height: 85%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    LogDetailModal #detail-title {
        text-style: bold;
        color: $accent;
        height: 1;
        margin-bottom: 1;
    }
    LogDetailModal #detail-hint {
        height: 1;
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    def __init__(self, entry: RequestLogEntry, **kwargs):
        super().__init__(**kwargs)
        self._entry = entry

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="detail-container"):
            yield Label("Request Detail", id="detail-title")
            yield Static(self._build_text(), id="detail-content", markup=False)
            yield Label("Esc or q to close", id="detail-hint")

    def _build_text(self) -> str:
        e = self._entry
        lines = [
            "── Request ─────────────────────────────────",
            f"  Time:       {e.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Method:     {e.method}",
            f"  Path:       {e.path}",
            f"  Status:     {e.status}",
            f"  Duration:   {e.duration:.2f}s",
            "",
            "── Horde ───────────────────────────────────",
            f"  Model:      {e.model or '—'}",
            f"  Real model: {e.real_model or e.model or '—'}",
            f"  Worker:     {e.worker or '—'}",
            f"  Worker ID:  {e.worker_id or '—'}",
            f"  Kudos:      {e.kudos:.2f}",
        ]

        if e.error:
            lines += [
                "",
                "── Error ───────────────────────────────────",
                f"  {e.error}",
            ]

        if e.prompt:
            lines += ["", "── Prompt ──────────────────────────────────"]
            for chunk in textwrap.wrap(e.prompt, 74) if e.prompt.strip() else [e.prompt]:
                lines.append(f"  {chunk}")

        if e.messages:
            lines += ["", "── Messages ────────────────────────────────"]
            for msg in e.messages:
                role = msg.get("role", "?").upper()
                content = str(msg.get("content", ""))
                lines.append(f"  [{role}]")
                for chunk in textwrap.wrap(content, 74) if content.strip() else [content]:
                    lines.append(f"    {chunk}")

        if e.response_text:
            lines += ["", "── Response ────────────────────────────────"]
            for chunk in textwrap.wrap(e.response_text, 76):
                lines.append(f"  {chunk}")

        return "\n".join(lines)


class LogsScreen(Screen):
    """Live request log viewer — shows all API requests from all sources."""

    TITLE = "Request Log"
    BINDINGS = [
        ("d", "switch_screen('dashboard')", "Dash"),
        ("s", "switch_screen('config')", "Set"),
        ("m", "switch_screen('models')", "Mod"),
        ("c", "switch_screen('chat')", "Chat"),
        ("l", "switch_screen('logs')", "Log"),
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
        yield DataTable(id="log-table", cursor_type="row")
        yield Label("No requests yet.", id="info", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Time", "Method", "Path", "Status", "Dur", "Model", "Worker", "Kudos")
        for entry in self.app.request_log:
            self._add_row(entry)

    def on_screen_resume(self) -> None:
        """Sync any entries added while this screen was not active."""
        table = self.query_one(DataTable)
        if table.row_count < len(self.app.request_log):
            for entry in self.app.request_log[table.row_count:]:
                self._add_row(entry)

    def _add_row(self, entry: RequestLogEntry) -> None:
        table = self.query_one(DataTable)
        table.add_row(
            entry.timestamp.strftime("%H:%M:%S"),
            entry.method,
            entry.path,
            str(entry.status),
            f"{entry.duration:.1f}s",
            entry.model,
            entry.worker,
            f"{entry.kudos:.1f}" if entry.kudos else "—",
        )
        count = table.row_count
        self.query_one("#info", Label).update(
            f"{count} request{'s' if count != 1 else ''} — Enter to view detail"
        )

    def add_entry(self, entry: RequestLogEntry) -> None:
        self._add_row(entry)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self.app.request_log):
            self.app.push_screen(LogDetailModal(self.app.request_log[idx]))

    def action_clear(self) -> None:
        self.app.request_log.clear()
        self.query_one(DataTable).clear()
        self.query_one("#info", Label).update("Log cleared.")
