"""Request log viewer screen (unified log & history)."""
from __future__ import annotations

import textwrap

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Label, Static

from app.log_store import RequestLogEntry, save_entries


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
            f"  Job ID:     {e.job_id or '—'}",
            f"  Kudos:      {e.kudos:.2f}",
            f"  Tokens in:  {e.input_tokens:,}  (~estimated)",
            f"  Tokens out: {e.output_tokens:,}  (~estimated)",
        ]
        if e.reasoning_tokens:
            lines.append(f"  Tokens (reasoning): {e.reasoning_tokens:,}  (~estimated)")

        if e.tool_info:
            lines += [
                "",
                "── Tool Call ───────────────────────────────",
                f"  {e.tool_info}",
            ]

        if e.error:
            lines += [
                "",
                "── Error ───────────────────────────────────",
                f"  {e.error}",
            ]

        if e.reasoning_content:
            lines += ["", "── Reasoning ───────────────────────────────"]
            for chunk in textwrap.wrap(e.reasoning_content, 76):
                lines.append(f"  {chunk}")

        if e.response_text:
            lines += ["", "── Response ────────────────────────────────"]
            for chunk in textwrap.wrap(e.response_text, 76):
                lines.append(f"  {chunk}")

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

        return "\n".join(lines)


class QueueDetailModal(ModalScreen):
    """Detail view for active in-flight requests, with cancel option."""

    BINDINGS = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]

    DEFAULT_CSS = """
    QueueDetailModal {
        align: center middle;
    }
    QueueDetailModal #queue-container {
        width: 72;
        height: auto;
        max-height: 85%;
        border: round $warning;
        background: $surface;
        padding: 1 2;
    }
    QueueDetailModal #queue-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }
    QueueDetailModal .req-info {
        margin-bottom: 0;
        color: $text;
    }
    QueueDetailModal .cancel-btn {
        margin-top: 1;
        margin-bottom: 1;
        width: 16;
    }
    QueueDetailModal #close-btn {
        margin-top: 1;
        width: 100%;
    }
    """

    def __init__(self, active: list[dict], **kwargs):
        super().__init__(**kwargs)
        self._active = list(active)  # snapshot at open time

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="queue-container"):
            n = len(self._active)
            yield Label(
                f"Active Requests ({n})" if n != 1 else "Active Request (1)",
                id="queue-title",
            )
            if not self._active:
                yield Static("No active requests.", markup=False)
            for i, req in enumerate(self._active):
                yield Static(self._req_text(i, req), markup=False, classes="req-info")
                can_cancel = bool(req.get("cancel_fn") and req.get("job_id"))
                yield Button(
                    "Cancel Job" if can_cancel else "Starting…",
                    id=f"cancel-{i}",
                    variant="error" if can_cancel else "default",
                    classes="cancel-btn",
                    disabled=not can_cancel,
                )
            yield Button("Close", id="close-btn", variant="default")

    def _req_text(self, idx: int, req: dict) -> str:
        lines = [f"── Request {idx + 1} ─────────────────────────────────"]
        alias = req.get("alias", "")
        model = req.get("model", "")
        if alias and alias != model:
            lines.append(f"  Alias:      {alias}")
            lines.append(f"  Model:      {model or '(resolving…)'}")
        else:
            lines.append(f"  Model:      {model or alias or '(resolving…)'}")
        lines.append(f"  Path:       {req.get('method', '?')} {req.get('path', '?')}")
        max_tokens = req.get("max_tokens")
        if max_tokens:
            lines.append(f"  Max tokens: {max_tokens}")
        q = req.get("queue_pos")
        eta = req.get("eta")
        if q is not None:
            line = f"  Queue pos:  {q}"
            if eta is not None:
                line += f"   ETA: {eta}s"
            lines.append(line)
        else:
            lines.append("  Status:     pending / submitting")
        job_id = req.get("job_id")
        if job_id:
            lines.append(f"  Job ID:     {job_id}")
        messages = req.get("messages")
        if messages:
            lines.append("")
            lines.append("── Messages ────────────────────────────────")
            for msg in messages:
                role = msg.get("role", "?").upper()
                content = str(msg.get("content", ""))
                lines.append(f"  [{role}]")
                for chunk in (textwrap.wrap(content, 64) if content.strip() else [content]):
                    lines.append(f"    {chunk}")
        return "\n".join(lines)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "close-btn":
            self.dismiss()
            return
        if btn_id.startswith("cancel-"):
            idx = int(btn_id.split("-", 1)[1])
            if 0 <= idx < len(self._active):
                req = self._active[idx]
                cancel_fn = req.get("cancel_fn")
                job_id = req.get("job_id")
                if cancel_fn and job_id:
                    self.app.run_worker(cancel_fn(job_id), exclusive=False)
                # Optimistically remove from the app's active list for visual feedback
                try:
                    self.app.active_requests.remove(req)
                except (ValueError, AttributeError):
                    pass
            self.dismiss()


class ActiveQueueBar(Static):
    """Clickable status bar that shows in-flight requests and opens detail modal on click."""

    DEFAULT_CSS = """
    ActiveQueueBar {
        height: 1;
        padding: 0 1;
        color: $warning;
    }
    ActiveQueueBar:hover {
        color: $accent;
        background: $boost;
    }
    """

    def on_click(self) -> None:
        active = getattr(self.app, "active_requests", [])
        if active:
            self.app.push_screen(QueueDetailModal(active))


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
        ("space", "toggle_checked", "Check"),
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
        yield ActiveQueueBar("", id="active-queue-bar", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(" ", "Time", "Status", "Dur", "Model", "Kudos", "Tokens in>out", "Output preview")
        self._rebuild_table()

    def on_screen_resume(self) -> None:
        """Sync any entries added while this screen was not active."""
        if self.query_one(DataTable).row_count != len(self.app.request_log):
            self._rebuild_table()

    def _rebuild_table(self) -> None:
        """Rebuild the table newest-first from request_log."""
        table = self.query_one(DataTable)
        log = self.app.request_log
        table.clear()
        for entry in reversed(log):
            model_display = entry.model or ""
            if entry.real_model and entry.real_model != entry.model:
                model_display = f"{entry.model} ({entry.real_model})"
            
            preview = (entry.response_text or entry.error or "").replace("\n", " ")[:60]
            table.add_row(
                "*" if entry.checked else " ",
                entry.timestamp.strftime("%H:%M:%S"),
                str(entry.status),
                f"{entry.duration:.1f}s",
                model_display[:30],
                f"{entry.kudos:.1f}" if entry.kudos else "—",
                f"{entry.input_tokens}>{entry.output_tokens}" if (entry.input_tokens or entry.output_tokens) else "—",
                preview,
            )
        count = table.row_count
        if count:
            self.query_one("#info", Label).update(
                f"{count} request{'s' if count != 1 else ''} — Enter to view detail, Space to check"
            )

    def add_entry(self, entry: RequestLogEntry) -> None:
        self._rebuild_table()

    def _row_to_log_idx(self, row: int) -> int:
        """Convert table row (newest-first) to request_log index (oldest-first)."""
        return len(self.app.request_log) - 1 - row

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        log = self.app.request_log
        idx = self._row_to_log_idx(event.cursor_row)
        if 0 <= idx < len(log):
            self.app.push_screen(LogDetailModal(log[idx]))

    def action_toggle_checked(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return
        log = self.app.request_log
        row = table.cursor_row
        idx = self._row_to_log_idx(row)
        if 0 <= idx < len(log):
            log[idx].checked = not log[idx].checked
            table.update_cell_at(Coordinate(row, 0), "*" if log[idx].checked else " ")
            save_entries(log)

    def update_active(self, active: list[dict]) -> None:
        bar = self.query_one("#active-queue-bar", ActiveQueueBar)
        if not active:
            bar.update("")
            return
        parts = []
        for r in active:
            queue_pos = r.get("queue_pos")
            eta = r.get("eta")
            if queue_pos is not None:
                line = f"● q={queue_pos}"
                if eta is not None:
                    line += f" eta={eta}s"
            else:
                line = "● pending"
            parts.append(line)
        bar.update("  " + "   ".join(parts))

    def action_clear(self) -> None:
        self.app.request_log.clear()
        self.query_one(DataTable).clear()
        self.query_one("#info", Label).update("No requests yet.")
