"""End-to-end tests for the full request → mock Horde → response pipeline."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import Settings
from app.horde.client import HordeClient
from app.horde.routing import ModelRouter
from app.main import create_app

from tests.conftest import (
    GENERATE_FIXTURE,
    IMAGE_FIXTURE,
    MODELS_FIXTURE,
    USER_FIXTURE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(config: Settings):
    app = create_app(config)
    return app


def _init_state(app, config):
    """Manually set app state that lifespan would normally provide."""
    horde = HordeClient(
        base_url=config.horde_api_url,
        api_key=config.horde_api_key,
        client_agent=config.client_agent,
    )
    app.state.horde = horde
    app.state.model_router = ModelRouter(config)
    return horde


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_image_generation_url(test_config, respx_mock):
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/async").mock(
        return_value=httpx.Response(202, json={"id": "test-image-job-id"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/status/test-image-job-id").mock(
        return_value=httpx.Response(200, json=IMAGE_FIXTURE)
    )

    app = _make_app(test_config)
    horde = _init_state(app, test_config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/images/generations", json={
            "prompt": "A beautiful sunset over the ocean",
            "model": "dall-e-3",
            "size": "1024x1024",
            "response_format": "url",
        })
    await horde.close()

    assert r.status_code == 200
    data = r.json()
    assert "data" in data
    assert len(data["data"]) == 1
    assert data["data"][0]["url"] is not None
    assert data["data"][0]["b64_json"] is None


@pytest.mark.asyncio
async def test_image_generation_b64(test_config, respx_mock):
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/async").mock(
        return_value=httpx.Response(202, json={"id": "test-image-job-id"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/status/test-image-job-id").mock(
        return_value=httpx.Response(200, json=IMAGE_FIXTURE)
    )

    app = _make_app(test_config)
    horde = _init_state(app, test_config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/images/generations", json={
            "prompt": "A beautiful sunset",
            "response_format": "b64_json",
        })
    await horde.close()

    assert r.status_code == 200
    data = r.json()
    assert data["data"][0]["b64_json"] is not None
    assert data["data"][0]["url"] is None


# ---------------------------------------------------------------------------
# Response content validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_response_content(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "default",
        "messages": [{"role": "user", "content": "Hello!"}],
    })
    assert r.status_code == 200
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "Hello! I'm doing well. How can I help you today?"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["total_tokens"] == 15  # kudos from fixture


@pytest.mark.asyncio
async def test_chat_response_model_field(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "large",
        "messages": [{"role": "user", "content": "Hi"}],
    })
    assert r.status_code == 200
    # Model field in response must reflect the dummy alias, not real model
    assert r.json()["model"] == "large"


@pytest.mark.asyncio
async def test_legacy_completion_content(client):
    r = await client.post("/v1/completions", json={
        "model": "default",
        "prompt": "Once upon a time",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "text_completion"
    assert data["choices"][0]["text"] != ""
    assert "id" in data
    assert data["id"].startswith("cmpl-")


# ---------------------------------------------------------------------------
# Streaming content validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_streaming_chunks_have_content(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "default",
        "messages": [{"role": "user", "content": "Hello!"}],
        "stream": True,
    })
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]

    lines = r.text.strip().split("\n")
    data_lines = [l for l in lines if l.startswith("data:") and l.strip() != "data: [DONE]"]

    assert len(data_lines) >= 2  # at least role chunk + content chunk(s)

    # First chunk should have role=assistant
    first = json.loads(data_lines[0][len("data: "):])
    assert first["choices"][0]["delta"].get("role") == "assistant"

    # At least one chunk should have content
    content_chunks = [
        json.loads(l[len("data: "):])
        for l in data_lines[1:]
    ]
    all_content = "".join(c["choices"][0]["delta"].get("content") or "" for c in content_chunks)
    assert len(all_content) > 0


@pytest.mark.asyncio
async def test_streaming_ends_with_done(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "default",
        "messages": [{"role": "user", "content": "Hello!"}],
        "stream": True,
    })
    assert "data: [DONE]" in r.text


# ---------------------------------------------------------------------------
# Horde API error → OpenAI-format HTTP error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_horde_401_becomes_401(test_config, respx_mock):
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(401, json={"message": "Invalid API key"})
    )

    app = _make_app(test_config)
    horde = _init_state(app, test_config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "Hi"}],
        })
    await horde.close()

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_horde_429_becomes_429(test_config, respx_mock):
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(429, json={"message": "Rate limited"})
    )

    app = _make_app(test_config)
    horde = _init_state(app, test_config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "Hi"}],
        })
    await horde.close()

    assert r.status_code == 429


@pytest.mark.asyncio
async def test_horde_500_becomes_502(test_config, respx_mock):
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(500, json={"message": "Internal server error"})
    )

    app = _make_app(test_config)
    horde = _init_state(app, test_config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "Hi"}],
        })
    await horde.close()

    assert r.status_code == 502


# ---------------------------------------------------------------------------
# Job timeout → 504
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_job_timeout_returns_504(test_config, respx_mock):
    never_done = {
        "done": False, "faulted": False, "processing": 1,
        "waiting": 0, "finished": 0, "queue_position": 5,
        "wait_time": 60, "kudos": 0, "generations": [],
    }
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(202, json={"id": "timeout-job-id"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/timeout-job-id").mock(
        return_value=httpx.Response(200, json=never_done)
    )
    respx_mock.delete("https://aihorde.net/api/v2/generate/text/status/timeout-job-id").mock(
        return_value=httpx.Response(200, json={"message": "Cancelled"})
    )

    # Use a very short timeout so the test is fast
    config = Settings(
        horde_api_key="test-key-0000",
        horde_api_url="https://aihorde.net/api",
        default_model="aphrodite/llama-3.1-8b-instruct",
        retry={"max_retries": 0, "timeout_seconds": 0, "broaden_on_retry": False},
    )

    app = _make_app(config)
    horde = _init_state(app, config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "Hi"}],
        })
    await horde.close()

    assert r.status_code == 504


# ---------------------------------------------------------------------------
# System message in chat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_with_system_message(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "default",
        "messages": [
            {"role": "system", "content": "You are a pirate. Speak like one."},
            {"role": "user", "content": "Hello!"},
        ],
    })
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["role"] == "assistant"


@pytest.mark.asyncio
async def test_chat_with_conversation_history(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "default",
        "messages": [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "And 3+3?"},
        ],
    })
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] != ""


# ---------------------------------------------------------------------------
# Model blocklist: yi is blocked in test_config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_list_excludes_blocklisted(client):
    """The test_config has model_blocklist=['yi'], so yi-34b must not appear in resolved models."""
    r = await client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    # Dummy names won't include yi directly, but the real model yi-34b-200k
    # is blocked — it won't appear as a passthrough model either
    assert not any("yi" in m_id for m_id in ids)


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_missing_messages_returns_422(client):
    r = await client.post("/v1/chat/completions", json={
        "model": "default",
        # "messages" intentionally omitted
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_image_missing_prompt_returns_422(client):
    r = await client.post("/v1/images/generations", json={
        "model": "dall-e-3",
        # "prompt" intentionally omitted
    })
    assert r.status_code == 422
