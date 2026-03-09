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
        yield Label("Tip: Press Enter on a model to set it as default and open Chat.", id="tip", classes="stat-row", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load_models(), exclusive=True)

    def on_screen_resume(self) -> None:
        """Re-apply config filters whenever the screen comes back into focus."""
        cfg = self.app.config
        try:
            widget = self.query_one(ModelTable)
            widget.update_filters(
                whitelist=cfg.model_whitelist or None,
                blocklist=cfg.model_blocklist or None,
                min_context=cfg.model_min_context,
                min_max_length=cfg.model_min_max_length,
            )
            shown = len(widget.displayed_models)
            total = len(widget.all_models)
            self.query_one("#info", Label).update(f"{shown} of {total} models")
        except Exception:
            pass

    def on_input_changed(self, event) -> None:
        """Update info label when the filter input changes."""
        widget = self.query_one(ModelTable)
        total = len(widget.all_models)
        shown = len(widget.displayed_models)
        q = event.value.strip()
        if q:
            self.query_one("#info", Label).update(
                f"{shown} of {total} models (search: '{q}')"
            )
        else:
            self.query_one("#info", Label).update(
                f"{shown} of {total} models"
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Get the model name from the row key
        model_name = str(event.row_key.value)

        # Guard: verify model still passes current config filters
        from app.horde.filters import filter_models as _filter
        widget = self.query_one(ModelTable)
        model_obj = next((m for m in widget.all_models if m.name == model_name), None)
        if model_obj is not None:
            cfg = self.app.config
            allowed = _filter(
                [model_obj],
                whitelist=cfg.model_whitelist or None,
                blocklist=cfg.model_blocklist or None,
                min_context=cfg.model_min_context,
                min_max_length=cfg.model_min_max_length,
            )
            if not allowed:
                self.notify(
                    f"'{model_name}' is excluded by current filter settings.",
                    title="Model Blocked",
                    severity="warning",
                )
                return

        self.app.selected_model = model_name

        # Persist as default_model in config
        from app.config import save_config
        self.app.config = self.app.config.model_copy(update={"default_model": model_name})
        try:
            save_config(self.app.config)
        except Exception:
            pass

        self.notify(f"Default model set to: {model_name}", title="Model Selected")

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
        import asyncio
        try:
            horde = self.app.horde
            models_task = asyncio.create_task(horde.get_models(type="text"))
            workers_task = asyncio.create_task(horde.get_text_workers())
            models, workers = await asyncio.gather(models_task, workers_task)

            # Aggregate max context/length per model name from workers
            ctx_map: dict[str, int] = {}
            len_map: dict[str, int] = {}
            for w in workers:
                if not w.get("online"):
                    continue
                max_ctx = w.get("max_context_length", 0)
                max_len = w.get("max_length", 0)
                for model_name in w.get("models", []):
                    if max_ctx > ctx_map.get(model_name, 0):
                        ctx_map[model_name] = max_ctx
                    if max_len > len_map.get(model_name, 0):
                        len_map[model_name] = max_len

            models = [
                m.model_copy(update={
                    "max_context_length": ctx_map.get(m.name, m.max_context_length),
                    "max_length": len_map.get(m.name, m.max_length),
                })
                for m in models
            ]

            cfg = self.app.config
            widget = self.query_one(ModelTable)
            widget.set_models(
                models,
                whitelist=cfg.model_whitelist or None,
                blocklist=cfg.model_blocklist or None,
                min_context=cfg.model_min_context,
                min_max_length=cfg.model_min_max_length,
            )
            shown = len(widget.displayed_models)
            total = len(models)
            self.query_one("#info", Label).update(f"{shown} of {total} models")

            # Store on app so dashboard can read it whenever it's visible
            self.app.model_count = shown
            self.app.model_total = total
        except Exception as e:
            self.query_one("#info", Label).update(f"Error: {e}")

    def action_refresh(self) -> None:
        self.query_one("#info", Label).update("Refreshing...")
        self.run_worker(self._load_models(), exclusive=True)
