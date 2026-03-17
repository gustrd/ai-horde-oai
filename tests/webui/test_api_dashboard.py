"""Tests for /ui/api/dashboard endpoint."""
from __future__ import annotations

import pytest

from tests.webui.conftest import make_log_entry


@pytest.mark.asyncio
async def test_dashboard_shape(webui_client):
    r = await webui_client.get("/ui/api/dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in ["server", "api_key_masked", "horde_url", "banned_models",
                "ip_blocked", "rate_limited", "request_count", "session_kudos"]:
        assert key in data


@pytest.mark.asyncio
async def test_dashboard_user_info(webui_client):
    r = await webui_client.get("/ui/api/dashboard")
    data = r.json()
    user = data.get("user", {})
    # Fixture has username "testuser#1234" and kudos 12450
    assert user.get("username") == "testuser#1234"
    assert user.get("kudos") == 12450.0


@pytest.mark.asyncio
async def test_dashboard_no_bans_by_default(webui_client):
    r = await webui_client.get("/ui/api/dashboard")
    assert r.json()["banned_models"] == []


@pytest.mark.asyncio
async def test_dashboard_session_kudos(webui_client, webui_app):
    entry = make_log_entry(kudos=30.0)
    webui_app.state.request_log.append(entry)
    r = await webui_client.get("/ui/api/dashboard")
    assert r.json()["session_kudos"] == 30.0


@pytest.mark.asyncio
async def test_unban_all(webui_client, webui_app):
    webui_app.state.horde.ban_model("some-model", duration=3600)
    assert len(webui_app.state.horde.banned_models) == 1
    r = await webui_client.post("/ui/api/dashboard/unban")
    assert r.status_code == 200
    assert len(webui_app.state.horde.banned_models) == 0
