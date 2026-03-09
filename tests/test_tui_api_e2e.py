"""End-to-end tests for TUI interacting with the API server."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from textual.widgets import Label, Select

from app.main import create_app
from app.tui.app import HordeApp
from app.tui.screens.chat import ChatScreen
from app.horde.client import HordeClient
from app.horde.routing import ModelRouter


@pytest.mark.asyncio
async def test_tui_to_api_chat_flow(test_config, respx_mock, tmp_path):
    """TUI chat screen sends request to in-process server; entry appears in request_log."""
    server_config = test_config.model_copy(update={"port": 8001, "host": "127.0.0.1"})

    # Create the proxy app (FastAPI) and init its state manually (mimics lifespan)
    proxy_app = create_app(server_config)
    horde_client = HordeClient(
        base_url=server_config.horde_api_url,
        api_key=server_config.horde_api_key,
        client_agent=server_config.client_agent,
    )
    proxy_app.state.horde = horde_client
    proxy_app.state.model_router = ModelRouter(server_config)

    try:
        # Route TUI's HTTP calls to the FastAPI app via ASGITransport
        async def proxy_handler(request):
            return await httpx.ASGITransport(app=proxy_app).handle_async_request(request)

        respx_mock.route(host=server_config.host, port=server_config.port).mock(
            side_effect=proxy_handler
        )

        # Mock the Horde API
        respx_mock.get(url__regex=r".*/v2/status/models").mock(
            return_value=httpx.Response(200, json=[
                {"name": "test-model", "performance": 1.0, "queued": 0, "eta": 0,
                 "threads": 1, "max_context_length": 4096, "max_length": 512}
            ])
        )
        respx_mock.post(url__regex=r".*/v2/generate/text/async").mock(
            return_value=httpx.Response(202, json={"id": "test-job-id"})
        )
        respx_mock.get(url__regex=r".*/v2/generate/text/status/test-job-id").mock(
            return_value=httpx.Response(200, json={
                "done": True,
                "generations": [{"text": "Hello from mock Horde!", "worker_name": "w1",
                                  "worker_id": "wid1", "kudos": 10}],
                "kudos": 10,
            })
        )
        respx_mock.get(url__regex=r".*/v2/find_user").mock(
            return_value=httpx.Response(200, json={"username": "testuser", "kudos": 5000})
        )

        # Wire the proxy_app's log_store to the TUI's request_log
        tui_request_log = []
        proxy_app.state.request_log = tui_request_log

        # Initialize TUI with start_server=False (proxy_app is wired manually above)
        app = HordeApp(config=server_config, start_server=False)
        app.request_log = tui_request_log

        with patch("app.tui.screens.chat.Path.home", return_value=tmp_path):
            async with app.run_test() as pilot:
                screen = ChatScreen()
                await app.push_screen(screen)
                await pilot.pause()

                select = screen.query_one("#model-select", Select)
                select.set_options([("test-model", "test-model")])
                select.value = "test-model"

                await pilot.click("#message-input")
                for char in "Hello API":
                    await pilot.press(char)
                await pilot.click("#send-btn")
                await pilot.pause(0.5)

                status = screen.query_one("#status", Label)
                assert "Done" in str(status.content)

                # History still saved to disk
                history_dir = tmp_path / ".ai-horde-oai" / "history"
                assert history_dir.exists()
                assert len(list(history_dir.glob("*.json"))) == 1

                # Log entry created by middleware (not chat.py finally block)
                assert len(app.request_log) == 1
                entry = app.request_log[0]
                assert entry.status == 200
                assert entry.path == "/v1/chat/completions"
                assert entry.worker == "w1"
                assert entry.kudos == 10.0

    finally:
        await horde_client.close()
