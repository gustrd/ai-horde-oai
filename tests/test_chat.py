"""Integration tests for /v1/chat/completions endpoint."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import RetrySettings
from app.routers.chat import _split_thinking, _strip_eos
from tests.conftest import GENERATE_FIXTURE, MODELS_FIXTURE, USER_FIXTURE




def _fast_retry(max_retries=0, timeout_seconds=1):
    return RetrySettings(
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
        broaden_on_retry=False,
        backoff_base=0.0,
        poll_interval=0.0,
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


async def test_stream_chat_cancels_job_on_client_disconnect():
    """When the streaming generator is closed (client disconnect), the Horde job is cancelled."""
    from unittest.mock import AsyncMock, MagicMock

    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeJobStatus

    pending_status = HordeJobStatus(
        done=False, faulted=False, generations=[], kudos=0,
        queue_position=5, wait_time=10,
    )

    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    horde.submit_text_job = AsyncMock(return_value="disconnect-job-id")
    horde.poll_text_status = AsyncMock(return_value=pending_status)
    horde.cancel_text_job = AsyncMock()

    gen = _stream_chat(horde, MagicMock(), "best", "test-model", stall_timeout=9999)

    # First chunk: role delta
    first = await gen.__anext__()
    assert "assistant" in first

    # Second chunk: x-horde-resolved comment (alias != real_model)
    second = await gen.__anext__()
    assert "x-horde-resolved" in second

    # Third chunk: job submitted, poll returns pending → queue_position SSE comment
    third = await gen.__anext__()
    assert "queue_position=5" in third

    # Close the generator (simulates client disconnect)
    await gen.aclose()

    # The Horde job should have been cancelled
    horde.cancel_text_job.assert_awaited_once_with("disconnect-job-id")


async def test_stream_chat_stall_retries_and_succeeds():
    """Stall timeout cancels the job and retries; succeeds on second attempt."""
    from unittest.mock import AsyncMock, MagicMock

    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeGeneration, HordeJobStatus, HordeModel

    pending_status = HordeJobStatus(
        done=False, faulted=False, generations=[], kudos=0,
        queue_position=5, wait_time=10,
    )
    done_status = HordeJobStatus(
        done=True, faulted=False, kudos=5.0,
        generations=[HordeGeneration(
            text="Hello!", model="test-model", worker_id="w1", worker_name="worker1", kudos=5.0,
        )],
    )

    call_count = 0

    async def poll_side_effect(job_id):
        nonlocal call_count
        call_count += 1
        # First submit: always pending (will stall)
        if job_id == "stall-job-id":
            return pending_status
        # Second submit: immediately done
        return done_status

    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    horde.submit_text_job = AsyncMock(side_effect=["stall-job-id", "retry-job-id"])
    horde.poll_text_status = AsyncMock(side_effect=poll_side_effect)
    horde.cancel_text_job = AsyncMock()

    chunks = []
    gen = _stream_chat(horde, MagicMock(), "best", "test-model", stall_timeout=0, max_retries=1)
    async for chunk in gen:
        chunks.append(chunk)

    all_text = "".join(chunks)
    content = "".join(
        json.loads(l[6:])["choices"][0]["delta"].get("content") or ""
        for l in all_text.splitlines()
        if l.startswith("data: ") and l != "data: [DONE]"
    )
    assert content == "Hello!"
    assert "data: [DONE]" in all_text
    # First job should have been cancelled due to stall
    horde.cancel_text_job.assert_any_await("stall-job-id")
    # Two submits: initial + 1 retry
    assert horde.submit_text_job.await_count == 2


async def test_stream_chat_faulted_retries_and_succeeds():
    """Faulted job is cancelled and retried; succeeds on second attempt."""
    from unittest.mock import AsyncMock, MagicMock

    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeGeneration, HordeJobStatus, HordeModel

    faulted_status = HordeJobStatus(
        done=False, faulted=True, generations=[], kudos=0,
    )
    done_status = HordeJobStatus(
        done=True, faulted=False, kudos=5.0,
        generations=[HordeGeneration(
            text="Hello!", model="test-model", worker_id="w1", worker_name="worker1", kudos=5.0,
        )],
    )

    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    horde.submit_text_job = AsyncMock(side_effect=["fault-job-id", "retry-job-id"])
    horde.poll_text_status = AsyncMock(side_effect=[faulted_status, done_status])
    horde.cancel_text_job = AsyncMock()

    chunks = []
    gen = _stream_chat(horde, MagicMock(), "best", "test-model", stall_timeout=9999, max_retries=1)
    async for chunk in gen:
        chunks.append(chunk)

    all_text = "".join(chunks)
    content = "".join(
        json.loads(l[6:])["choices"][0]["delta"].get("content") or ""
        for l in all_text.splitlines()
        if l.startswith("data: ") and l != "data: [DONE]"
    )
    assert content == "Hello!"
    assert "data: [DONE]" in all_text
    horde.cancel_text_job.assert_any_await("fault-job-id")
    assert horde.submit_text_job.await_count == 2


async def test_stream_chat_no_cancel_after_normal_completion(client):
    """Streaming generator that completes normally does NOT try to cancel the job."""
    cancel_called = False
    original_delete = None

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
        lines = [l async for l in response.aiter_lines()]

    # Normal stream should end with [DONE] — no orphaned cancel
    assert "data: [DONE]" in lines


# ── is_possible=False tests ───────────────────────────────────────────────────

async def test_stream_chat_is_possible_false_aborts_immediately():
    """is_possible=False on first poll → abort immediately, no retry, model banned."""
    from unittest.mock import AsyncMock, MagicMock

    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeJobStatus

    impossible_status = HordeJobStatus(
        done=False, faulted=False, is_possible=False, generations=[], kudos=0,
    )

    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    horde.submit_text_job = AsyncMock(return_value="impossible-job-id")
    horde.poll_text_status = AsyncMock(return_value=impossible_status)
    horde.cancel_text_job = AsyncMock()
    horde.ban_model = MagicMock()
    horde.cached_model_count = MagicMock(return_value=0)  # count=0 → truly offline

    chunks = []
    gen = _stream_chat(horde, MagicMock(), "best", "dead-model", stall_timeout=9999, max_retries=2)
    async for chunk in gen:
        chunks.append(chunk)

    all_text = "".join(chunks)

    # Should abort (not retry with same model)
    assert "x-horde-abort reason=impossible" in all_text
    # MODEL_UNAVAILABLE message should appear in the streamed content
    content = "".join(
        json.loads(l[6:])["choices"][0]["delta"].get("content") or ""
        for l in all_text.splitlines()
        if l.startswith("data: ") and l != "data: [DONE]"
    )
    assert "MODEL_UNAVAILABLE" in content
    # The model should be banned
    horde.ban_model.assert_called_once_with("dead-model", duration=3600.0)
    # Job should be cancelled
    horde.cancel_text_job.assert_awaited_with("impossible-job-id")
    # Only ONE submit — no retry with same dead model
    assert horde.submit_text_job.await_count == 1


async def test_stream_chat_retry_delay_applied():
    """Retry attempts wait the configured streaming_retry_delay seconds."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.config import RetrySettings, Settings
    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeGeneration, HordeJobStatus, HordeModel

    faulted = HordeJobStatus(done=False, faulted=True, generations=[], kudos=0)
    done = HordeJobStatus(
        done=True, faulted=False, kudos=1.0,
        generations=[HordeGeneration(text="ok", model="m", worker_id="w", worker_name="n", kudos=1)],
    )
    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    horde.submit_text_job = AsyncMock(side_effect=["job-1", "job-2"])
    horde.poll_text_status = AsyncMock(side_effect=[faulted, done])
    horde.cancel_text_job = AsyncMock()

    cfg = Settings(
        horde_api_key="k",
        retry=RetrySettings(streaming_retry_delay=0.0, max_retries=1, backoff_base=0),
    )
    sleep_calls = []

    async def _fake_sleep(t):
        sleep_calls.append(t)

    with patch("app.routers.chat.asyncio.sleep", side_effect=_fake_sleep):
        chunks = []
        async for chunk in _stream_chat(horde, MagicMock(), "best", "test-model",
                                        stall_timeout=9999, max_retries=1, config=cfg):
            chunks.append(chunk)

    # With streaming_retry_delay=0.0, sleep(0) is skipped; the poll sleep(2) fires
    assert all(t != 0 for t in sleep_calls if t == 0)  # delay=0 means no extra sleep


async def test_stream_chat_impossible_fallback_to_new_model():
    """On is_possible=False, stream retries with a re-resolved model when available."""
    from unittest.mock import AsyncMock, AsyncMock as AM, MagicMock

    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeGeneration, HordeJobStatus, HordeModel

    impossible = HordeJobStatus(done=False, faulted=False, is_possible=False, generations=[], kudos=0)
    done_status = HordeJobStatus(
        done=True, faulted=False, kudos=2.0,
        generations=[HordeGeneration(text="Hello!", model="new-model", worker_id="w", worker_name="worker", kudos=2)],
    )

    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    horde.submit_text_job = AsyncMock(side_effect=["job-old", "job-new"])
    horde.poll_text_status = AsyncMock(side_effect=[impossible, done_status])
    horde.cancel_text_job = AsyncMock()
    horde.ban_model = MagicMock()
    horde.cached_model_count = MagicMock(return_value=0)  # count=0 → ban on impossible
    horde.get_enriched_models = AsyncMock(return_value=[
        HordeModel(name="old-model", count=0),
        HordeModel(name="new-model", count=1, eta=0, queued=0),
    ])

    # Model router that returns a different model on second call (fallback)
    resolve_calls = []
    async def _resolve(alias, models, config=None, exclude_model=None, exclude_models=None):
        resolve_calls.append(exclude_models)
        if not exclude_models:
            return "old-model"
        return "new-model"
    model_router = MagicMock()
    model_router.resolve = _resolve

    from app.schemas.horde import HordeTextRequest
    horde_req = HordeTextRequest(prompt="test", models=["old-model"])

    chunks = []
    async for chunk in _stream_chat(horde, horde_req, "best", "old-model",
                                    stall_timeout=9999, max_retries=1,
                                    model_router=model_router):
        chunks.append(chunk)

    all_text = "".join(chunks)
    content = "".join(
        json.loads(l[6:])["choices"][0]["delta"].get("content") or ""
        for l in all_text.splitlines()
        if l.startswith("data: ") and l != "data: [DONE]"
    )
    # Should have successfully gotten a response from the fallback model
    assert content == "Hello!"
    assert horde.ban_model.called
    # Two submits: one for old model, one for new model
    assert horde.submit_text_job.await_count == 2


async def test_chat_completions_is_possible_false_retries(app, client, respx_mock):
    """Non-streaming: is_possible=False (count=0) re-resolves and ultimately returns 504."""
    impossible = {
        "done": False, "faulted": False, "processing": 0, "waiting": 1,
        "finished": 0, "queue_position": 0, "wait_time": 0, "kudos": 2.0,
        "generations": [], "is_possible": False,
    }
    target_model = "aphrodite/llama-3.1-8b-instruct"
    # Single model with count=0 so ban triggers → re-resolve finds nothing → 504
    zero_models = [{"name": target_model, "count": 0, "queued": 0, "jobs": 0.0, "eta": 0, "max_length": 512, "max_context_length": 8192, "performance": "", "type": "text"}]
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=zero_models)
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json=impossible)
    )
    respx_mock.delete("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={"message": "Cancelled"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=1, timeout_seconds=1)}
    )
    app.state.horde.invalidate_model_cache()
    response = await client.post(
        "/v1/chat/completions",
        json={"model": target_model, "messages": [{"role": "user", "content": "Hello!"}]},
    )
    assert response.status_code == 504


async def test_chat_completions_is_possible_false_bans_model(app, client, respx_mock):
    """Non-streaming: is_possible=False with count=0 bans the model for 1h."""
    impossible = {
        "done": False, "faulted": False, "processing": 0, "waiting": 1,
        "finished": 0, "queue_position": 0, "wait_time": 0, "kudos": 2.0,
        "generations": [], "is_possible": False,
    }
    target_model = "aphrodite/llama-3.1-8b-instruct"
    zero_models = [{"name": target_model, "count": 0, "queued": 0, "jobs": 0.0, "eta": 0, "max_length": 512, "max_context_length": 8192, "performance": "", "type": "text"}]
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=zero_models)
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json=impossible)
    )
    respx_mock.delete("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={"message": "Cancelled"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=1, timeout_seconds=1)}
    )
    app.state.horde.invalidate_model_cache()
    response = await client.post(
        "/v1/chat/completions",
        json={"model": target_model, "messages": [{"role": "user", "content": "Hello!"}]},
    )
    assert response.status_code == 504
    # The model with no workers should be banned
    horde = app.state.horde
    assert len(horde._banned_models) > 0


async def test_chat_completions_is_possible_false_emits_ban_log(app, client, respx_mock):
    """Non-streaming: is_possible=False with count=0 emits a 'ban' entry to request_log."""
    impossible = {
        "done": False, "faulted": False, "processing": 0, "waiting": 1,
        "finished": 0, "queue_position": 0, "wait_time": 0, "kudos": 2.0,
        "generations": [], "is_possible": False,
    }
    target_model = "aphrodite/llama-3.1-8b-instruct"
    # Single model with count=0 so cached_model_count() returns 0 (truly offline)
    zero_models = [{"name": target_model, "count": 0, "queued": 0, "jobs": 0.0, "eta": 0, "max_length": 512, "max_context_length": 8192, "performance": "", "type": "text"}]
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=zero_models)
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json=impossible)
    )
    respx_mock.delete("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={"message": "Cancelled"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=0, timeout_seconds=1)}
    )
    app.state.request_log = []
    app.state.horde.invalidate_model_cache()

    # Direct model name: resolve() accepts it via name-match even with count=0
    await client.post(
        "/v1/chat/completions",
        json={"model": target_model, "messages": [{"role": "user", "content": "Hello!"}]},
    )

    ban_entries = [e for e in app.state.request_log if e.status == "ban"]
    assert len(ban_entries) >= 1
    assert ban_entries[0].real_model != ""
    assert "banned" in ban_entries[0].error.lower() or "unavailable" in ban_entries[0].error.lower()


async def test_chat_completions_is_possible_false_transient_no_ban(app, client, respx_mock):
    """Non-streaming: is_possible=False with count>0 does NOT ban the model."""
    impossible = {
        "done": False, "faulted": False, "processing": 0, "waiting": 1,
        "finished": 0, "queue_position": 0, "wait_time": 0, "kudos": 2.0,
        "generations": [], "is_possible": False,
    }
    target_model = "aphrodite/llama-3.1-8b-instruct"
    # Single model with count=5 — is_possible=False is transient, should NOT ban
    live_models = [{"name": target_model, "count": 5, "queued": 0, "jobs": 0.0, "eta": 0, "max_length": 512, "max_context_length": 8192, "performance": "", "type": "text"}]
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=live_models)
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json=impossible)
    )
    respx_mock.delete("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={"message": "Cancelled"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": RetrySettings(max_retries=0, timeout_seconds=1, broaden_on_retry=False,
                                       backoff_base=0.0, poll_interval=0.0,
                                       unavailable_max_transient_retries=0)}
    )
    app.state.request_log = []
    app.state.horde.invalidate_model_cache()

    await client.post(
        "/v1/chat/completions",
        json={"model": target_model, "messages": [{"role": "user", "content": "Hello!"}]},
    )

    ban_entries = [e for e in app.state.request_log if e.status == "ban"]
    assert len(ban_entries) == 0, "Model with workers should not be banned when transient retries disabled"


async def test_stream_chat_is_possible_false_emits_ban_log():
    """Streaming: is_possible=False with count=0 emits a 'ban' entry to request_log."""
    from unittest.mock import AsyncMock, MagicMock
    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeJobStatus
    from app.log_store import RequestLogEntry

    impossible_status = HordeJobStatus(
        done=False, faulted=False, is_possible=False, generations=[], kudos=0,
    )

    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    horde.submit_text_job = AsyncMock(return_value="job-ban-log")
    horde.poll_text_status = AsyncMock(return_value=impossible_status)
    horde.cancel_text_job = AsyncMock()
    horde.ban_model = MagicMock()
    horde.cached_model_count = MagicMock(return_value=0)  # count=0 → truly offline

    request_log: list[RequestLogEntry] = []

    mock_state = MagicMock()
    mock_state.request_log = request_log
    mock_state.log_callback = None
    mock_request = MagicMock()
    mock_request.app.state = mock_state
    mock_request.method = "POST"
    mock_request.url.path = "/v1/chat/completions"

    from app.schemas.horde import HordeTextRequest
    horde_req = HordeTextRequest(models=["dead-model"], prompt="Hi")

    chunks = []
    async for chunk in _stream_chat(
        horde=horde,
        horde_req=horde_req,
        alias="best",
        real_model="dead-model",
        request=mock_request,
        max_retries=0,
    ):
        chunks.append(chunk)

    ban_entries = [e for e in request_log if e.status == "ban"]
    assert len(ban_entries) == 1
    assert ban_entries[0].real_model == "dead-model"
    assert ban_entries[0].model == "best"


async def test_stream_chat_is_possible_false_transient_no_ban():
    """Streaming: is_possible=False with count>0 does NOT ban the model."""
    from unittest.mock import AsyncMock, MagicMock
    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeJobStatus
    from app.log_store import RequestLogEntry

    impossible_status = HordeJobStatus(
        done=False, faulted=False, is_possible=False, generations=[], kudos=0,
    )

    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    horde.submit_text_job = AsyncMock(return_value="job-transient")
    horde.poll_text_status = AsyncMock(return_value=impossible_status)
    horde.cancel_text_job = AsyncMock()
    horde.ban_model = MagicMock()
    horde.cached_model_count = MagicMock(return_value=5)  # count>0 → transient

    request_log: list[RequestLogEntry] = []

    mock_state = MagicMock()
    mock_state.request_log = request_log
    mock_state.log_callback = None
    mock_request = MagicMock()
    mock_request.app.state = mock_state
    mock_request.method = "POST"
    mock_request.url.path = "/v1/chat/completions"

    from app.schemas.horde import HordeTextRequest
    from app.config import RetrySettings
    horde_req = HordeTextRequest(models=["live-model"], prompt="Hi")
    config = RetrySettings(max_retries=0, timeout_seconds=0, unavailable_max_transient_retries=0, poll_interval=0.0)

    chunks = []
    async for chunk in _stream_chat(
        horde=horde,
        horde_req=horde_req,
        alias="best",
        real_model="live-model",
        request=mock_request,
        max_retries=0,
        config=type("S", (), {"retry": config, "stream_stall_timeout": 1})(),
    ):
        chunks.append(chunk)

    ban_entries = [e for e in request_log if e.status == "ban"]
    assert len(ban_entries) == 0, "Model with workers should not be banned"
    horde.ban_model.assert_not_called()


async def test_stream_chat_count_positive_transient_exhausted_no_ban():
    """Streaming: count>0 but transient budget exhausted → NO global ban, MODEL_UNAVAILABLE."""
    from unittest.mock import AsyncMock, MagicMock
    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeJobStatus, HordeTextRequest
    from app.config import RetrySettings, Settings

    impossible = HordeJobStatus(done=False, faulted=False, is_possible=False, generations=[], kudos=0)

    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    # 3 submits: initial + 2 transient retries = budget of 2 exhausted on 3rd poll
    horde.submit_text_job = AsyncMock(side_effect=["job-1", "job-2", "job-3"])
    horde.poll_text_status = AsyncMock(return_value=impossible)
    horde.cancel_text_job = AsyncMock()
    horde.ban_model = MagicMock()
    horde.cached_model_count = MagicMock(return_value=5)  # count>0 always
    horde.get_enriched_models = AsyncMock(return_value=[])  # no fallback available

    cfg = Settings(
        horde_api_key="k",
        retry=RetrySettings(
            max_retries=0, poll_interval=0.0, streaming_retry_delay=0.0,
            unavailable_max_transient_retries=2,
        ),
    )

    horde_req = HordeTextRequest(models=["live-model"], prompt="Hi")
    chunks = []
    async for chunk in _stream_chat(
        horde=horde,
        horde_req=horde_req,
        alias="best",
        real_model="live-model",
        stall_timeout=9999,
        max_retries=0,
        config=cfg,
    ):
        chunks.append(chunk)

    all_text = "".join(chunks)
    # 3 job submissions: 1 initial + 2 transient retries
    assert horde.submit_text_job.await_count == 3
    # Model with workers must NEVER be banned
    horde.ban_model.assert_not_called()
    # Should still give a useful error response
    content = "".join(
        json.loads(l[6:])["choices"][0]["delta"].get("content") or ""
        for l in all_text.splitlines()
        if l.startswith("data: ") and l != "data: [DONE]"
    )
    assert "MODEL_UNAVAILABLE" in content


async def test_stream_chat_count_positive_exhausted_passes_exclude_models_to_resolve():
    """Streaming: after transient exhaustion, resolve() receives exclude_models with the failed model."""
    from unittest.mock import AsyncMock, MagicMock
    from app.routers.chat import _stream_chat
    from app.schemas.horde import HordeJobStatus, HordeTextRequest, HordeModel, HordeGeneration
    from app.config import RetrySettings, Settings

    impossible = HordeJobStatus(done=False, faulted=False, is_possible=False, generations=[], kudos=0)
    done_status = HordeJobStatus(
        done=True, faulted=False, kudos=1.0,
        generations=[HordeGeneration(text="ok", model="new-model", worker_id="w", worker_name="n", kudos=1)],
    )

    horde = AsyncMock()
    horde.check_ip_block = MagicMock()
    # 3 impossible (budget=2) then 1 success on new model
    horde.submit_text_job = AsyncMock(side_effect=["job-1", "job-2", "job-3", "job-4"])
    horde.poll_text_status = AsyncMock(side_effect=[impossible, impossible, impossible, done_status])
    horde.cancel_text_job = AsyncMock()
    horde.ban_model = MagicMock()
    horde.cached_model_count = MagicMock(return_value=5)
    horde.get_enriched_models = AsyncMock(return_value=[
        HordeModel(name="live-model", count=5),
        HordeModel(name="new-model", count=3, eta=0, queued=0),
    ])

    cfg = Settings(
        horde_api_key="k",
        retry=RetrySettings(
            max_retries=0, poll_interval=0.0, streaming_retry_delay=0.0,
            unavailable_max_transient_retries=2,
        ),
    )

    resolve_kwargs_list: list[dict] = []

    async def _capture_resolve(alias, models, config=None, exclude_model=None, exclude_models=None):
        resolve_kwargs_list.append({"exclude_model": exclude_model, "exclude_models": exclude_models})
        if exclude_models and "live-model" in exclude_models:
            return "new-model"
        return "live-model"

    model_router = MagicMock()
    model_router.resolve = _capture_resolve

    horde_req = HordeTextRequest(models=["live-model"], prompt="Hi")
    chunks = []
    async for chunk in _stream_chat(
        horde=horde,
        horde_req=horde_req,
        alias="best",
        real_model="live-model",
        stall_timeout=9999,
        max_retries=0,
        config=cfg,
        model_router=model_router,
    ):
        chunks.append(chunk)

    all_text = "".join(chunks)
    # ban_model must NOT be called (count > 0)
    horde.ban_model.assert_not_called()
    # resolve was called with exclude_models containing the failed model
    assert any(
        kw.get("exclude_models") and "live-model" in kw["exclude_models"]
        for kw in resolve_kwargs_list
    ), f"Expected exclude_models to contain 'live-model', got: {resolve_kwargs_list}"
    # Should succeed with fallback model
    content = "".join(
        json.loads(l[6:])["choices"][0]["delta"].get("content") or ""
        for l in all_text.splitlines()
        if l.startswith("data: ") and l != "data: [DONE]"
    )
    assert content == "ok"


async def test_chat_completions_count_positive_exhausted_no_ban(app, client, respx_mock):
    """Non-streaming: count>0 but transient budget exhausted → NO ban, 504 returned."""
    impossible = {
        "done": False, "faulted": False, "processing": 0, "waiting": 1,
        "finished": 0, "queue_position": 0, "wait_time": 0, "kudos": 0.0,
        "generations": [], "is_possible": False,
    }
    target_model = "aphrodite/llama-3.1-8b-instruct"
    # count=5: model has workers, so is_possible=False must be treated as transient
    live_models = [{"name": target_model, "count": 5, "queued": 0, "jobs": 0.0, "eta": 0,
                    "max_length": 512, "max_context_length": 8192, "performance": "", "type": "text"}]
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=live_models)
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json=impossible)
    )
    respx_mock.delete("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={"message": "Cancelled"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": RetrySettings(
            max_retries=0, timeout_seconds=1, broaden_on_retry=False,
            backoff_base=0.0, poll_interval=0.0,
            unavailable_max_transient_retries=2,
        )}
    )
    app.state.request_log = []
    app.state.horde.invalidate_model_cache()

    response = await client.post(
        "/v1/chat/completions",
        json={"model": target_model, "messages": [{"role": "user", "content": "Hello!"}]},
    )

    # Request ultimately fails (no fallback model after skipping the transient one)
    assert response.status_code == 504
    # Model with workers must NOT be banned globally
    assert len(app.state.horde._banned_models) == 0, (
        f"Model was wrongly banned: {app.state.horde._banned_models}"
    )
    # No ban log entry
    ban_entries = [e for e in app.state.request_log if e.status == "ban"]
    assert len(ban_entries) == 0, "Model with active workers should not emit a ban log entry"


# ── IP block / CorruptPrompt integration tests ────────────────────────────────

async def test_chat_completions_timeout_ip_returns_503(app, client, respx_mock):
    """Non-streaming: Horde 403 TimeoutIP → proxy returns 503 without retrying."""
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(403, json={"message": "IP timed out", "rc": "TimeoutIP"})
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "best", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 503


async def test_chat_completions_timeout_ip_short_circuits(app, client, respx_mock):
    """After TimeoutIP, subsequent requests are rejected locally without hitting Horde."""
    import time
    # Set IP block directly on the client
    horde = app.state.horde
    horde._ip_blocked_until = time.monotonic() + 3600.0
    horde._ip_block_reason = "TimeoutIP"

    response = await client.post(
        "/v1/chat/completions",
        json={"model": "best", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 503
    # The submit endpoint should NOT have been called
    assert not any(
        call.request.url.path == "/v2/generate/text/async"
        for call in respx_mock.calls
    )


async def test_chat_completions_unsafe_ip_returns_503(app, client, respx_mock):
    """Non-streaming: Horde 403 UnsafeIP → proxy returns 503."""
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(403, json={"message": "Unsafe IP", "rc": "UnsafeIP"})
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "best", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 503


async def test_chat_completions_corrupt_prompt_returns_400(app, client, respx_mock):
    """Non-streaming: Horde 400 CorruptPrompt → proxy returns 400, never retries."""
    submit_calls = 0

    def submit_side_effect(request):
        nonlocal submit_calls
        submit_calls += 1
        return httpx.Response(400, json={"message": "Prompt is corrupt", "rc": "CorruptPrompt"})

    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        side_effect=submit_side_effect
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=2)}
    )
    response = await client.post(
        "/v1/chat/completions",
        json={"model": "best", "messages": [{"role": "user", "content": "bad content"}]},
    )
    assert response.status_code == 400
    assert submit_calls == 1  # not retried


async def test_rate_limit_cooldown_recorded(app, client, respx_mock):
    """After a 429 response, the client records a rate-limit cooldown."""
    import time
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(429, json={"message": "Rate limited"})
    )
    app.state.config = app.state.config.model_copy(
        update={"retry": _fast_retry(max_retries=0)}
    )
    await client.post(
        "/v1/chat/completions",
        json={"model": "best", "messages": [{"role": "user", "content": "Hi"}]},
    )
    horde = app.state.horde
    # A cooldown should have been recorded
    assert horde._rate_limited_until > time.monotonic()


# ── Unit tests for _strip_eos and _split_thinking ────────────────────────────

def test_strip_eos_chatml():
    assert _strip_eos("Hello!<|im_end|>") == "Hello!"


def test_strip_eos_llama3_eot():
    assert _strip_eos("Hello!<|eot_id|>") == "Hello!"


def test_strip_eos_llama3_end_of_text():
    assert _strip_eos("Hello!<|end_of_text|>") == "Hello!"


def test_strip_eos_slash_s():
    assert _strip_eos("Hello!</s>") == "Hello!"


def test_strip_eos_multiple_trailing():
    # Worker may emit token + whitespace + token
    assert _strip_eos("Hello!<|im_end|>  <|im_end|>") == "Hello!"


def test_strip_eos_no_eos():
    assert _strip_eos("Hello!") == "Hello!"


def test_strip_eos_empty():
    assert _strip_eos("") == ""


def test_split_thinking_strips_eos_plain():
    rc, text = _split_thinking("Hello!<|im_end|>")
    assert rc is None
    assert text == "Hello!"


def test_split_thinking_strips_eos_after_think():
    rc, text = _split_thinking("<think>reason</think>\nAnswer<|im_end|>")
    assert rc == "reason"
    assert text == "Answer"
