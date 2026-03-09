"""Integration tests for /v1/chat/completions endpoint."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import RetrySettings
from tests.conftest import GENERATE_FIXTURE, MODELS_FIXTURE, USER_FIXTURE


pytestmark = pytest.mark.asyncio


def _fast_retry(max_retries=0, timeout_seconds=1):
    return RetrySettings(
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
        broaden_on_retry=False,
        backoff_base=0.0,
    )


async def test_chat_completions_basic(client):
    """Non-streaming chat completion returns a valid OpenAI response."""
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Hello!"}],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] == GENERATE_FIXTURE["generations"][0]["text"]
    assert data["choices"][0]["finish_reason"] == "stop"
    assert "usage" in data


async def test_chat_completions_model_alias(client):
    """Model alias 'large' resolves correctly and response model is the alias."""
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "large",
            "messages": [{"role": "user", "content": "Hello!"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["model"] == "large"


async def test_chat_completions_with_system_message(client):
    """System message is included in the request."""
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hi!"},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"


async def test_chat_completions_model_not_found(app, client, respx_mock):
    """Unknown model that can't be resolved returns 404 when models list is empty."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=[])
    )
    app.state.horde.invalidate_model_cache()

    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Hello!"}],
        },
    )
    assert response.status_code == 404


async def test_chat_completions_horde_401(app, client, respx_mock):
    """Horde 401 on model fetch maps to 401 with authentication_error type."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(401, json={"message": "Invalid API key"})
    )
    app.state.horde.invalidate_model_cache()

    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Hello!"}],
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"]["error"]["type"] == "authentication_error"


async def test_chat_completions_horde_submit_500(app, client, respx_mock):
    """Horde 500 on job submit maps to 502 with server_error type."""
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(500, json={"message": "Internal Server Error"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=0)}
    )
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Hello!"}],
        },
    )
    assert response.status_code == 502
    assert response.json()["detail"]["error"]["type"] == "server_error"


async def test_chat_completions_timeout(app, client, respx_mock):
    """Job that never completes within timeout raises 504."""
    pending_status = {
        "done": False, "faulted": False, "processing": 1, "waiting": 0,
        "finished": 0, "queue_position": 5, "wait_time": 999, "kudos": 0,
        "generations": [], "is_possible": True,
    }
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json=pending_status)
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=0, timeout_seconds=0)}
    )
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Hello!"}],
        },
    )
    assert response.status_code == 504


async def test_chat_completions_faulted_job(app, client, respx_mock):
    """Faulted job with no retries returns 504."""
    faulted = {
        "done": False, "faulted": True, "processing": 0, "waiting": 0,
        "finished": 0, "queue_position": None, "wait_time": 0, "kudos": 0,
        "generations": [], "is_possible": True,
    }
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json=faulted)
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=0, timeout_seconds=10)}
    )
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Hello!"}],
        },
    )
    assert response.status_code == 504


async def test_chat_completions_streaming(client):
    """Streaming response returns SSE chunks ending with [DONE]."""
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Hello!"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        lines = []
        async for line in response.aiter_lines():
            lines.append(line)

    data_lines = [l for l in lines if l.startswith("data: ") and l != "data: [DONE]"]
    assert len(data_lines) >= 2

    first = json.loads(data_lines[0][6:])
    assert first["choices"][0]["delta"].get("role") == "assistant"

    last = json.loads(data_lines[-1][6:])
    assert last["choices"][0]["finish_reason"] == "stop"

    assert "data: [DONE]" in lines


async def test_streaming_worker_comment_emitted(client):
    """SSE stream includes x-horde-worker comment with worker name, id, model, kudos."""
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Hello!"}],
            "stream": True,
        },
    ) as response:
        lines = [l async for l in response.aiter_lines()]

    worker_lines = [l for l in lines if l.startswith(": x-horde-worker")]
    assert len(worker_lines) == 1, f"Expected 1 worker comment, got: {worker_lines}"

    wl = worker_lines[0]
    # Fixture has worker_name=gpu-node-7, worker_id=worker-abc-123, kudos=15.0
    assert "gpu-node-7" in wl
    assert "worker-abc-123" in wl
    assert "15.0" in wl


async def test_streaming_chunks_use_actual_model(client):
    """Content chunks carry actual Horde model name, not the alias."""
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    ) as response:
        lines = [l async for l in response.aiter_lines()]

    content_chunks = [
        json.loads(l[6:])
        for l in lines
        if l.startswith("data: ") and l != "data: [DONE]"
        and json.loads(l[6:])["choices"][0]["delta"].get("content")
    ]
    assert content_chunks, "Expected at least one content chunk"
    # All content chunks should carry the real model from the fixture
    fixture_model = GENERATE_FIXTURE["generations"][0]["model"]
    assert all(c["model"] == fixture_model for c in content_chunks)


async def test_health_endpoint(client):
    """Health endpoint returns ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
