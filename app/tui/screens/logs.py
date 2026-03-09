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
            f"  Tokens in:  {e.input_tokens:,}  (~estimated)",
            f"  Tokens out: {e.output_tokens:,}  (~estimated)",
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
    LogsScreen #active-label {
        height: 1;
        padding: 0 1;
        color: $warning;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="log-table", cursor_type="row")
        yield Label("No requests yet.", id="info", markup=False)
        yield Label("", id="active-label", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Time", "Status", "Dur", "Model", "Kudos", "Tokens in→out", "Output preview")
        self._rebuild_table()

    def on_screen_resume(self) -> None:
        """Sync any entries added while this screen was not active."""
        if self.query_one(DataTable).row_count != len(self.app.request_log):
            self._rebuild_table()

    def _rebuild_table(self) -> None:
        """Rebuild the table newest-first from request_log."""
        table = self.query_one(DataTable)
        table.clear()
        for entry in reversed(self.app.request_log):
            preview = (entry.response_text or entry.error or "").replace("\n", " ")[:60]
            table.add_row(
                entry.timestamp.strftime("%H:%M:%S"),
                str(entry.status),
                f"{entry.duration:.1f}s",
                entry.model,
                f"{entry.kudos:.1f}" if entry.kudos else "—",
                f"{entry.input_tokens}→{entry.output_tokens}" if (entry.input_tokens or entry.output_tokens) else "—",
                preview,
            )
        count = table.row_count
        if count:
            self.query_one("#info", Label).update(
                f"{count} request{'s' if count != 1 else ''} — Enter to view detail"
            )

    def add_entry(self, entry: RequestLogEntry) -> None:
        self._rebuild_table()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        log = self.app.request_log
        # Table is newest-first, so row 0 = last log entry
        idx = len(log) - 1 - event.cursor_row
        if 0 <= idx < len(log):
            self.app.push_screen(LogDetailModal(log[idx]))

    def update_active(self, active: list[dict]) -> None:
        label = self.query_one("#active-label", Label)
        if not active:
            label.update("")
        else:
            parts = [f"● {r['method']} {r['path']}" for r in active]
            label.update("  " + " | ".join(parts))

    def action_clear(self) -> None:
        self.app.request_log.clear()
        self.query_one(DataTable).clear()
        self.query_one("#info", Label).update("No requests yet.")
