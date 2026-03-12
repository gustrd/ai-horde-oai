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


async def test_completions_is_possible_false_emits_ban_log(app, client, respx_mock):
    """is_possible=False with count=0 in completions emits a 'ban' entry to request_log."""
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
        update={"retry": RetrySettings(max_retries=0, timeout_seconds=1, broaden_on_retry=False, backoff_base=0.0, poll_interval=0.0)}
    )
    app.state.request_log = []
    app.state.horde.invalidate_model_cache()

    # Direct model name: resolve() accepts it via name-match even with count=0
    await client.post(
        "/v1/completions",
        json={"model": target_model, "prompt": "Hello"},
    )

    ban_entries = [e for e in app.state.request_log if e.status == "ban"]
    assert len(ban_entries) >= 1
    assert ban_entries[0].real_model != ""
    assert "banned" in ban_entries[0].error.lower() or "unavailable" in ban_entries[0].error.lower()


async def test_completions_is_possible_false_transient_no_ban(app, client, respx_mock):
    """is_possible=False with count>0 in completions does NOT ban the model."""
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
        "/v1/completions",
        json={"model": target_model, "prompt": "Hello"},
    )

    ban_entries = [e for e in app.state.request_log if e.status == "ban"]
    assert len(ban_entries) == 0, "Model with workers should not be banned when transient retries disabled"


async def test_completions_count_positive_exhausted_no_ban(app, client, respx_mock):
    """completions: count>0 but transient budget exhausted → NO ban, 504 returned."""
    impossible = {
        "done": False, "faulted": False, "processing": 0, "waiting": 1,
        "finished": 0, "queue_position": 0, "wait_time": 0, "kudos": 0.0,
        "generations": [], "is_possible": False,
    }
    target_model = "aphrodite/llama-3.1-8b-instruct"
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
        "/v1/completions",
        json={"model": target_model, "prompt": "Hello"},
    )

    assert response.status_code == 504
    assert len(app.state.horde._banned_models) == 0, (
        f"Model was wrongly banned: {app.state.horde._banned_models}"
    )
    ban_entries = [e for e in app.state.request_log if e.status == "ban"]
    assert len(ban_entries) == 0, "Model with active workers should not emit a ban log entry"
