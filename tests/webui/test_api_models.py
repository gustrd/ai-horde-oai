"""Tests for /ui/api/models endpoints."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
import respx
from app.config import Settings
from app.horde.client import HordeClient
from app.horde.routing import ModelRouter
from app.main import create_app
from tests.webui.conftest import MODELS_FIXTURE, USER_FIXTURE


@asynccontextmanager
async def _filtered_client(extra_config: dict):
    """Spin up an app with custom config and yield an async HTTP client."""
    config = Settings(
        horde_api_key="filter-test-key",
        horde_api_url="https://aihorde.net/api",
        host="127.0.0.1",
        port=18001,
        **extra_config,
    )
    with respx.mock:
        respx.get("https://aihorde.net/api/v2/status/models").mock(
            return_value=httpx.Response(200, json=MODELS_FIXTURE)
        )
        respx.get("https://aihorde.net/api/v2/find_user").mock(
            return_value=httpx.Response(200, json=USER_FIXTURE)
        )
        respx.get("https://aihorde.net/api/v2/workers").mock(
            return_value=httpx.Response(200, json=[])
        )
        app = create_app(config)
        horde = HordeClient(
            base_url=config.horde_api_url,
            api_key=config.horde_api_key,
            client_agent=config.client_agent,
            global_min_request_delay=0,
        )
        app.state.horde = horde
        app.state.model_router = ModelRouter(config)
        app.state.request_log = []
        app.state.active_requests = []
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        await horde.close()


@pytest.mark.asyncio
async def test_models_returns_list(webui_client):
    r = await webui_client.get("/ui/api/models")
    assert r.status_code == 200
    models = r.json()
    assert isinstance(models, list)
    # Fixture has at least one model
    assert len(models) > 0
    first = models[0]
    for field in ["name", "count", "queued", "eta", "max_context_length", "max_length"]:
        assert field in first


@pytest.mark.asyncio
async def test_models_invalidate(webui_client):
    r = await webui_client.post("/ui/api/models/invalidate")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_models_set_default(webui_client, webui_app):
    r = await webui_client.post(
        "/ui/api/models/set-default",
        json={"model": "aphrodite/llama-3-8b"},
    )
    assert r.status_code == 200
    assert r.json()["default_model"] == "aphrodite/llama-3-8b"
    assert webui_app.state.config.default_model == "aphrodite/llama-3-8b"


@pytest.mark.asyncio
async def test_models_set_default_requires_model(webui_client):
    r = await webui_client.post("/ui/api/models/set-default", json={})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_models_blocklist_applied():
    """Models matching the blocklist substring must be excluded."""
    # Fixture has koboldcpp/yi-34b-200k — blocklist 'yi' should remove it.
    async with _filtered_client({"model_blocklist": ["yi"]}) as client:
        r = await client.get("/ui/api/models")
    assert r.status_code == 200
    names = [m["name"] for m in r.json()]
    assert not any("yi" in n.lower() for n in names)
    assert len(names) > 0  # other models still present


@pytest.mark.asyncio
async def test_models_whitelist_applied():
    """Only models matching the whitelist substring must be returned."""
    # Fixture has aphrodite/* and koboldcpp/* models.
    async with _filtered_client({"model_whitelist": ["aphrodite"]}) as client:
        r = await client.get("/ui/api/models")
    assert r.status_code == 200
    names = [m["name"] for m in r.json()]
    assert all("aphrodite" in n.lower() for n in names)
    assert len(names) > 0


@pytest.mark.asyncio
async def test_models_min_context_applied():
    """Models below min_context_length must be excluded."""
    # Fixture: aphrodite/llama-3.1-70b has 4096 ctx; others have >= 8192.
    async with _filtered_client({"model_min_context": 8192}) as client:
        r = await client.get("/ui/api/models")
    assert r.status_code == 200
    for m in r.json():
        assert m["max_context_length"] >= 8192
