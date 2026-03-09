"""Integration tests: middleware + router log_extras capture for all API routes."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import Settings
from app.horde.client import HordeClient
from app.horde.routing import ModelRouter
from app.log_store import RequestLogEntry
from app.main import create_app

from tests.conftest import GENERATE_FIXTURE, IMAGE_FIXTURE, MODELS_FIXTURE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(config: Settings, request_log: list | None = None) -> tuple:
    """Create FastAPI app with optional shared request_log. Returns (app, horde)."""
    app = create_app(config)
    horde = HordeClient(
        base_url=config.horde_api_url,
        api_key=config.horde_api_key,
        client_agent=config.client_agent,
    )
    app.state.horde = horde
    app.state.model_router = ModelRouter(config)
    if request_log is not None:
        app.state.request_log = request_log
    return app, horde


# ---------------------------------------------------------------------------
# Chat completions (non-streaming)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_non_streaming_logs_entry(test_config, respx_mock):
    """Non-streaming chat request produces a complete RequestLogEntry."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(202, json={"id": "job-1"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/job-1").mock(
        return_value=httpx.Response(200, json={
            "done": True,
            "kudos": 5,
            "generations": [{"text": "Paris!", "worker_name": "WorkerX",
                              "worker_id": "wid-x", "kudos": 5}],
        })
    )

    request_log: list[RequestLogEntry] = []
    app, horde = _make_app(test_config, request_log)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
        })

    await horde.close()

    assert r.status_code == 200
    assert len(request_log) == 1
    entry = request_log[0]
    assert entry.path == "/v1/chat/completions"
    assert entry.method == "POST"
    assert entry.status == 200
    assert entry.duration > 0
    assert entry.worker == "WorkerX"
    assert entry.worker_id == "wid-x"
    assert entry.kudos == 5.0
    assert entry.response_text == "Paris!"
    assert entry.messages is not None
    assert entry.messages[0]["role"] == "user"
    assert entry.messages[0]["content"] == "What is the capital of France?"
    assert entry.error == ""


@pytest.mark.asyncio
async def test_chat_non_streaming_logs_model_alias(test_config, respx_mock):
    """Log entry records both the alias (model) and resolved name (real_model)."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(202, json={"id": "job-2"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/job-2").mock(
        return_value=httpx.Response(200, json=GENERATE_FIXTURE)
    )

    request_log: list[RequestLogEntry] = []
    app, horde = _make_app(test_config, request_log)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/v1/chat/completions", json={
            "model": "large",  # alias from test_config
            "messages": [{"role": "user", "content": "Hi"}],
        })

    await horde.close()

    assert len(request_log) == 1
    entry = request_log[0]
    assert entry.model == "large"
    assert entry.real_model == "aphrodite/llama-3.1-70b-instruct"


@pytest.mark.asyncio
async def test_chat_non_streaming_horde_error_logs_entry(test_config, respx_mock):
    """A Horde API error still logs an entry with error field set."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(401, json={"message": "Invalid API key"})
    )

    request_log: list[RequestLogEntry] = []
    app, horde = _make_app(test_config, request_log)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "Hi"}],
        })

    await horde.close()

    assert r.status_code == 401
    assert len(request_log) == 1
    entry = request_log[0]
    assert entry.status == 401
    assert entry.error != ""


# ---------------------------------------------------------------------------
# Chat completions (streaming)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_streaming_logs_entry(test_config, respx_mock):
    """Streaming chat request produces a log entry when the stream is consumed."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(202, json={"id": "stream-job"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/stream-job").mock(
        return_value=httpx.Response(200, json={
            "done": True,
            "kudos": 8,
            "generations": [{"text": "Streaming response!", "worker_name": "StreamWorker",
                              "worker_id": "swid", "kudos": 8}],
        })
    )

    request_log: list[RequestLogEntry] = []
    app, horde = _make_app(test_config, request_log)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "stream this"}],
            "stream": True,
        })
        # Fully consume the stream so the generator's finally block runs
        _ = r.text

    await horde.close()

    assert r.status_code == 200
    assert len(request_log) == 1
    entry = request_log[0]
    assert entry.path == "/v1/chat/completions"
    assert entry.worker == "StreamWorker"
    assert entry.kudos == 8.0
    assert "Streaming response!" in entry.response_text
    assert entry.messages is not None
    assert entry.messages[0]["content"] == "stream this"


@pytest.mark.asyncio
async def test_chat_streaming_not_double_logged(test_config, respx_mock):
    """Streaming requests are NOT logged by the middleware AND the generator (only generator)."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(202, json={"id": "nodup-job"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/nodup-job").mock(
        return_value=httpx.Response(200, json=GENERATE_FIXTURE)
    )

    request_log: list[RequestLogEntry] = []
    app, horde = _make_app(test_config, request_log)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        _ = r.text  # consume stream

    await horde.close()

    # Exactly one entry — not two
    assert len(request_log) == 1


# ---------------------------------------------------------------------------
# Completions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completions_logs_entry(test_config, respx_mock):
    """/v1/completions logs an entry with prompt and response_text."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(202, json={"id": "comp-job"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/comp-job").mock(
        return_value=httpx.Response(200, json={
            "done": True,
            "kudos": 3,
            "generations": [{"text": "a time.", "worker_name": "W2", "worker_id": "w2id", "kudos": 3}],
        })
    )

    request_log: list[RequestLogEntry] = []
    app, horde = _make_app(test_config, request_log)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/completions", json={
            "model": "default",
            "prompt": "Once upon",
        })

    await horde.close()

    assert r.status_code == 200
    assert len(request_log) == 1
    entry = request_log[0]
    assert entry.path == "/v1/completions"
    assert entry.prompt == "Once upon"
    assert entry.response_text == "a time."
    assert entry.worker == "W2"
    assert entry.kudos == 3.0


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_image_generation_logs_entry(test_config, respx_mock):
    """/v1/images/generations logs an entry with prompt and model."""
    respx_mock.post("https://aihorde.net/api/v2/generate/async").mock(
        return_value=httpx.Response(202, json={"id": "img-job"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/status/img-job").mock(
        return_value=httpx.Response(200, json=IMAGE_FIXTURE)
    )

    request_log: list[RequestLogEntry] = []
    app, horde = _make_app(test_config, request_log)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/images/generations", json={
            "prompt": "A dragon flying over mountains",
        })

    await horde.close()

    assert r.status_code == 200
    assert len(request_log) == 1
    entry = request_log[0]
    assert entry.path == "/v1/images/generations"
    assert entry.prompt == "A dragon flying over mountains"
    assert entry.model != ""


# ---------------------------------------------------------------------------
# No request_log on app.state → middleware is silent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_request_log_does_not_crash(test_config, respx_mock):
    """Middleware does not crash if app.state.request_log is not set."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(202, json={"id": "silent-job"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/silent-job").mock(
        return_value=httpx.Response(200, json=GENERATE_FIXTURE)
    )

    # Deliberately do NOT set request_log on app.state
    app, horde = _make_app(test_config, request_log=None)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "hi"}],
        })

    await horde.close()
    assert r.status_code == 200  # request succeeds even without logging


# ---------------------------------------------------------------------------
# /health is NOT logged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_endpoint_not_logged(test_config):
    """The /health endpoint is not added to request_log."""
    request_log: list[RequestLogEntry] = []
    app, horde = _make_app(test_config, request_log)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/health")

    await horde.close()

    assert r.status_code == 200
    assert len(request_log) == 0


# ---------------------------------------------------------------------------
# log_callback is called for new entries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_callback_invoked(test_config, respx_mock):
    """app.state.log_callback is called with each new RequestLogEntry."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(202, json={"id": "cb-job"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/cb-job").mock(
        return_value=httpx.Response(200, json=GENERATE_FIXTURE)
    )

    callback_entries: list[RequestLogEntry] = []
    request_log: list[RequestLogEntry] = []
    app, horde = _make_app(test_config, request_log)
    app.state.log_callback = callback_entries.append
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/v1/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "callback test"}],
        })

    await horde.close()

    assert len(callback_entries) == 1
    assert callback_entries[0] is request_log[0]
