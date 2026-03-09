"""Tests for ModelTable widget filtering: text search, settings filters, name wrapping."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.schemas.horde import HordeModel
from app.tui.app import HordeApp
from app.tui.screens.models import ModelsScreen
from app.tui.widgets.model_table import ModelTable
from textual.widgets import DataTable, Input, Label


pytestmark = pytest.mark.asyncio


def make_config(**overrides) -> Settings:
    defaults = dict(
        horde_api_key="test-key",
        horde_api_url="https://aihorde.net/api",
        default_model="model-a",
        host="127.0.0.1",
        port=8000,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_horde_mock(models=None):
    mock = AsyncMock()
    mock.get_models = AsyncMock(return_value=models or [])
    mock.get_text_workers = AsyncMock(return_value=[])
    mock.get_user = AsyncMock()
    mock.close = AsyncMock()
    return mock


def _models(*names, ctx=4096, max_len=512) -> list[HordeModel]:
    return [HordeModel(name=n, max_context_length=ctx, max_length=max_len) for n in names]


# ---------------------------------------------------------------------------
# ModelTable widget — text filter (case insensitivity)
# ---------------------------------------------------------------------------

async def test_text_filter_case_insensitive():
    """Typing uppercase query matches lowercase names and vice versa."""
    models = _models("aphrodite/LLaMA-3.1-8b", "koboldcpp/mistral-7b")
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable(models=models)
            await app.mount(widget)
            await pilot.pause()

            inp = widget.query_one("#filter-input", Input)
            widget.on_input_changed(Input.Changed(inp, "llama"))
            await pilot.pause()

            assert len(widget.displayed_models) == 1
            assert widget.displayed_models[0].name == "aphrodite/LLaMA-3.1-8b"


async def test_text_filter_uppercase_query():
    """Typing lowercase model name with uppercase query still matches."""
    models = _models("koboldcpp/mistral-7b", "aphrodite/llama-3.1-8b")
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable(models=models)
            await app.mount(widget)
            await pilot.pause()

            inp = widget.query_one("#filter-input", Input)
            widget.on_input_changed(Input.Changed(inp, "MISTRAL"))
            await pilot.pause()

            assert len(widget.displayed_models) == 1
            assert "mistral" in widget.displayed_models[0].name.lower()


async def test_text_filter_empty_shows_all():
    """Empty filter shows all models."""
    models = _models("model-a", "model-b", "model-c")
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable(models=models)
            await app.mount(widget)
            await pilot.pause()

            inp = widget.query_one("#filter-input", Input)
            widget.on_input_changed(Input.Changed(inp, "b"))
            await pilot.pause()
            assert len(widget.displayed_models) == 1

            widget.on_input_changed(Input.Changed(inp, ""))
            await pilot.pause()
            assert len(widget.displayed_models) == 3


# ---------------------------------------------------------------------------
# ModelTable widget — settings filters (whitelist / blocklist / context)
# ---------------------------------------------------------------------------

async def test_settings_whitelist_filters_models():
    """set_models with whitelist keeps only matching models."""
    models = _models("aphrodite/llama-3.1-8b", "koboldcpp/mistral-7b", "aphrodite/llama-3.1-70b")
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable()
            await app.mount(widget)
            await pilot.pause()

            widget.set_models(models, whitelist=["llama"])
            await pilot.pause()

            assert len(widget.displayed_models) == 2
            assert all("llama" in m.name.lower() for m in widget.displayed_models)


async def test_settings_blocklist_filters_models():
    """set_models with blocklist removes matching models."""
    models = _models("aphrodite/llama-3.1-8b", "koboldcpp/mistral-7b", "aphrodite/llama-3.1-70b")
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable()
            await app.mount(widget)
            await pilot.pause()

            widget.set_models(models, blocklist=["mistral"])
            await pilot.pause()

            assert len(widget.displayed_models) == 2
            assert all("mistral" not in m.name.lower() for m in widget.displayed_models)


async def test_settings_min_context_filters_models():
    """set_models with min_context removes models below threshold."""
    models = [
        HordeModel(name="small", max_context_length=2048, max_length=512),
        HordeModel(name="medium", max_context_length=4096, max_length=512),
        HordeModel(name="large", max_context_length=8192, max_length=512),
    ]
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable()
            await app.mount(widget)
            await pilot.pause()

            widget.set_models(models, min_context=4096)
            await pilot.pause()

            assert len(widget.displayed_models) == 2
            assert all(m.max_context_length >= 4096 for m in widget.displayed_models)


async def test_settings_min_max_length_filters_models():
    """set_models with min_max_length removes models below threshold."""
    models = [
        HordeModel(name="short", max_context_length=4096, max_length=128),
        HordeModel(name="medium", max_context_length=4096, max_length=256),
        HordeModel(name="long", max_context_length=4096, max_length=512),
    ]
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable()
            await app.mount(widget)
            await pilot.pause()

            widget.set_models(models, min_max_length=256)
            await pilot.pause()

            assert len(widget.displayed_models) == 2
            assert all(m.max_length >= 256 for m in widget.displayed_models)


async def test_settings_and_text_filter_combined():
    """Settings filters and text search are applied together."""
    models = [
        HordeModel(name="aphrodite/llama-3.1-8b", max_context_length=4096, max_length=512),
        HordeModel(name="aphrodite/llama-3.1-70b", max_context_length=4096, max_length=512),
        HordeModel(name="koboldcpp/mistral-7b", max_context_length=4096, max_length=512),
    ]
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable()
            await app.mount(widget)
            await pilot.pause()

            # Settings: only llama; then text filter: only 70b
            widget.set_models(models, whitelist=["llama"])
            await pilot.pause()
            assert len(widget.displayed_models) == 2

            inp = widget.query_one("#filter-input", Input)
            widget.on_input_changed(Input.Changed(inp, "70b"))
            await pilot.pause()

            assert len(widget.displayed_models) == 1
            assert "70b" in widget.displayed_models[0].name


async def test_all_models_property_reflects_unfiltered_total():
    """all_models always returns full list regardless of active filters."""
    models = _models("a", "b", "c", "d")
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable()
            await app.mount(widget)
            await pilot.pause()

            widget.set_models(models, whitelist=["a"])
            await pilot.pause()

            assert len(widget.all_models) == 4
            assert len(widget.displayed_models) == 1


# ---------------------------------------------------------------------------
# ModelTable — name line-wrapping
# ---------------------------------------------------------------------------

def _get_name_cell(dt: DataTable, row_key) -> str:
    """Return the Name column cell value (last column, index 5)."""
    col_key = list(dt.columns.keys())[5]
    return str(dt.get_cell(row_key, col_key))


async def test_long_name_wrapped_in_table():
    """Model names longer than 40 chars are wrapped with a newline in the table."""
    long_name = "aphrodite/very-long-model-name-that-exceeds-forty-characters"
    assert len(long_name) > 40

    models = [HordeModel(name=long_name, max_context_length=4096, max_length=512)]
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable(models=models)
            await app.mount(widget)
            await pilot.pause()

            dt = widget.query_one(DataTable)
            row_key = list(dt.rows.keys())[0]
            assert "\n" in _get_name_cell(dt, row_key)


async def test_short_name_not_wrapped():
    """Short model names are not wrapped."""
    models = [HordeModel(name="short-model", max_context_length=4096, max_length=512)]
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable(models=models)
            await app.mount(widget)
            await pilot.pause()

            dt = widget.query_one(DataTable)
            row_key = list(dt.rows.keys())[0]
            assert "\n" not in _get_name_cell(dt, row_key)


async def test_row_key_is_original_name():
    """Row key stays as the original (unwrapped) model name for selection."""
    long_name = "aphrodite/very-long-model-name-that-exceeds-forty-characters"
    models = [HordeModel(name=long_name, max_context_length=4096, max_length=512)]
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable(models=models)
            await app.mount(widget)
            await pilot.pause()

            dt = widget.query_one(DataTable)
            row_key = list(dt.rows.keys())[0]
            assert str(row_key.value) == long_name


# ---------------------------------------------------------------------------
# ModelsScreen — info label dynamic updates
# ---------------------------------------------------------------------------

async def test_models_screen_info_updates_on_search():
    """Info label updates dynamically when user types in the filter box."""
    models = _models("aphrodite/llama-3.1-8b", "koboldcpp/mistral-7b", "aphrodite/llama-70b")
    config = make_config()
    app = HordeApp(config=config)
    app.horde = make_horde_mock(models=models)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = ModelsScreen()
            await app.push_screen(screen)
            await pilot.pause(0.5)

            widget = screen.query_one(ModelTable)
            inp = widget.query_one("#filter-input", Input)

            # Simulate typing "llama"
            inp.value = "llama"
            widget.on_input_changed(Input.Changed(inp, "llama"))
            # Bubble up to screen
            screen.on_input_changed(Input.Changed(inp, "llama"))
            await pilot.pause()

            info = screen.query_one("#info", Label)
            text = str(info.content)
            assert "search" in text.lower() or "llama" in text.lower()
            assert "2" in text  # 2 llama models


async def test_models_screen_settings_filters_applied():
    """ModelsScreen applies config whitelist — only matching models shown."""
    models = _models("aphrodite/llama-3.1-8b", "koboldcpp/mistral-7b", "aphrodite/llama-70b")
    config = make_config(model_whitelist=["llama"])
    app = HordeApp(config=config)
    app.horde = make_horde_mock(models=models)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = ModelsScreen()
            await app.push_screen(screen)
            await pilot.pause(0.5)

            widget = screen.query_one(ModelTable)
            assert len(widget.displayed_models) == 2
            assert all("llama" in m.name.lower() for m in widget.displayed_models)
            assert len(widget.all_models) == 3  # full list preserved


async def test_models_screen_blocklist_applied():
    """ModelsScreen applies config blocklist — blocked models not shown."""
    models = _models("aphrodite/llama-3.1-8b", "koboldcpp/mistral-7b")
    config = make_config(model_blocklist=["mistral"])
    app = HordeApp(config=config)
    app.horde = make_horde_mock(models=models)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = ModelsScreen()
            await app.push_screen(screen)
            await pilot.pause(0.5)

            widget = screen.query_one(ModelTable)
            assert len(widget.displayed_models) == 1
            assert "mistral" not in widget.displayed_models[0].name.lower()


# ---------------------------------------------------------------------------
# update_filters — live re-apply without re-fetching
# ---------------------------------------------------------------------------

async def test_update_filters_removes_newly_blocked():
    """update_filters hides models that are now blocked after a config change."""
    models = _models("aphrodite/llama-3.1-8b", "koboldcpp/mistral-7b")
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable()
            await app.mount(widget)
            await pilot.pause()

            widget.set_models(models)
            await pilot.pause()
            assert len(widget.displayed_models) == 2

            # Config changes: now block mistral
            widget.update_filters(blocklist=["mistral"])
            await pilot.pause()
            assert len(widget.displayed_models) == 1
            assert "mistral" not in widget.displayed_models[0].name.lower()


async def test_update_filters_preserves_text_query():
    """update_filters keeps the active text search while changing settings filters."""
    models = _models("aphrodite/llama-3.1-8b", "aphrodite/llama-70b", "koboldcpp/mistral-7b")
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            widget = ModelTable()
            await app.mount(widget)
            await pilot.pause()

            widget.set_models(models)
            inp = widget.query_one("#filter-input", Input)
            inp.value = "llama"
            widget.on_input_changed(Input.Changed(inp, "llama"))
            await pilot.pause()
            assert len(widget.displayed_models) == 2

            # Now apply whitelist — combined with text filter should still yield 2
            widget.update_filters(whitelist=["llama"])
            await pilot.pause()
            assert len(widget.displayed_models) == 2
            assert all("llama" in m.name.lower() for m in widget.displayed_models)


# ---------------------------------------------------------------------------
# on_data_table_row_selected guard — blocked model cannot be set as default
# ---------------------------------------------------------------------------

async def test_row_selection_blocked_by_current_filters():
    """Selecting a row whose model is now blocked by config shows warning, not set as default."""
    models = [HordeModel(name="koboldcpp/mistral-7b", max_context_length=4096, max_length=512)]
    config = make_config()
    app = HordeApp(config=config)
    app.horde = make_horde_mock(models=models)
    notifications = []

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = ModelsScreen()
            await app.push_screen(screen)
            await pilot.pause(0.5)

            # Now change config to block mistral
            app.config = app.config.model_copy(update={"model_blocklist": ["mistral"]})

            # Simulate row selection
            mock_event = MagicMock()
            mock_event.row_key.value = "koboldcpp/mistral-7b"

            orig_notify = screen.notify
            def capture_notify(msg, **kw):
                notifications.append((msg, kw))
                return orig_notify(msg, **kw)
            screen.notify = capture_notify

            screen.on_data_table_row_selected(mock_event)
            await pilot.pause()

            # Should NOT have changed selected_model
            assert getattr(app, "selected_model", None) != "koboldcpp/mistral-7b"
            # Should have shown a warning notification
            assert any("blocked" in str(msg).lower() or "filter" in str(msg).lower()
                       for msg, _ in notifications)


async def test_row_selection_passes_when_model_allowed():
    """Selecting a row whose model passes current filters sets it as default."""
    models = [HordeModel(name="aphrodite/llama-3.1-8b", max_context_length=4096, max_length=512)]
    config = make_config()
    app = HordeApp(config=config)
    app.horde = make_horde_mock(models=models)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()), \
         patch("app.config.save_config"):
        async with app.run_test() as pilot:
            screen = ModelsScreen()
            await app.push_screen(screen)
            await pilot.pause(0.5)

            mock_event = MagicMock()
            mock_event.row_key.value = "aphrodite/llama-3.1-8b"
            screen.on_data_table_row_selected(mock_event)
            await pilot.pause()

            assert app.selected_model == "aphrodite/llama-3.1-8b"
