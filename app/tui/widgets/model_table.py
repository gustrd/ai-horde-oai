"""Filterable model list widget."""
from __future__ import annotations

import textwrap

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Input, Label

from app.horde.filters import filter_models
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

_NAME_WRAP = 40  # chars before wrapping model name


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
        # settings filters (applied before text search)
        self._whitelist: list[str] = []
        self._blocklist: list[str] = []
        self._min_context: int = 0
        self._min_max_length: int = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="filter-row"):
            yield Label("Filter: ")
            yield Input(placeholder="name substring (case-insensitive)...", id="filter-input")
        yield DataTable(id="model-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*[col[0] for col in _COLUMNS])
        self._render_table()
        table.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._apply_filters(event.value)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        col = event.column_index
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = _COLUMNS[col][2]
        self._render_table()

    def _apply_filters(self, text_query: str = "") -> None:
        """Apply settings filters then text search, update displayed list."""
        after_settings = filter_models(
            self._all_models,
            whitelist=self._whitelist or None,
            blocklist=self._blocklist or None,
            min_context=self._min_context,
            min_max_length=self._min_max_length,
        )
        q = text_query.strip().lower()
        if q:
            self._displayed = [m for m in after_settings if q in m.name.lower()]
        else:
            self._displayed = list(after_settings)
        self._render_table()

    def _render_table(self) -> None:
        key_fn, reverse = _COLUMNS[self._sort_col][1], self._sort_reverse
        sorted_models = sorted(self._displayed, key=key_fn, reverse=reverse)

        table = self.query_one(DataTable)
        table.clear()
        for m in sorted_models:
            eta_str = f"{m.eta}s" if m.eta else "-"
            name = textwrap.fill(m.name, width=_NAME_WRAP)
            table.add_row(
                str(m.count),
                str(m.max_context_length),
                str(m.max_length),
                str(m.queued),
                eta_str,
                name,
                key=m.name,  # key stays original (used for model selection)
            )

    def set_models(
        self,
        models: list[HordeModel],
        *,
        whitelist: list[str] | None = None,
        blocklist: list[str] | None = None,
        min_context: int = 0,
        min_max_length: int = 0,
    ) -> None:
        self._all_models = models
        self._whitelist = whitelist or []
        self._blocklist = blocklist or []
        self._min_context = min_context
        self._min_max_length = min_max_length
        # preserve current text filter
        try:
            q = self.query_one("#filter-input", Input).value
        except Exception:
            q = ""
        self._apply_filters(q)

    def update_filters(
        self,
        *,
        whitelist: list[str] | None = None,
        blocklist: list[str] | None = None,
        min_context: int = 0,
        min_max_length: int = 0,
    ) -> None:
        """Re-apply settings filters with new values, keeping current models and text query."""
        self._whitelist = whitelist or []
        self._blocklist = blocklist or []
        self._min_context = min_context
        self._min_max_length = min_max_length
        try:
            q = self.query_one("#filter-input", Input).value
        except Exception:
            q = ""
        self._apply_filters(q)

    @property
    def displayed_models(self) -> list[HordeModel]:
        return self._displayed

    @property
    def all_models(self) -> list[HordeModel]:
        return self._all_models
