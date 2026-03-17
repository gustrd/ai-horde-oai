"""Integration tests for the web UI: full app startup, round-trips, WebSocket."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from app.config import Settings
from app.horde.client import HordeClient
from app.horde.routing import ModelRouter
from app.main import create_app
from tests.webui.conftest import MODELS_FIXTURE, USER_FIXTURE, make_log_entry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def int_config() -> Settings:
    return Settings(
        horde_api_key="int-test-key",
        horde_api_url="https://aihorde.net/api",
        host="127.0.0.1",
        port=19000,
    )


@pytest.fixture
def int_app(int_config, respx_mock):
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.get("https://aihorde.net/api/v2/find_user").mock(
        return_value=httpx.Response(200, json=USER_FIXTURE)
    )
    respx_mock.get("https://aihorde.net/api/v2/workers").mock(
        return_value=httpx.Response(200, json=[])
    )
    return create_app(int_config)


@pytest.fixture
async def int_client(int_app, int_config):
    horde = HordeClient(
        base_url=int_config.horde_api_url,
        api_key=int_config.horde_api_key,
        client_agent=int_config.client_agent,
        global_min_request_delay=0,
    )
    int_app.state.horde = horde
    int_app.state.model_router = ModelRouter(int_config)
    int_app.state.request_log = []
    int_app.state.active_requests = []
    transport = httpx.ASGITransport(app=int_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await horde.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_serves_html(int_client):
    """GET /ui/ returns HTML page."""
    r = await int_client.get("/ui/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "ai-horde-oai" in r.text


@pytest.mark.asyncio
async def test_config_round_trip(int_client, int_app):
    """GET config -> PUT change -> GET verify."""
    r1 = await int_client.get("/ui/api/config")
    assert r1.status_code == 200

    r2 = await int_client.put("/ui/api/config", json={"default_model": "round-trip-model"})
    assert r2.status_code == 200

    r3 = await int_client.get("/ui/api/config")
    assert r3.json()["default_model"] == "round-trip-model"

    # App state updated
    assert int_app.state.config.default_model == "round-trip-model"


@pytest.mark.asyncio
async def test_log_lifecycle(int_client, int_app):
    """Add log entry -> verify it appears in GET /ui/api/logs."""
    assert (await int_client.get("/ui/api/logs")).json() == []

    entry = make_log_entry(response_text="integration-test")
    int_app.state.request_log.append(entry)

    r = await int_client.get("/ui/api/logs")
    logs = r.json()
    assert len(logs) == 1
    assert logs[0]["response_text"] == "integration-test"


@pytest.mark.asyncio
async def test_log_clear_lifecycle(int_client, int_app):
    """Add logs -> clear -> verify empty."""
    int_app.state.request_log.extend([make_log_entry(), make_log_entry()])
    assert len((await int_client.get("/ui/api/logs")).json()) == 2

    r = await int_client.delete("/ui/api/logs")
    assert r.status_code == 200
    assert (await int_client.get("/ui/api/logs")).json() == []


@pytest.mark.asyncio
async def test_set_default_model_cross_feature(int_client, int_app):
    """Set default model via models endpoint -> config is updated."""
    r = await int_client.post(
        "/ui/api/models/set-default",
        json={"model": "aphrodite/some-model"},
    )
    assert r.status_code == 200
    assert int_app.state.config.default_model == "aphrodite/some-model"

    # Config endpoint reflects the change
    r2 = await int_client.get("/ui/api/config")
    assert r2.json()["default_model"] == "aphrodite/some-model"


@pytest.mark.asyncio
async def test_dashboard_reflects_request_log(int_client, int_app):
    """Dashboard session_kudos sums up request log kudos."""
    int_app.state.request_log.append(make_log_entry(kudos=10.0))
    int_app.state.request_log.append(make_log_entry(kudos=20.0))

    r = await int_client.get("/ui/api/dashboard")
    assert r.json()["session_kudos"] == 30.0
    assert r.json()["request_count"] == 2


def test_websocket_connect_disconnect(int_app, int_config):
    """WebSocket endpoint accepts connection."""
    from starlette.testclient import TestClient
    from app.horde.client import HordeClient
    from app.horde.routing import ModelRouter
    from app.webui.ws import ws_manager

    horde = HordeClient(
        base_url=int_config.horde_api_url,
        api_key=int_config.horde_api_key,
        client_agent=int_config.client_agent,
        global_min_request_delay=0,
    )
    int_app.state.horde = horde
    int_app.state.model_router = ModelRouter(int_config)
    int_app.state.request_log = []
    int_app.state.active_requests = []

    with TestClient(int_app) as client:
        with client.websocket_connect("/ui/ws"):
            assert len(ws_manager._connections) >= 1
    # After context, connection is removed
    assert len(ws_manager._connections) == 0


def test_websocket_receives_broadcast(int_app, int_config):
    """Broadcast from ws_manager reaches connected client."""
    import asyncio
    from starlette.testclient import TestClient
    from app.horde.client import HordeClient
    from app.horde.routing import ModelRouter
    from app.webui.ws import ws_manager

    horde = HordeClient(
        base_url=int_config.horde_api_url,
        api_key=int_config.horde_api_key,
        client_agent=int_config.client_agent,
        global_min_request_delay=0,
    )
    int_app.state.horde = horde
    int_app.state.model_router = ModelRouter(int_config)
    int_app.state.request_log = []
    int_app.state.active_requests = []

    with TestClient(int_app) as client:
        with client.websocket_connect("/ui/ws") as ws:
            asyncio.get_event_loop().run_until_complete(
                ws_manager.broadcast({"type": "test_event", "data": {"x": 42}})
            )
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "test_event"
            assert msg["data"]["x"] == 42
