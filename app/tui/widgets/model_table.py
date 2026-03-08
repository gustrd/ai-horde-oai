"""Filterable model list widget."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Input, Label

from app.schemas.horde import HordeModel


class ModelTable(Widget):
    """Shows horde models with filter controls."""

    DEFAULT_CSS = """
    ModelTable {
        height: 1fr;
    }
    ModelTable #filter-row {
        height: 3;
        layout: horizontal;
    }
    ModelTable DataTable {
        height: 1fr;
    }
    """

    def __init__(self, models: list[HordeModel] | None = None, **kwargs):
        super().__init__(**kwargs)
        self._all_models: list[HordeModel] = models or []
        self._displayed: list[HordeModel] = list(self._all_models)

    def compose(self) -> ComposeResult:
        with Horizontal(id="filter-row"):
            yield Label("Filter: ")
            yield Input(placeholder="name substring...", id="filter-input")
        yield DataTable(id="model-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Status", "Name", "Context", "Max Len")
        self._render_table()
        table.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        q = event.value.lower()
        self._displayed = [m for m in self._all_models if q in m.name.lower()]
        self._render_table()

    def _render_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for i, m in enumerate(self._displayed):
            table.add_row(
                "✓", m.name, str(m.max_context_length), str(m.max_length), key=m.name
            )

    def set_models(self, models: list[HordeModel]) -> None:
        self._all_models = models
        self._displayed = list(models)
        self._render_table()

    @property
    def displayed_models(self) -> list[HordeModel]:
        return self._displayed
