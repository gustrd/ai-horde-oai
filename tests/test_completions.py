"""Integration tests for /v1/completions endpoint."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.config import RetrySettings
from tests.conftest import GENERATE_FIXTURE




def _fast_retry():
    return RetrySettings(max_retries=0, timeout_seconds=10, broaden_on_retry=False, backoff_base=0.0)


async def test_completions_basic(client):
    """Basic completion returns a valid response."""
    response = await client.post(
        "/v1/completions",
        json={"model": "best", "prompt": "Once upon a time"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "text_completion"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["text"] == GENERATE_FIXTURE["generations"][0]["text"]
    assert data["choices"][0]["finish_reason"] == "stop"


async def test_completions_stream_rejected(client):
    """stream=True is rejected with a 400 and a clear error message."""
    response = await client.post(
        "/v1/completions",
        json={"model": "best", "prompt": "Hello", "stream": True},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"]["type"] == "invalid_request_error"
    assert "streaming" in detail["error"]["message"].lower()


async def test_completions_model_alias(client):
    """Model alias resolves correctly."""
    response = await client.post(
        "/v1/completions",
        json={"model": "large", "prompt": "Hello"},
    )
    assert response.status_code == 200


async def test_completions_horde_error_429(app, client, respx_mock):
    """Horde 429 maps to 429 with rate_limit_error type."""
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(429, json={"message": "Too many requests"})
    )
    app.state.config = app.state.config.model_copy(update={"retry": _fast_retry()})
    response = await client.post(
        "/v1/completions",
        json={"model": "best", "prompt": "Hello"},
    )
    assert response.status_code == 429


async def test_completions_prompt_list(client):
    """Accepts prompt as a list (uses first element)."""
    response = await client.post(
        "/v1/completions",
        json={"model": "best", "prompt": ["Hello", "World"]},
    )
    assert response.status_code == 200


async def test_completions_model_in_response(client):
    """Response model field matches requested alias."""
    response = await client.post(
        "/v1/completions",
        json={"model": "best", "prompt": "Test"},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "best"
