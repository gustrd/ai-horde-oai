"""Filterable model list widget."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Input, Label

from app.schemas.horde import HordeModel

# (column_label, sort_key_fn, default_reverse)
_COLUMNS: list[tuple[str, callable, bool]] = [
    ("Workers",  lambda m: m.count,               True),
    ("Max Ctx",  lambda m: m.max_context_length,  True),
    ("Max Tok",  lambda m: m.max_length,           True),
    ("Queued",   lambda m: m.queued,               True),
    ("ETA",      lambda m: m.eta,                  True),
    ("Name",     lambda m: m.name.lower(),         False),
]


class ModelTable(Widget):
    """Shows horde models with filter and click-to-sort."""

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
        self._sort_col: int = 3          # default: Queued
        self._sort_reverse: bool = True

    def compose(self) -> ComposeResult:
        with Horizontal(id="filter-row"):
            yield Label("Filter: ")
            yield Input(placeholder="name substring...", id="filter-input")
        yield DataTable(id="model-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*[col[0] for col in _COLUMNS])
        self._render_table()
        table.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        q = event.value.lower()
        self._displayed = [m for m in self._all_models if q in m.name.lower()]
        self._render_table()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        col = event.column_index
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = _COLUMNS[col][2]
        self._render_table()

    def _render_table(self) -> None:
        key_fn, reverse = _COLUMNS[self._sort_col][1], self._sort_reverse
        sorted_models = sorted(self._displayed, key=key_fn, reverse=reverse)

        table = self.query_one(DataTable)
        table.clear()
        for m in sorted_models:
            eta_str = f"{m.eta}s" if m.eta else "-"
            table.add_row(
                str(m.count),
                str(m.max_context_length),
                str(m.max_length),
                str(m.queued),
                eta_str,
                m.name,
                key=m.name,
            )

    def set_models(self, models: list[HordeModel]) -> None:
        self._all_models = models
        self._displayed = list(models)
        self._render_table()

    @property
    def displayed_models(self) -> list[HordeModel]:
        return self._displayed
