"""Dashboard (home) screen."""
from __future__ import annotations

import asyncio
from datetime import datetime

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from app.tui.widgets.kudos_bar import KudosBar


class DashboardScreen(Screen):
    """Main dashboard showing server status and stats."""

    TITLE = "ai-horde-oai"
    BINDINGS = [
        ("d", "switch_screen('dashboard')", "Dash"),
        ("s", "switch_screen('config')", "Set"),
        ("m", "switch_screen('models')", "Mod"),
        ("c", "switch_screen('chat')", "Chat"),
        ("l", "switch_screen('logs')", "Log"),
        ("q", "quit", "Quit"),
        ("r", "refresh", "Ref"),
    ]

    DEFAULT_CSS = """
    DashboardScreen #status-panel {
        border: round $accent;
        padding: 1 2;
        margin: 1 1 0 1;
        height: auto;
    }
    DashboardScreen #stats-panel {
        border: round $primary;
        padding: 1 2;
        margin: 0 1 1 1;
        height: auto;
    }
    DashboardScreen .panel-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    DashboardScreen .stat-row {
        height: 1;
        margin-bottom: 1;
    }
    DashboardScreen .stat-row-last {
        height: 1;
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
            yield Label("Connection", classes="panel-title", markup=False)
            yield Label("", id="server-status", classes="stat-row", markup=False)
            yield Label("", id="api-key-status", classes="stat-row", markup=False)
            yield Label("", id="horde-status", classes="stat-row", markup=False)
            yield Label("", id="model-stats", classes="stat-row-last", markup=False)
        with Static(id="stats-panel"):
            yield Label("Activity", classes="panel-title", markup=False)
            yield Label("", id="request-stats", classes="stat-row", markup=False)
            yield Label("", id="session-stats", classes="stat-row", markup=False)
            yield Label("", id="last-request", classes="stat-row-last", markup=False)
        yield KudosBar(id="kudos-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_labels()
        self.run_worker(self._load_horde_stats(), exclusive=True, name="dashboard-stats")

    def on_screen_resume(self) -> None:
        self._refresh_labels()
        self.run_worker(self._load_horde_stats(), exclusive=True, name="dashboard-stats")

    def _refresh_labels(self) -> None:
        config = self.app.config
        self.query_one("#server-status", Label).update(
            f"  Server  : Running on {config.host}:{config.port}"
        )
        masked = f"****{config.horde_api_key[-4:]}" if len(config.horde_api_key) > 4 else "****"
        self.query_one("#api-key-status", Label).update(f"  API key : {masked}")
        self.query_one("#model-stats", Label).update(
            f"  Models  : {self.models_count} selected / {self.total_models} total"
        )
        log = getattr(self.app, "request_log", [])
        session_count = len(log)
        self.query_one("#request-stats", Label).update(
            f"  Requests: {session_count} this session"
        )
        if log:
            last = log[-1]
            last_time = last.timestamp.strftime("%H:%M:%S")
            last_model = last.model or "—"
            last_dur = f"{last.duration:.1f}s"
            self.query_one("#last-request", Label).update(
                f"  Last    : {last_time}  {last_model}  ({last_dur})"
            )
        else:
            self.query_one("#last-request", Label).update("  Last    : —")

    async def _load_horde_stats(self) -> None:
        horde = getattr(self.app, "horde", None)
        if horde is None:
            self.query_one("#horde-status", Label).update("  Horde   : not connected")
            self.query_one("#session-stats", Label).update("  Kudos   : —")
            return

        # Fetch models, workers, and user info concurrently
        models_result = None
        workers_result = None
        user_result = None
        models_err = None
        user_err = None

        try:
            models_task = asyncio.create_task(horde.get_models(type="text"))
            workers_task = asyncio.create_task(horde.get_text_workers())
            user_task = asyncio.create_task(horde.get_user())
            models_result, workers_result, user_result = await asyncio.gather(
                models_task, workers_task, user_task, return_exceptions=True
            )
        except Exception as e:
            models_err = e

        # Handle errors
        if isinstance(models_result, Exception):
            models_err = models_result
            models_result = None
        if isinstance(workers_result, Exception):
            workers_result = None
        if isinstance(user_result, Exception):
            user_err = user_result
            user_result = None

        if models_result is not None:
            # Enrich models with worker max_context_length / max_length (same as ModelsScreen)
            if workers_result:
                ctx_map: dict[str, int] = {}
                len_map: dict[str, int] = {}
                for w in workers_result:
                    if not w.get("online"):
                        continue
                    max_ctx = w.get("max_context_length", 0)
                    max_len = w.get("max_length", 0)
                    for model_name in w.get("models", []):
                        if max_ctx > ctx_map.get(model_name, 0):
                            ctx_map[model_name] = max_ctx
                        if max_len > len_map.get(model_name, 0):
                            len_map[model_name] = max_len
                models_result = [
                    m.model_copy(update={
                        "max_context_length": ctx_map.get(m.name, m.max_context_length),
                        "max_length": len_map.get(m.name, m.max_length),
                    })
                    for m in models_result
                ]

            total = len(models_result)
            from app.horde.filters import filter_models
            cfg = self.app.config
            shown_models = filter_models(
                models_result,
                whitelist=cfg.model_whitelist or None,
                blocklist=cfg.model_blocklist or None,
                min_context=cfg.model_min_context,
                min_max_length=cfg.model_min_max_length,
            )
            shown = len(shown_models)
            self.app.model_count = shown
            self.app.model_total = total
            self.models_count = shown
            self.total_models = total
            self.query_one("#horde-status", Label).update("  Horde   : connected")
        else:
            err_msg = str(models_err)[:40] if models_err else "unavailable"
            self.query_one("#horde-status", Label).update(f"  Horde   : error — {err_msg}")

        if user_result is not None:
            kudos = int(getattr(user_result, "kudos", 0))
            username = getattr(user_result, "username", "")
            self.query_one("#session-stats", Label).update(
                f"  Kudos   : {kudos:,}  ({username})"
            )
            self.query_one(KudosBar).balance = kudos
        else:
            self.query_one("#session-stats", Label).update("  Kudos   : —")

        # Refresh other labels now that model counts are updated
        self._refresh_labels()

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

    def action_refresh(self) -> None:
        self.query_one("#horde-status", Label).update("  Horde   : loading…")
        self.query_one("#model-stats", Label).update("  Models  : loading…")
        self.run_worker(self._load_horde_stats(), exclusive=True, name="dashboard-stats")
