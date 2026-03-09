"""Integration tests for /v1/models endpoint."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_list_models(client):
    """GET /v1/models returns all configured aliases."""
    response = await client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    # Built-in aliases
    assert "best" in ids
    assert "fast" in ids
    assert "default" in ids
    # Configured alias from test_config
    assert "large" in ids


async def test_list_models_fields(client):
    """Each model card has required fields."""
    response = await client.get("/v1/models")
    for card in response.json()["data"]:
        assert "id" in card
        assert card["object"] == "model"
        assert "owned_by" in card


async def test_get_model_found(client):
    """GET /v1/models/{id} returns a single model card."""
    response = await client.get("/v1/models/best")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "best"
    assert data["object"] == "model"


async def test_get_model_not_found(client):
    """GET /v1/models/{id} for unknown model returns 404."""
    response = await client.get("/v1/models/nonexistent-model-xyz")
    assert response.status_code == 404


async def test_real_horde_names_not_exposed(client):
    """Real Horde model names are not in the public model list."""
    response = await client.get("/v1/models")
    ids = [m["id"] for m in response.json()["data"]]
    # These are real Horde names that should not be exposed
    assert "aphrodite/llama-3.1-8b-instruct" not in ids
    assert "aphrodite/llama-3.1-70b-instruct" not in ids
