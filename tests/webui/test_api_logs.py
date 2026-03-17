"""Tests for /ui/api/logs endpoints."""
from __future__ import annotations

import pytest

from tests.webui.conftest import make_log_entry


@pytest.mark.asyncio
async def test_logs_empty(webui_client):
    r = await webui_client.get("/ui/api/logs")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_logs_returns_newest_first(webui_client, webui_app):
    from datetime import datetime, timedelta
    e1 = make_log_entry(response_text="first")
    e2 = make_log_entry(response_text="second")
    webui_app.state.request_log.extend([e1, e2])

    r = await webui_client.get("/ui/api/logs")
    entries = r.json()
    assert len(entries) == 2
    assert entries[0]["response_text"] == "second"
    assert entries[1]["response_text"] == "first"


@pytest.mark.asyncio
async def test_log_detail(webui_client, webui_app):
    entry = make_log_entry(response_text="detail-test", kudos=5.0)
    webui_app.state.request_log.append(entry)

    r = await webui_client.get("/ui/api/logs/0")
    assert r.status_code == 200
    data = r.json()
    assert data["response_text"] == "detail-test"
    assert data["kudos"] == 5.0


@pytest.mark.asyncio
async def test_log_detail_out_of_range(webui_client):
    r = await webui_client.get("/ui/api/logs/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_toggle_check(webui_client, webui_app):
    entry = make_log_entry()
    webui_app.state.request_log.append(entry)
    assert entry.checked is False

    r = await webui_client.patch("/ui/api/logs/0/check")
    assert r.status_code == 200
    assert r.json()["checked"] is True
    assert entry.checked is True

    # Toggle back
    r2 = await webui_client.patch("/ui/api/logs/0/check")
    assert r2.json()["checked"] is False


@pytest.mark.asyncio
async def test_clear_logs(webui_client, webui_app):
    webui_app.state.request_log.append(make_log_entry())
    r = await webui_client.delete("/ui/api/logs")
    assert r.status_code == 200
    assert webui_app.state.request_log == []
