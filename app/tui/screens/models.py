"""Models browser screen."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label

from app.tui.widgets.model_table import ModelTable


class ModelsScreen(Screen):
    """Browse available Horde models."""

    TITLE = "Models Browser"
    BINDINGS = [
        ("d", "switch_screen('dashboard')", "Dash"),
        ("s", "switch_screen('config')", "Set"),
        ("m", "switch_screen('models')", "Mod"),
        ("c", "switch_screen('chat')", "Chat"),
        ("l", "switch_screen('logs')", "Log"),
        ("h", "switch_screen('history')", "Hist"),
        ("q", "quit", "Quit"),
        ("r", "refresh", "Ref"),
    ]

    DEFAULT_CSS = """
    ModelsScreen #info {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    ModelsScreen #tip {
        height: 1;
        padding: 0 1;
        color: $accent;
        text-style: italic;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Loading models...", id="info", markup=False)
        yield ModelTable(id="model-table-widget")
        yield Label("Tip: Press Enter on a model to select it for Chat.", id="tip", classes="stat-row", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load_models(), exclusive=True)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Get the model name from the row key
        model_name = str(event.row_key.value)
        self.app.selected_model = model_name
        
        # Switch to chat first
        self.app.switch_screen("chat")
        
        # Then update the select widget if it's already there
        try:
            # We use call_after_refresh to ensure the chat screen is mounted
            def update_select():
                try:
                    chat_screen = self.app.get_screen("chat")
                    from textual.widgets import Select
                    select = chat_screen.query_one("#model-select", Select)
                    
                    # Ensure options are loaded and include our model
                    options = list(select._options) if hasattr(select, "_options") else []
                    option_values = [opt[1] for opt in options]
                    
                    if model_name not in option_values:
                        options.append((model_name, model_name))
                        select.set_options(options)
                    
                    select.value = model_name
                except Exception:
                    pass
            
            self.call_after_refresh(update_select)
        except Exception:
            pass

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
