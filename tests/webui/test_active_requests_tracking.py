"""Tests for active request tracking and WebSocket broadcast in the WebUI router.

Key regression: cancel_fn is a Python callable stored in each active_req dict.
json.dumps raises TypeError on it, which broadcast_sync swallows silently —
so NOTHING is sent to the browser. The fix is _serialize_active() which strips
non-serializable keys before broadcasting.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.webui.router import _serialize_active, setup_webui_callbacks
from app.log_store import RequestLogEntry


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

class _State:
    pass


class _App:
    def __init__(self):
        self.state = _State()


def _make_entry(**kwargs):
    defaults = dict(
        timestamp=datetime.now(),
        method="POST",
        path="/v1/chat/completions",
        status=200,
        duration=1.0,
        model="test-model",
    )
    defaults.update(kwargs)
    return RequestLogEntry(**defaults)


# ---------------------------------------------------------------------------
# _serialize_active — unit tests
# ---------------------------------------------------------------------------

def test_serialize_active_strips_cancel_fn():
    """cancel_fn must be removed so json.dumps does not raise TypeError."""
    async def _fake_cancel(job_id): ...

    active = [{
        "method": "POST", "path": "/v1/chat/completions",
        "alias": "best", "model": "aphrodite/llama-3.1-8b-instruct",
        "max_tokens": 512, "queue_pos": 3, "eta": 45, "job_id": "job-abc",
        "cancel_fn": _fake_cancel,      # <<< the problematic field
        "messages": [{"role": "user", "content": "Hello"}],
    }]

    result = _serialize_active(active)

    # Must be JSON-serializable — this line would throw before the fix
    decoded = json.loads(json.dumps(result))
    r = decoded[0]
    assert "cancel_fn" not in r
    assert r["model"] == "aphrodite/llama-3.1-8b-instruct"
    assert r["queue_pos"] == 3
    assert r["job_id"] == "job-abc"
    assert r["messages"][0]["content"] == "Hello"


def test_serialize_active_empty():
    """Empty list serializes cleanly."""
    assert _serialize_active([]) == []


def test_serialize_active_keeps_all_display_fields():
    """All display fields survive serialization."""
    req = {
        "method": "POST", "path": "/v1/chat/completions",
        "alias": "fast", "model": "koboldcpp/qwen", "max_tokens": 256,
        "queue_pos": 1, "eta": 10, "job_id": "j-xyz",
        "cancel_fn": lambda: None, "messages": None,
    }
    r = _serialize_active([req])[0]
    for field in ("method", "path", "alias", "model", "max_tokens",
                  "queue_pos", "eta", "job_id", "messages"):
        assert field in r, f"Missing field: {field}"


def test_serialize_active_messages_preserved():
    """messages list (conversation history) survives and is JSON-safe."""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    req = {
        "method": "POST", "path": "/v1/chat/completions",
        "alias": "best", "model": "m", "max_tokens": 100,
        "queue_pos": None, "eta": None, "job_id": None,
        "cancel_fn": None, "messages": messages,
    }
    result = _serialize_active([req])
    assert result[0]["messages"] == messages


def test_serialize_active_queue_pos_zero():
    """queue_pos=0 is kept (falsy but not None — critical for status display)."""
    req = {
        "method": "POST", "path": "/v1/chat/completions",
        "alias": "best", "model": "m", "max_tokens": 100,
        "queue_pos": 0, "eta": 90, "job_id": "j-1",
        "cancel_fn": None, "messages": None,
    }
    result = _serialize_active([req])
    # queue_pos=0 must NOT be dropped (it's falsy but indicates position 0 in queue)
    assert result[0]["queue_pos"] == 0


# ---------------------------------------------------------------------------
# setup_webui_callbacks — integration tests
# ---------------------------------------------------------------------------

def test_setup_callbacks_initializes_active_requests():
    """setup_webui_callbacks creates active_requests list if absent."""
    app = _App()
    with patch("app.webui.router.ws_manager") as mock_ws, \
         patch("app.webui.router.load_entries", return_value=[]):
        mock_ws.broadcast_sync = MagicMock()
        setup_webui_callbacks(app)
    assert hasattr(app.state, "active_requests")
    assert app.state.active_requests == []


def test_start_callback_adds_request_and_broadcasts():
    """start_callback appends to active_requests and sends valid JSON over WS."""
    app = _App()
    broadcasts = []

    async def _cancel(job_id): ...

    req = {
        "method": "POST", "path": "/v1/chat/completions",
        "alias": "best", "model": "some-model", "max_tokens": 100,
        "queue_pos": None, "eta": None, "job_id": None,
        "cancel_fn": _cancel, "messages": None,
    }

    # patch must stay active during the callback — closures capture the
    # module-level ws_manager reference, not a snapshot at setup time.
    with patch("app.webui.router.ws_manager") as mock_ws, \
         patch("app.webui.router.load_entries", return_value=[]):
        mock_ws.broadcast_sync = MagicMock(side_effect=lambda m: broadcasts.append(m))
        setup_webui_callbacks(app)
        app.state.start_callback(req)

    assert req in app.state.active_requests

    active_msgs = [m for m in broadcasts if m.get("type") == "active_requests"]
    assert len(active_msgs) == 1

    # Must be JSON-serializable (regression guard)
    decoded = json.loads(json.dumps(active_msgs[0]))
    assert decoded["data"][0]["model"] == "some-model"
    assert "cancel_fn" not in decoded["data"][0]


def test_log_callback_removes_request_and_broadcasts_empty():
    """log_callback removes matching request and broadcasts the now-empty list."""
    app = _App()
    broadcasts = []

    req = {
        "method": "POST", "path": "/v1/chat/completions",
        "alias": "", "model": "", "max_tokens": 0,
        "queue_pos": None, "eta": None, "job_id": None,
        "cancel_fn": None, "messages": None,
    }

    with patch("app.webui.router.ws_manager") as mock_ws, \
         patch("app.webui.router.load_entries", return_value=[]):
        mock_ws.broadcast_sync = MagicMock(side_effect=lambda m: broadcasts.append(m))
        setup_webui_callbacks(app)
        app.state.active_requests.append(req)
        app.state.log_callback(_make_entry())

    assert app.state.active_requests == []
    active_msgs = [m for m in broadcasts if m.get("type") == "active_requests"]
    assert len(active_msgs) == 1
    assert active_msgs[0]["data"] == []


def test_refresh_active_broadcasts_serialized():
    """refresh_active_callback sends only JSON-safe data (strips cancel_fn)."""
    app = _App()
    broadcasts = []

    async def _cancel(job_id): ...

    with patch("app.webui.router.ws_manager") as mock_ws, \
         patch("app.webui.router.load_entries", return_value=[]):
        mock_ws.broadcast_sync = MagicMock(side_effect=lambda m: broadcasts.append(m))
        setup_webui_callbacks(app)
        app.state.active_requests.append({
            "method": "POST", "path": "/v1/chat/completions",
            "alias": "best", "model": "llama", "max_tokens": 512,
            "queue_pos": 5, "eta": 30, "job_id": "j-1",
            "cancel_fn": _cancel,  # non-serializable
            "messages": [{"role": "user", "content": "hi"}],
        })
        app.state.refresh_active_callback()

    active_msgs = [m for m in broadcasts if m.get("type") == "active_requests"]
    assert len(active_msgs) == 1
    decoded = json.loads(json.dumps(active_msgs[0]))  # would throw without fix
    r = decoded["data"][0]
    assert r["queue_pos"] == 5
    assert "cancel_fn" not in r


def test_refresh_reflects_updated_queue_position():
    """Each refresh call broadcasts the current (mutated) queue_pos."""
    app = _App()
    broadcasts = []

    req = {
        "method": "POST", "path": "/v1/chat/completions",
        "alias": "best", "model": "m", "max_tokens": 100,
        "queue_pos": 10, "eta": 120, "job_id": "j-2",
        "cancel_fn": None, "messages": None,
    }

    with patch("app.webui.router.ws_manager") as mock_ws, \
         patch("app.webui.router.load_entries", return_value=[]):
        mock_ws.broadcast_sync = MagicMock(side_effect=lambda m: broadcasts.append(m))
        setup_webui_callbacks(app)
        app.state.active_requests.append(req)

        req["queue_pos"] = 5; req["eta"] = 60
        app.state.refresh_active_callback()

        req["queue_pos"] = 0; req["eta"] = 10
        app.state.refresh_active_callback()

    active_msgs = [m for m in broadcasts if m.get("type") == "active_requests"]
    assert len(active_msgs) == 2
    assert [m["data"][0]["queue_pos"] for m in active_msgs] == [5, 0]


def test_multiple_concurrent_requests_one_completes():
    """Two concurrent in-flight requests; completing req1 leaves only req2."""
    app = _App()
    broadcasts = []

    req1 = {
        "method": "POST", "path": "/v1/chat/completions",
        "alias": "fast", "model": "m1", "max_tokens": 100,
        "queue_pos": 2, "eta": 30, "job_id": "j-A",
        "cancel_fn": None, "messages": None,
    }
    req2 = {
        "method": "POST", "path": "/v1/chat/completions",
        "alias": "best", "model": "m2", "max_tokens": 200,
        "queue_pos": 5, "eta": 80, "job_id": "j-B",
        "cancel_fn": None, "messages": None,
    }

    with patch("app.webui.router.ws_manager") as mock_ws, \
         patch("app.webui.router.load_entries", return_value=[]):
        mock_ws.broadcast_sync = MagicMock(side_effect=lambda m: broadcasts.append(m))
        setup_webui_callbacks(app)
        app.state.start_callback(req1)
        app.state.start_callback(req2)
        app.state.log_callback(_make_entry(model="m1"))

    active_msgs = [m for m in broadcasts if m.get("type") == "active_requests"]
    # 2 start broadcasts + 1 completion broadcast
    assert len(active_msgs) == 3
    final = active_msgs[-1]["data"]
    assert len(final) == 1
    assert final[0]["job_id"] == "j-B"
