"""Models browser screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label

from app.tui.widgets.model_table import ModelTable


class ModelsScreen(Screen):
    """Browse available Horde models."""

    TITLE = "Models Browser"
    BINDINGS = [
        ("escape", "pop_screen", "Back"),
        ("r", "refresh", "Refresh"),
    ]

    DEFAULT_CSS = """
    ModelsScreen #info {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Loading models...", id="info")
        yield ModelTable(id="model-table-widget")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load_models(), exclusive=True)

    async def _load_models(self) -> None:
        try:
            horde = self.app.horde
            models = await horde.get_models(type="text")
            cfg = self.app.config
            from app.horde.filters import filter_models
            filtered = filter_models(
                models,
                whitelist=cfg.model_whitelist,
                blocklist=cfg.model_blocklist,
                min_context=cfg.model_min_context,
                min_max_length=cfg.model_min_max_length,
            )
            widget = self.query_one(ModelTable)
            widget.set_models(filtered)
            self.query_one("#info", Label).update(
                f"{len(filtered)} models shown (from {len(models)} total)"
            )
        except Exception as e:
            self.query_one("#info", Label).update(f"Error: {e}")

    def action_refresh(self) -> None:
        self.query_one("#info", Label).update("Refreshing...")
        self.run_worker(self._load_models(), exclusive=True)
