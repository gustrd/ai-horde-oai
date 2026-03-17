"""Tests for WebSocket ConnectionManager."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.webui.ws import ConnectionManager


@pytest.mark.asyncio
async def test_connect_disconnect():
    mgr = ConnectionManager()
    ws = AsyncMock()
    await mgr.connect(ws)
    assert ws in mgr._connections
    await mgr.disconnect(ws)
    assert ws not in mgr._connections


@pytest.mark.asyncio
async def test_disconnect_unknown_is_noop():
    mgr = ConnectionManager()
    ws = AsyncMock()
    # Disconnecting something never connected should not raise
    await mgr.disconnect(ws)


@pytest.mark.asyncio
async def test_broadcast_sends_to_all():
    mgr = ConnectionManager()
    ws1, ws2 = AsyncMock(), AsyncMock()
    await mgr.connect(ws1)
    await mgr.connect(ws2)

    msg = {"type": "log_entry", "data": {"status": 200}}
    await mgr.broadcast(msg)

    ws1.send_text.assert_called_once_with(json.dumps(msg))
    ws2.send_text.assert_called_once_with(json.dumps(msg))


@pytest.mark.asyncio
async def test_broadcast_removes_dead_connections():
    mgr = ConnectionManager()
    dead = AsyncMock()
    dead.send_text.side_effect = RuntimeError("closed")
    alive = AsyncMock()

    await mgr.connect(dead)
    await mgr.connect(alive)

    await mgr.broadcast({"type": "test"})

    assert dead not in mgr._connections
    assert alive in mgr._connections


@pytest.mark.asyncio
async def test_broadcast_empty_no_error():
    mgr = ConnectionManager()
    # Should not raise with no connections
    await mgr.broadcast({"type": "test"})
