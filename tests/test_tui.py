"""Tests for the TUI using Textual's headless Pilot."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.schemas.horde import HordeUser
from app.tui.app import HordeApp
from app.tui.screens.welcome import WelcomeScreen
from app.tui.screens.dashboard import DashboardScreen
from app.tui.screens.config import ConfigScreen
from app.tui.screens.models import ModelsScreen
from app.tui.widgets.kudos_bar import KudosBar
from app.tui.widgets.model_table import ModelTable
from app.schemas.horde import HordeModel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_config(**overrides) -> Settings:
    defaults = dict(
        horde_api_key="test-key-1234",
        horde_api_url="https://aihorde.net/api",
        default_model="aphrodite/llama-3.1-8b-instruct",
        host="127.0.0.1",
        port=8000,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_horde_mock(models=None, user=None):
    mock = AsyncMock()
    mock.get_models = AsyncMock(return_value=models or [])
    mock.get_user = AsyncMock(return_value=user or HordeUser(username="testuser", kudos=5000))
    mock.close = AsyncMock()
    return mock


# ---------------------------------------------------------------------------
# WelcomeScreen tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_welcome_screen_renders():
    """Welcome screen has the key input and both buttons."""
    config = make_config(horde_api_key="0000000000")
    app = HordeApp(config=config)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(WelcomeScreen())
            await pilot.pause()

            # Buttons exist
            assert app.screen.query("#validate-btn")
            assert app.screen.query("#anon-btn")
            # API key input exists
            assert app.screen.query("#api-key-input")


@pytest.mark.asyncio
async def test_welcome_anon_button_posts_message():
    """Clicking 'Use Anonymous' triggers ApiKeyChosen with key=0000000000."""
    config = make_config(horde_api_key="old-key")
    app = HordeApp(config=config)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()), \
         patch("app.config.save_config"), \
         patch("app.tui.app.HordeClient") as mock_client:
        mock_client.return_value.close = AsyncMock()
        async with app.run_test() as pilot:
            await app.push_screen(WelcomeScreen())
            await pilot.pause()
            await pilot.click("#anon-btn")
            await pilot.pause()

            assert app.config.horde_api_key == "0000000000"
            assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_welcome_validate_empty_key_shows_error():
    """Clicking Validate with empty input shows an error, no message posted."""
    config = make_config(horde_api_key="0000000000")
    received: list = []

    app = HordeApp(config=config)

    async def capture(event):
        received.append(event)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()), \
         patch.object(HordeApp, "on_welcome_screen_api_key_chosen", new=capture):
        async with app.run_test() as pilot:
            await app.push_screen(WelcomeScreen())
            await pilot.pause()
            # Don't type anything
            await pilot.click("#validate-btn")
            await pilot.pause()

    # No message should have been posted
    assert len(received) == 0


@pytest.mark.asyncio
async def test_welcome_set_validation_result():
    """set_validation_result updates status label."""
    config = make_config(horde_api_key="0000000000")
    app = HordeApp(config=config)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = WelcomeScreen()
            await app.push_screen(screen)
            await pilot.pause()
            screen.set_validation_result("alice", 9999)
            await pilot.pause()
            from textual.widgets import Label
            status = screen.query_one("#status-label", Label)
            assert "alice" in str(status.content)


# ---------------------------------------------------------------------------
# DashboardScreen tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_renders():
    """Dashboard mounts without errors and shows key labels."""
    config = make_config()
    app = HordeApp(config=config)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(DashboardScreen())
            await pilot.pause()

            assert app.screen.query("#server-status")
            assert app.screen.query("#api-key-status")
            assert app.screen.query(KudosBar)


@pytest.mark.asyncio
async def test_dashboard_shows_server_address():
    """Dashboard server status contains configured host:port."""
    from textual.widgets import Label

    config = make_config(host="0.0.0.0", port=9999)
    app = HordeApp(config=config)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(DashboardScreen())
            await pilot.pause()
            label = app.screen.query_one("#server-status", Label)
            text = str(label.content)
            assert "9999" in text


@pytest.mark.asyncio
async def test_dashboard_kudos_updates():
    """Setting kudos on dashboard updates the KudosBar."""
    config = make_config()
    app = HordeApp(config=config)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = DashboardScreen()
            await app.push_screen(screen)
            await pilot.pause()
            screen.set_kudos(12450)
            await pilot.pause()
            assert screen.query_one(KudosBar).balance == 12450


# ---------------------------------------------------------------------------
# ConfigScreen tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_screen_renders():
    """Config screen renders all key inputs."""
    config = make_config()
    app = HordeApp(config=config)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(ConfigScreen())
            await pilot.pause()

            assert app.screen.query("#field-api-key")
            assert app.screen.query("#field-api-url")
            assert app.screen.query("#field-host")
            assert app.screen.query("#field-port")
            assert app.screen.query("#field-blocklist")


@pytest.mark.asyncio
async def test_config_screen_prefills_values():
    """Config screen pre-fills fields from current config."""
    from textual.widgets import Input

    config = make_config(host="192.168.1.1", port=1234)
    app = HordeApp(config=config)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(ConfigScreen())
            await pilot.pause()

            host = app.screen.query_one("#field-host", Input).value
            port = app.screen.query_one("#field-port", Input).value
            assert host == "192.168.1.1"
            assert port == "1234"


@pytest.mark.asyncio
async def test_config_screen_save():
    """Clicking Save writes config and updates app.config."""
    from textual.widgets import Input

    config = make_config()
    app = HordeApp(config=config)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()), \
         patch("app.config.save_config") as mock_save:
        async with app.run_test() as pilot:
            await app.push_screen(ConfigScreen())
            await pilot.pause()

            # Change port
            port_input = app.screen.query_one("#field-port", Input)
            await pilot.click("#field-port")
            port_input.value = "7777"
            await pilot.pause()

            await pilot.click("#save-btn")
            await pilot.pause()

        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert saved_config.port == 7777


# ---------------------------------------------------------------------------
# ModelsScreen tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_models_screen_renders():
    """Models screen renders the table widget."""
    config = make_config()
    app = HordeApp(config=config)
    app.horde = make_horde_mock(models=[
        HordeModel(name="aphrodite/llama-3.1-8b", max_context_length=8192, max_length=512),
        HordeModel(name="koboldcpp/mistral-7b", max_context_length=4096, max_length=512),
    ])

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(ModelsScreen())
            await pilot.pause()
            await pilot.pause()  # second pause for async _load_models worker

            assert app.screen.query(ModelTable)


@pytest.mark.asyncio
async def test_models_screen_shows_count():
    """Models screen info label shows model count after load."""
    from textual.widgets import Label

    models = [
        HordeModel(name="aphrodite/llama-3.1-8b", max_context_length=8192, max_length=512),
        HordeModel(name="koboldcpp/mistral-7b", max_context_length=4096, max_length=512),
    ]
    config = make_config()
    app = HordeApp(config=config)
    app.horde = make_horde_mock(models=models)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = ModelsScreen()
            await app.push_screen(screen)
            # Wait for worker to finish
            await pilot.pause(0.5)

            info = screen.query_one("#info", Label)
            assert "2" in str(info.content)


# ---------------------------------------------------------------------------
# KudosBar widget tests (unit, no app needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kudos_bar_initial_state():
    """KudosBar shows '...' when balance is None."""
    from textual.widgets import Label

    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            bar = KudosBar()
            await app.mount(bar)
            await pilot.pause()
            label = bar.query_one("#kudos-label", Label)
            assert "..." in str(label.content)


@pytest.mark.asyncio
async def test_kudos_bar_updates_on_balance_change():
    """KudosBar label updates when balance reactive changes."""
    from textual.widgets import Label

    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            bar = KudosBar()
            await app.mount(bar)
            await pilot.pause()
            bar.balance = 3000
            await pilot.pause()
            label = bar.query_one("#kudos-label", Label)
            assert "3,000" in str(label.content)


@pytest.mark.asyncio
async def test_kudos_bar_low_balance_adds_class():
    """KudosBar adds 'low' CSS class when balance < 100."""
    from textual.widgets import Label

    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            bar = KudosBar()
            await app.mount(bar)
            await pilot.pause()
            bar.balance = 50
            await pilot.pause()
            label = bar.query_one("#kudos-label", Label)
            assert "low" in label.classes


# ---------------------------------------------------------------------------
# ModelTable widget tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_table_shows_all_models():
    """ModelTable shows all models when no filter is active."""
    from textual.widgets import DataTable

    models = [
        HordeModel(name="model-a", max_context_length=4096, max_length=512),
        HordeModel(name="model-b", max_context_length=8192, max_length=512),
        HordeModel(name="model-c", max_context_length=16384, max_length=512),
    ]
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            table_widget = ModelTable(models=models)
            await app.mount(table_widget)
            await pilot.pause()
            dt = table_widget.query_one(DataTable)
            assert dt.row_count == 3


@pytest.mark.asyncio
async def test_model_table_filter_by_name():
    """ModelTable filters rows when text is typed into filter input."""
    from textual.widgets import DataTable, Input

    models = [
        HordeModel(name="aphrodite/llama-3.1-8b", max_context_length=4096, max_length=512),
        HordeModel(name="koboldcpp/mistral-7b", max_context_length=4096, max_length=512),
        HordeModel(name="aphrodite/llama-3.1-70b", max_context_length=4096, max_length=512),
    ]
    app = HordeApp(config=make_config())
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            table_widget = ModelTable(models=models)
            await app.mount(table_widget)
            await pilot.pause()

            inp = table_widget.query_one("#filter-input", Input)
            inp.value = "llama"
            # Trigger the input changed event manually
            table_widget.on_input_changed(Input.Changed(inp, "llama"))
            await pilot.pause()

            dt = table_widget.query_one(DataTable)
            assert dt.row_count == 2
            assert len(table_widget.displayed_models) == 2
