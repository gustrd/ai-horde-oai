"""Configuration editor screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Switch


class ConfigScreen(Screen):
    """Interactive configuration editor."""

    TITLE = "Configuration"
    BINDINGS = [
        ("escape", "pop_screen", "Back"),
        ("ctrl+s", "save", "Save"),
    ]

    DEFAULT_CSS = """
    ConfigScreen #form {
        padding: 1 2;
        height: 1fr;
    }
    ConfigScreen .field-row {
        height: 3;
        margin-bottom: 1;
    }
    ConfigScreen .field-label {
        width: 25;
        padding-top: 1;
    }
    ConfigScreen .field-input {
        width: 1fr;
    }
    ConfigScreen #button-row {
        dock: bottom;
        height: 3;
        padding: 0 2;
    }
    ConfigScreen #status {
        dock: bottom;
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    ConfigScreen .section-header {
        margin-top: 1;
        color: $accent;
        text-style: bold;
    }
    """

    def _field_row(self, label: str, widget_id: str, **input_kwargs):
        """Helper to yield a label+input horizontal row."""
        return [
            Label(label, classes="field-label"),
            Input(id=widget_id, classes="field-input", **input_kwargs),
        ]

    def compose(self) -> ComposeResult:
        yield Header()
        with ScrollableContainer(id="form"):
            yield Label("─── API Settings ───────────────────", classes="section-header")
            with Horizontal(classes="field-row"):
                yield Label("Horde API Key:", classes="field-label")
                yield Input(id="field-api-key", password=True, classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("Horde API URL:", classes="field-label")
                yield Input(id="field-api-url", classes="field-input")

            yield Label("─── Server Settings ────────────────", classes="section-header")
            with Horizontal(classes="field-row"):
                yield Label("Host:", classes="field-label")
                yield Input(id="field-host", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("Port:", classes="field-label")
                yield Input(id="field-port", classes="field-input")

            yield Label("─── Model Filters ──────────────────", classes="section-header")
            with Horizontal(classes="field-row"):
                yield Label("Default Model:", classes="field-label")
                yield Input(id="field-default-model", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("Min Context Length:", classes="field-label")
                yield Input(id="field-min-context", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("Model Whitelist:", classes="field-label")
                yield Input(id="field-whitelist", placeholder="comma-separated", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("Model Blocklist:", classes="field-label")
                yield Input(id="field-blocklist", placeholder="comma-separated", classes="field-input")

            yield Label("─── Retry Settings ─────────────────", classes="section-header")
            with Horizontal(classes="field-row"):
                yield Label("Max Retries:", classes="field-label")
                yield Input(id="field-max-retries", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("Timeout (s):", classes="field-label")
                yield Input(id="field-timeout", classes="field-input")
            with Horizontal(classes="field-row"):
                yield Label("Broaden on Retry:", classes="field-label")
                yield Switch(id="field-broaden")

        with Horizontal(id="button-row"):
            yield Button("Save", id="save-btn", variant="primary")
            yield Button("Back", id="back-btn", variant="default")
        yield Label("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        cfg = self.app.config
        self.query_one("#field-api-key", Input).value = cfg.horde_api_key
        self.query_one("#field-api-url", Input).value = cfg.horde_api_url
        self.query_one("#field-host", Input).value = cfg.host
        self.query_one("#field-port", Input).value = str(cfg.port)
        self.query_one("#field-default-model", Input).value = cfg.default_model
        self.query_one("#field-min-context", Input).value = str(cfg.model_min_context)
        self.query_one("#field-whitelist", Input).value = ",".join(cfg.model_whitelist)
        self.query_one("#field-blocklist", Input).value = ",".join(cfg.model_blocklist)
        self.query_one("#field-max-retries", Input).value = str(cfg.retry.max_retries)
        self.query_one("#field-timeout", Input).value = str(cfg.retry.timeout_seconds)
        self.query_one("#field-broaden", Switch).value = cfg.retry.broaden_on_retry

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.action_save()
        elif event.button.id == "back-btn":
            self.action_pop_screen()

    def action_save(self) -> None:
        from app.config import RetrySettings, Settings, save_config

        try:
            port = int(self.query_one("#field-port", Input).value)
            min_ctx = int(self.query_one("#field-min-context", Input).value or "0")
            max_retries = int(self.query_one("#field-max-retries", Input).value or "2")
            timeout = int(self.query_one("#field-timeout", Input).value or "300")
        except ValueError as e:
            self.query_one("#status", Label).update(f"Error: {e}")
            return

        whitelist_str = self.query_one("#field-whitelist", Input).value
        blocklist_str = self.query_one("#field-blocklist", Input).value

        new_config = Settings(
            horde_api_key=self.query_one("#field-api-key", Input).value,
            horde_api_url=self.query_one("#field-api-url", Input).value,
            host=self.query_one("#field-host", Input).value,
            port=port,
            default_model=self.query_one("#field-default-model", Input).value,
            model_min_context=min_ctx,
            model_whitelist=[s.strip() for s in whitelist_str.split(",") if s.strip()],
            model_blocklist=[s.strip() for s in blocklist_str.split(",") if s.strip()],
            retry=RetrySettings(
                max_retries=max_retries,
                timeout_seconds=timeout,
                broaden_on_retry=self.query_one("#field-broaden", Switch).value,
            ),
        )
        save_config(new_config)
        self.app.config = new_config
        self.query_one("#status", Label).update("✓ Config saved.")
