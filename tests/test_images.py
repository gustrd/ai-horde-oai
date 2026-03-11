"""Integration tests for /v1/images/generations endpoint."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.config import RetrySettings
from tests.conftest import IMAGE_FIXTURE




def _fast_retry(max_retries=0, timeout_seconds=1):
    return RetrySettings(
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
        broaden_on_retry=False,
        backoff_base=0.0,
    )


async def test_image_generations_url(client):
    """Image generation with url format returns URL."""
    response = await client.post(
        "/v1/images/generations",
        json={"prompt": "A beautiful sunset", "response_format": "url"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 1
    assert data["data"][0]["url"] == IMAGE_FIXTURE["generations"][0]["img"]
    assert data["data"][0]["b64_json"] is None


async def test_image_generations_b64_json(client, respx_mock):
    """Image generation with b64_json format sets b64_json field."""
    b64_fixture = {
        **IMAGE_FIXTURE,
        "generations": [{**IMAGE_FIXTURE["generations"][0], "img": "base64datahere=="}],
    }
    respx_mock.get("https://aihorde.net/api/v2/generate/status/test-image-job-id").mock(
        return_value=httpx.Response(200, json=b64_fixture)
    )
    response = await client.post(
        "/v1/images/generations",
        json={"prompt": "A cat", "response_format": "b64_json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["data"][0]["b64_json"] == "base64datahere=="
    assert data["data"][0]["url"] is None


async def test_image_generations_default_format_is_url(client):
    """Default response_format is url."""
    response = await client.post(
        "/v1/images/generations",
        json={"prompt": "A dog"},
    )
    assert response.status_code == 200
    assert response.json()["data"][0]["url"] is not None


async def test_image_generations_horde_submit_error(app, client, respx_mock):
    """Horde 500 on submit returns 502."""
    respx_mock.post("https://aihorde.net/api/v2/generate/async").mock(
        return_value=httpx.Response(500, json={"message": "Horde error"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=0)}
    )
    response = await client.post(
        "/v1/images/generations",
        json={"prompt": "A cat"},
    )
    assert response.status_code == 502


async def test_image_generations_timeout(app, client, respx_mock):
    """Image generation timeout returns 504."""
    pending = {
        "done": False, "faulted": False, "processing": 1, "waiting": 0,
        "finished": 0, "queue_position": 3, "wait_time": 999, "kudos": 0,
        "generations": [], "is_possible": True,
    }
    respx_mock.get("https://aihorde.net/api/v2/generate/status/test-image-job-id").mock(
        return_value=httpx.Response(200, json=pending)
    )
    respx_mock.delete("https://aihorde.net/api/v2/generate/status/test-image-job-id").mock(
        return_value=httpx.Response(200, json={"message": "Cancelled"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=0, timeout_seconds=0)}
    )
    response = await client.post(
        "/v1/images/generations",
        json={"prompt": "A sunset"},
    )
    assert response.status_code == 504


async def test_image_generations_faulted(app, client, respx_mock):
    """Faulted image job returns 504."""
    faulted = {
        "done": False, "faulted": True, "processing": 0, "waiting": 0,
        "finished": 0, "queue_position": None, "wait_time": 0, "kudos": 0,
        "generations": [], "is_possible": True,
    }
    respx_mock.get("https://aihorde.net/api/v2/generate/status/test-image-job-id").mock(
        return_value=httpx.Response(200, json=faulted)
    )
    respx_mock.delete("https://aihorde.net/api/v2/generate/status/test-image-job-id").mock(
        return_value=httpx.Response(200, json={"message": "Cancelled"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=0, timeout_seconds=10)}
    )
    response = await client.post(
        "/v1/images/generations",
        json={"prompt": "A landscape"},
    )
    assert response.status_code == 504


async def test_image_generations_has_created_timestamp(client):
    """Response includes created timestamp."""
    response = await client.post(
        "/v1/images/generations",
        json={"prompt": "Sunset"},
    )
    assert response.status_code == 200
    assert isinstance(response.json()["created"], int)
