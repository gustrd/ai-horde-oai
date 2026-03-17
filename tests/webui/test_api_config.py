"""Tests for /ui/api/config endpoints."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_config_returns_masked_key(webui_client):
    r = await webui_client.get("/ui/api/config")
    assert r.status_code == 200
    data = r.json()
    key = data["horde_api_key"]
    assert "test" in key or "*" in key
    # Must not return the raw key
    assert "test-webui-key" not in key


@pytest.mark.asyncio
async def test_get_config_shape(webui_client):
    r = await webui_client.get("/ui/api/config")
    data = r.json()
    for field in ["horde_api_url", "host", "port", "default_model", "retry"]:
        assert field in data


@pytest.mark.asyncio
async def test_put_config_saves(webui_client, webui_app, tmp_path):
    r = await webui_client.put(
        "/ui/api/config",
        json={"default_model": "best", "port": 18001},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert webui_app.state.config.default_model == "best"
    assert webui_app.state.config.port == 18001


@pytest.mark.asyncio
async def test_put_config_masked_key_ignored(webui_client, webui_app):
    """Posting a masked key should not overwrite the real key."""
    r = await webui_client.put(
        "/ui/api/config",
        json={"horde_api_key": "test****key"},
    )
    assert r.status_code == 200
    # Real key unchanged
    assert webui_app.state.config.horde_api_key == "test-webui-key"


@pytest.mark.asyncio
async def test_put_config_invalid_client_agent(webui_client):
    r = await webui_client.put(
        "/ui/api/config",
        json={"client_agent": "bad-format"},
    )
    assert r.status_code == 422
