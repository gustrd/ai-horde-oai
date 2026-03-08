"""Main Textual application for ai-horde-oai TUI."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Label

from app.config import Settings, load_config, save_config
from app.horde.client import HordeClient
from app.tui.screens.chat import ChatScreen
from app.tui.screens.config import ConfigScreen
from app.tui.screens.dashboard import DashboardScreen
from app.tui.screens.history import HistoryScreen
from app.tui.screens.logs import LogsScreen, RequestLogEntry
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
        "dashboard": DashboardScreen,
        "config": ConfigScreen,
        "models": ModelsScreen,
        "chat": ChatScreen,
        "logs": LogsScreen,
        "history": HistoryScreen,
    }

    def __init__(self, config: Settings | None = None, **kwargs):
        super().__init__(**kwargs)
        self.config: Settings = config or load_config()
        self.horde: HordeClient | None = None
        self.request_log: list[RequestLogEntry] = []

    def compose(self) -> ComposeResult:
        # Placeholder; screens handle their own layouts
        yield Label("")

    async def on_mount(self) -> None:
        self.horde = HordeClient(
            base_url=self.config.horde_api_url,
            api_key=self.config.horde_api_key,
            client_agent=self.config.client_agent,
        )

        # Show welcome if this is the first run (default anon key)
        if self.config.horde_api_key == "0000000000":
            await self.push_screen(WelcomeScreen())
        else:
            await self.push_screen(DashboardScreen())

    async def on_unmount(self) -> None:
        if self.horde:
            await self.horde.close()

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
        await self.switch_screen(DashboardScreen())


def cli() -> None:
    app = HordeApp()
    app.run()


if __name__ == "__main__":
    cli()
