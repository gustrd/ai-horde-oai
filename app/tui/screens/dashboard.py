"""Dashboard (home) screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from app.tui.widgets.kudos_bar import KudosBar


class DashboardScreen(Screen):
    """Main dashboard showing server status and stats."""

    TITLE = "ai-horde-oai"
    BINDINGS = [
        ("f1", "push_screen('config')", "Config"),
        ("f2", "push_screen('models')", "Models"),
        ("f3", "push_screen('chat')", "Test Chat"),
        ("f4", "push_screen('logs')", "Logs"),
        ("f5", "push_screen('history')", "History"),
        ("q", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    DashboardScreen #status-panel {
        border: round $accent;
        padding: 1 2;
        margin: 1;
        height: auto;
    }
    DashboardScreen .stat-row {
        height: 1;
        margin-bottom: 1;
    }
    DashboardScreen #kudos-bar {
        dock: bottom;
    }
    """

    models_count: reactive[int] = reactive(0)
    total_models: reactive[int] = reactive(0)
    request_count: reactive[int] = reactive(0)

    def compose(self) -> ComposeResult:
        yield Header()
        with Static(id="status-panel"):
            yield Label("", id="server-status", classes="stat-row")
            yield Label("", id="api-key-status", classes="stat-row")
            yield Label("", id="model-stats", classes="stat-row")
            yield Label("", id="request-stats", classes="stat-row")
        yield KudosBar(id="kudos-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        config = self.app.config
        self.query_one("#server-status", Label).update(
            f"Server: ● Running on {config.host}:{config.port}"
        )
        masked = f"****{config.horde_api_key[-4:]}" if len(config.horde_api_key) > 4 else "****"
        self.query_one("#api-key-status", Label).update(f"API Key: {masked}")
        self.query_one("#model-stats", Label).update(
            f"Models loaded: {self.models_count} (filtered from {self.total_models})"
        )
        self.query_one("#request-stats", Label).update(
            f"Requests this session: {self.request_count}"
        )

    def watch_models_count(self, _: int) -> None:
        self._refresh_labels()

    def watch_total_models(self, _: int) -> None:
        self._refresh_labels()

    def watch_request_count(self, _: int) -> None:
        self._refresh_labels()

    def set_kudos(self, balance: int) -> None:
        self.query_one(KudosBar).balance = balance

    def increment_requests(self) -> None:
        self.request_count += 1
