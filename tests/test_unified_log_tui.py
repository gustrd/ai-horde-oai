"""TUI unit tests for the unified LogsScreen and LogDetailModal."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Label

from app.config import Settings
from app.log_store import RequestLogEntry
from app.tui.app import HordeApp
from app.tui.screens.logs import LogDetailModal, LogsScreen


def make_config(**overrides) -> Settings:
    defaults = dict(
        horde_api_key="test-key-1234",
        horde_api_url="https://aihorde.net/api",
        host="127.0.0.1",
        port=8002,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_entry(**kwargs) -> RequestLogEntry:
    defaults = dict(
        timestamp=datetime(2024, 6, 1, 14, 30, 0),
        method="POST",
        path="/v1/chat/completions",
        status=200,
        duration=12.5,
        model="best",
        real_model="aphrodite/llama-8b",
        worker="WorkerA",
        worker_id="wid-aaa",
        kudos=4.0,
        messages=[{"role": "user", "content": "Hello"}],
        response_text="Hi there!",
    )
    defaults.update(kwargs)
    return RequestLogEntry(**defaults)


# ---------------------------------------------------------------------------
# LogsScreen rendering and population
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logs_screen_renders_empty():
    """LogsScreen mounts without error and shows 'No requests yet' when empty."""
    app = HordeApp(config=make_config(), start_server=False)
    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = LogsScreen()
            await app.push_screen(screen)
            await pilot.pause()

            info = screen.query_one("#info", Label)
            assert "No requests" in str(info.content)
            table = screen.query_one(DataTable)
            assert table.row_count == 0


@pytest.mark.asyncio
async def test_logs_screen_shows_preloaded_entries():
    """Entries already in app.request_log appear as rows when screen mounts."""
    app = HordeApp(config=make_config(), start_server=False)
    app.request_log.append(make_entry())
    app.request_log.append(make_entry(path="/v1/completions", model="default"))

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = LogsScreen()
            await app.push_screen(screen)
            await pilot.pause()

            table = screen.query_one(DataTable)
            assert table.row_count == 2


@pytest.mark.asyncio
async def test_logs_screen_add_entry_appends_row():
    """add_entry() adds a new row to the DataTable."""
    app = HordeApp(config=make_config(), start_server=False)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = LogsScreen()
            await app.push_screen(screen)
            await pilot.pause()

            assert screen.query_one(DataTable).row_count == 0

            entry = make_entry()
            app.request_log.append(entry)
            screen.add_entry(entry)
            await pilot.pause()

            assert screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_logs_screen_resume_syncs_new_entries():
    """on_screen_resume adds rows for entries added while screen was inactive."""
    app = HordeApp(config=make_config(), start_server=False)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = LogsScreen()
            await app.push_screen(screen)
            await pilot.pause()

            # Simulate entry added while screen inactive
            app.request_log.append(make_entry())
            # Trigger resume manually
            screen.on_screen_resume()
            await pilot.pause()

            assert screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_logs_screen_clear_action():
    """action_clear removes all rows and clears app.request_log."""
    app = HordeApp(config=make_config(), start_server=False)
    app.request_log.extend([make_entry(), make_entry()])

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = LogsScreen()
            await app.push_screen(screen)
            await pilot.pause()

            assert screen.query_one(DataTable).row_count == 2

            screen.action_clear()
            await pilot.pause()

            assert screen.query_one(DataTable).row_count == 0
            assert len(app.request_log) == 0
            info = screen.query_one("#info", Label)
            assert "No requests" in str(info.content)


@pytest.mark.asyncio
async def test_logs_screen_info_updates_with_count():
    """Info label reflects the current row count after adding entries."""
    app = HordeApp(config=make_config(), start_server=False)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = LogsScreen()
            await app.push_screen(screen)
            await pilot.pause()

            for _ in range(3):
                entry = make_entry()
                app.request_log.append(entry)
                screen.add_entry(entry)
            await pilot.pause()

            info_text = str(screen.query_one("#info", Label).content)
            assert "3" in info_text


@pytest.mark.asyncio
async def test_logs_screen_has_kudos_column():
    """The DataTable has a 'Kudos' column."""
    app = HordeApp(config=make_config(), start_server=False)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = LogsScreen()
            await app.push_screen(screen)
            await pilot.pause()

            table = screen.query_one(DataTable)
            col_labels = [str(col.label) for col in table.columns.values()]
            assert "Kudos" in col_labels


# ---------------------------------------------------------------------------
# LogDetailModal content
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detail_modal_shows_request_fields():
    """LogDetailModal renders timestamp, method, path, status, duration."""
    app = HordeApp(config=make_config(), start_server=False)
    entry = make_entry()

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(LogDetailModal(entry))
            await pilot.pause()

            from textual.widgets import Static
            content = app.screen.query_one("#detail-content", Static)
            text = str(content.content)
            assert "POST" in text
            assert "/v1/chat/completions" in text
            assert "200" in text


@pytest.mark.asyncio
async def test_detail_modal_shows_horde_fields():
    """LogDetailModal renders model, real_model, worker, worker_id, kudos."""
    app = HordeApp(config=make_config(), start_server=False)
    entry = make_entry()

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(LogDetailModal(entry))
            await pilot.pause()

            from textual.widgets import Static
            text = str(app.screen.query_one("#detail-content", Static).content)
            assert "best" in text
            assert "aphrodite/llama-8b" in text
            assert "WorkerA" in text
            assert "wid-aaa" in text
            assert "4.00" in text


@pytest.mark.asyncio
async def test_detail_modal_shows_messages():
    """LogDetailModal renders chat messages when present."""
    app = HordeApp(config=make_config(), start_server=False)
    entry = make_entry(messages=[
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "What is Python?"},
    ])

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(LogDetailModal(entry))
            await pilot.pause()

            from textual.widgets import Static
            text = str(app.screen.query_one("#detail-content", Static).content)
            assert "SYSTEM" in text
            assert "USER" in text
            assert "You are helpful." in text
            assert "What is Python?" in text


@pytest.mark.asyncio
async def test_detail_modal_shows_response_text():
    """LogDetailModal renders the generated response."""
    app = HordeApp(config=make_config(), start_server=False)
    entry = make_entry(response_text="Python is a programming language.")

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(LogDetailModal(entry))
            await pilot.pause()

            from textual.widgets import Static
            text = str(app.screen.query_one("#detail-content", Static).content)
            assert "Python is a programming language." in text


@pytest.mark.asyncio
async def test_detail_modal_shows_error():
    """LogDetailModal renders error field when set."""
    app = HordeApp(config=make_config(), start_server=False)
    entry = make_entry(status=504, error="Job timed out after 300s", response_text="")

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(LogDetailModal(entry))
            await pilot.pause()

            from textual.widgets import Static
            text = str(app.screen.query_one("#detail-content", Static).content)
            assert "Job timed out" in text


@pytest.mark.asyncio
async def test_detail_modal_shows_prompt_for_completions():
    """LogDetailModal shows prompt field for /v1/completions entries."""
    app = HordeApp(config=make_config(), start_server=False)
    entry = make_entry(
        path="/v1/completions",
        prompt="Once upon a time",
        messages=None,
        response_text="there was a wizard.",
    )

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            await app.push_screen(LogDetailModal(entry))
            await pilot.pause()

            from textual.widgets import Static
            text = str(app.screen.query_one("#detail-content", Static).content)
            assert "Once upon a time" in text
            assert "there was a wizard." in text


@pytest.mark.asyncio
async def test_logs_screen_row_selected_opens_modal():
    """Selecting a row in LogsScreen pushes a LogDetailModal."""
    app = HordeApp(config=make_config(), start_server=False)
    entry = make_entry()
    app.request_log.append(entry)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()):
        async with app.run_test() as pilot:
            screen = LogsScreen()
            await app.push_screen(screen)
            await pilot.pause()

            # Simulate row selection by calling handler directly
            table = screen.query_one(DataTable)
            mock_event = type("RowSelected", (), {"cursor_row": 0})()
            screen.on_data_table_row_selected(mock_event)
            await pilot.pause()

            # A LogDetailModal should now be on the screen stack
            assert any(isinstance(s, LogDetailModal) for s in app.screen_stack)


@pytest.mark.asyncio
async def test_logs_screen_toggle_checked_action():
    """Pressing space toggles the checked flag and updates the table."""
    app = HordeApp(config=make_config(), start_server=False)
    entry = make_entry(checked=False)
    app.request_log.append(entry)

    with patch.object(HordeApp, "on_mount", new=AsyncMock()), \
         patch("app.tui.screens.logs.save_entries") as mock_save:
        async with app.run_test() as pilot:
            screen = LogsScreen()
            await app.push_screen(screen)
            await pilot.pause()

            table = screen.query_one(DataTable)
            # Coordinate(row, col) - checked is at column 0
            assert table.get_cell_at(Coordinate(0, 0)) == " "

            # Trigger toggle action by pressing space
            await pilot.press("space")
            await pilot.pause()

            assert entry.checked is True
            assert table.get_cell_at(Coordinate(0, 0)) == "*"
            mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# HistoryScreen is no longer accessible (removed)
# ---------------------------------------------------------------------------

def test_history_screen_not_in_app_screens():
    """'history' is not registered in HordeApp.SCREENS."""
    app = HordeApp(config=make_config(), start_server=False)
    assert "history" not in app.SCREENS


def test_no_history_binding():
    """HordeApp has no keybinding for history screen."""
    app = HordeApp(config=make_config(), start_server=False)
    bound_actions = [b[1] for b in app.BINDINGS]
    assert not any("history" in action for action in bound_actions)
