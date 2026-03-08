"""Welcome / first-run API key setup screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static


class WelcomeScreen(Screen):
    """First-run screen for setting up the Horde API key."""

    TITLE = "Welcome to ai-horde-oai"

    DEFAULT_CSS = """
    WelcomeScreen {
        align: center middle;
    }
    WelcomeScreen #panel {
        width: 60;
        height: auto;
        border: round $accent;
        padding: 2 4;
    }
    WelcomeScreen #title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    WelcomeScreen #subtitle {
        text-align: center;
        color: $text-muted;
        margin-bottom: 2;
    }
    WelcomeScreen #api-key-input {
        margin-bottom: 1;
    }
    WelcomeScreen #button-row {
        layout: horizontal;
        height: 3;
        margin-top: 1;
    }
    WelcomeScreen #status-label {
        margin-top: 1;
        text-align: center;
    }
    """

    def compose(self) -> ComposeResult:
        with Static(id="panel"):
            yield Label("Welcome to ai-horde-oai", id="title")
            yield Label(
                "Enter your AI Horde API key to get started.\nGet one at: https://aihorde.net/register",
                id="subtitle",
            )
            yield Input(
                placeholder="API Key",
                password=True,
                id="api-key-input",
            )
            with Static(id="button-row"):
                yield Button("Validate & Save", id="validate-btn", variant="primary")
                yield Button("Use Anonymous (0000)", id="anon-btn", variant="default")
            yield Label("", id="status-label")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "anon-btn":
            self.app.post_message(self.ApiKeyChosen(api_key="0000000000"))
        elif event.button.id == "validate-btn":
            key = self.query_one("#api-key-input", Input).value.strip()
            if not key:
                self._set_status("Please enter an API key.", error=True)
                return
            self.app.post_message(self.ApiKeyChosen(api_key=key))

    def _set_status(self, text: str, error: bool = False) -> None:
        label = self.query_one("#status-label", Label)
        label.update(text)
        if error:
            label.add_class("error")
        else:
            label.remove_class("error")

    def set_validation_result(self, username: str, kudos: int) -> None:
        self._set_status(f"✓ Authenticated as {username}  (kudos: {kudos:,})")

    def set_validation_error(self, message: str) -> None:
        self._set_status(f"✗ {message}", error=True)

    class ApiKeyChosen(Message):
        def __init__(self, api_key: str) -> None:
            super().__init__()
            self.api_key = api_key
