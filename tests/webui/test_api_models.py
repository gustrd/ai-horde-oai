"""Tests for /ui/api/models endpoints."""
from __future__ import annotations

import pytest


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
