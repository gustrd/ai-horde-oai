"""Main Textual application for ai-horde-oai TUI."""
from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Label

from app.config import Settings, load_config, save_config
from app.horde.client import HordeClient
from app.log_store import RequestLogEntry
from app.tui.screens.chat import ChatScreen
from app.tui.screens.config import ConfigScreen
from app.tui.screens.dashboard import DashboardScreen
from app.tui.screens.logs import LogsScreen
from app.tui.screens.models import ModelsScreen
from app.tui.screens.welcome import WelcomeScreen


class HordeApp(App):
    """ai-horde-oai terminal user interface."""

    CSS = """
    Screen {
        background: $surface;
    }
    .section-header {
        color: $accent;
        text-style: bold;
        margin-top: 1;
        margin-bottom: 1;
    }
    .error {
        color: $error;
    }
    """

    SCREENS = {
        "welcome": WelcomeScreen,
        "dashboard": DashboardScreen,
        "config": ConfigScreen,
        "models": ModelsScreen,
        "chat": ChatScreen,
        "logs": LogsScreen,
    }

    BINDINGS = [
        ("d", "switch_screen('dashboard')", "Dash"),
        ("s", "switch_screen('config')", "Set"),
        ("m", "switch_screen('models')", "Mod"),
        ("c", "switch_screen('chat')", "Chat"),
        ("l", "switch_screen('logs')", "Log"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: Settings | None = None, start_server: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.config: Settings = config or load_config()
        self.horde: HordeClient | None = None
        self.request_log: list[RequestLogEntry] = []
        self.selected_model: str | None = None
        # Model counts updated by ModelsScreen after each load
        self.model_count: int = 0
        self.model_total: int = 0
        self._start_server = start_server
        self._uv_server = None
        self._server_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        # Placeholder; screens handle their own layouts
        yield Label("")

    async def on_mount(self) -> None:
        self.horde = HordeClient(
            base_url=self.config.horde_api_url,
            api_key=self.config.horde_api_key,
            client_agent=self.config.client_agent,
        )

        if self._start_server:
            await self._launch_server()

        # Show welcome if this is the first run (default anon key)
        if self.config.horde_api_key == "0000000000":
            self.push_screen("welcome")
        else:
            self.push_screen("dashboard")

    async def _launch_server(self) -> None:
        """Start the FastAPI server as an in-process asyncio task."""
        import uvicorn

        from app.main import create_app

        fastapi_app = create_app(self.config)
        # Share the TUI's request_log and notify callback with the FastAPI app
        fastapi_app.state.request_log = self.request_log
        fastapi_app.state.log_callback = self._notify_logs

        uv_config = uvicorn.Config(
            app=fastapi_app,
            host=self.config.host,
            port=self.config.port,
            log_config=None,
            install_signal_handlers=False,
        )
        self._uv_server = uvicorn.Server(uv_config)
        self._server_task = asyncio.create_task(self._uv_server.serve())

    def _notify_logs(self, entry: RequestLogEntry) -> None:
        """Called by FastAPI middleware when a new log entry is created."""
        for screen in self.screen_stack:
            if isinstance(screen, LogsScreen):
                screen.add_entry(entry)
                break

    async def on_unmount(self) -> None:
        if self.horde:
            await self.horde.close()
        if self._uv_server is not None:
            self._uv_server.should_exit = True
        if self._server_task is not None:
            try:
                await asyncio.wait_for(self._server_task, timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                pass

    # -----------------------------------------------------------------
    # Welcome screen events
    # -----------------------------------------------------------------

    async def on_welcome_screen_api_key_chosen(self, event: WelcomeScreen.ApiKeyChosen) -> None:
        key = event.api_key
        welcome = self.screen
        if key != "0000000000":
            # Validate key
            try:
                old_horde = self.horde
                test_client = HordeClient(
                    base_url=self.config.horde_api_url,
                    api_key=key,
                    client_agent=self.config.client_agent,
                )
                user = await test_client.get_user()
                await test_client.close()
                if old_horde:
                    await old_horde.close()
                if isinstance(welcome, WelcomeScreen):
                    welcome.set_validation_result(user.username, int(user.kudos))
            except Exception as e:
                if isinstance(welcome, WelcomeScreen):
                    welcome.set_validation_error(str(e))
                return

        # Save and proceed
        new_config = self.config.model_copy(update={"horde_api_key": key})
        self.config = new_config
        save_config(new_config)
        self.horde = HordeClient(
            base_url=new_config.horde_api_url,
            api_key=new_config.horde_api_key,
            client_agent=new_config.client_agent,
        )
        self.switch_screen("dashboard")


def cli() -> None:
    app = HordeApp(start_server=True)
    app.run()


if __name__ == "__main__":
    cli()
