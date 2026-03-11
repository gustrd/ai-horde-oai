"""Tests for HordeClient model caching and error handling."""
from __future__ import annotations

import time

import httpx
import pytest
import respx

from app.horde.client import HordeClient, HordeError
from tests.conftest import MODELS_FIXTURE, USER_FIXTURE


BASE_URL = "https://aihorde.net/api"


@pytest.fixture
async def horde_client():
    client = HordeClient(
        base_url=BASE_URL,
        api_key="test-key",
        client_agent="test/1.0",
        model_cache_ttl=60,
    )
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_get_models_returns_list(horde_client, respx_mock):
    """get_models() fetches and parses models."""
    respx_mock.get(f"{BASE_URL}/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    models = await horde_client.get_models()
    assert len(models) == len(MODELS_FIXTURE)
    assert models[0].name == MODELS_FIXTURE[0]["name"]


@pytest.mark.asyncio
async def test_get_models_cached(horde_client, respx_mock):
    """Second call to get_models() uses cache without hitting network."""
    route = respx_mock.get(f"{BASE_URL}/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    await horde_client.get_models()
    await horde_client.get_models()
    # Should only have called the API once
    assert route.called
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_get_models_cache_expired(horde_client, respx_mock):
    """Cache miss after TTL triggers new API call."""
    route = respx_mock.get(f"{BASE_URL}/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    horde_client._model_cache_ttl = 0  # zero TTL → always expired
    horde_client._model_cache_expires = 0.0  # force expired

    await horde_client.get_models()
    horde_client._model_cache_expires = 0.0  # expire again
    await horde_client.get_models()

    assert route.call_count == 2


@pytest.mark.asyncio
async def test_invalidate_model_cache(horde_client, respx_mock):
    """invalidate_model_cache() forces the next call to re-fetch."""
    route = respx_mock.get(f"{BASE_URL}/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    await horde_client.get_models()
    horde_client.invalidate_model_cache()
    await horde_client.get_models()

    assert route.call_count == 2


@pytest.mark.asyncio
async def test_horde_error_on_401(horde_client, respx_mock):
    """401 response raises HordeError with correct status code."""
    respx_mock.get(f"{BASE_URL}/v2/status/models").mock(
        return_value=httpx.Response(401, json={"message": "Invalid key"})
    )
    with pytest.raises(HordeError) as exc_info:
        await horde_client.get_models()
    assert exc_info.value.status_code == 401
    assert "Invalid key" in exc_info.value.message


@pytest.mark.asyncio
async def test_horde_error_on_500(horde_client, respx_mock):
    """500 response raises HordeError."""
    respx_mock.get(f"{BASE_URL}/v2/status/models").mock(
        return_value=httpx.Response(500, json={"message": "Server error"})
    )
    with pytest.raises(HordeError) as exc_info:
        await horde_client.get_models()
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_cancel_silently_ignores_error(horde_client, respx_mock):
    """cancel_text_job() does not raise even when the request fails."""
    respx_mock.delete(f"{BASE_URL}/v2/generate/text/status/bad-id").mock(
        return_value=httpx.Response(404, json={"message": "Not found"})
    )
    # Should not raise
    await horde_client.cancel_text_job("bad-id")


@pytest.mark.asyncio
async def test_get_user(horde_client, respx_mock):
    """get_user() returns a HordeUser object."""
    respx_mock.get(f"{BASE_URL}/v2/find_user").mock(
        return_value=httpx.Response(200, json=USER_FIXTURE)
    )
    user = await horde_client.get_user()
    assert user.kudos == USER_FIXTURE["kudos"]
    assert user.username == USER_FIXTURE["username"]


def test_ban_model_removes_from_cache(horde_client):
    """ban_model() removes the model from both in-memory caches."""
    from app.schemas.horde import HordeModel
    m1 = HordeModel(name="dead/model", count=0)
    m2 = HordeModel(name="alive/model", count=2)
    horde_client._model_cache = [m1, m2]
    horde_client._enriched_cache = [m1, m2]

    horde_client.ban_model("dead/model", duration=3600)

    assert all(m.name != "dead/model" for m in horde_client._model_cache)
    assert all(m.name != "dead/model" for m in horde_client._enriched_cache)
    assert "dead/model" in horde_client._banned_models


def test_ban_model_filtered_from_get_models_cache(horde_client):
    """_filter_banned() excludes banned models from returned list."""
    from app.schemas.horde import HordeModel
    m1 = HordeModel(name="dead/model", count=0)
    m2 = HordeModel(name="alive/model", count=2)
    horde_client._banned_models["dead/model"] = time.monotonic() + 3600

    result = horde_client._filter_banned([m1, m2])
    assert len(result) == 1
    assert result[0].name == "alive/model"


def test_ban_model_expires(horde_client):
    """Expired bans are cleaned up and model reappears."""
    from app.schemas.horde import HordeModel
    m = HordeModel(name="model/x", count=1)
    horde_client._banned_models["model/x"] = time.monotonic() - 1  # already expired

    result = horde_client._filter_banned([m])
    assert len(result) == 1
    assert "model/x" not in horde_client._banned_models
