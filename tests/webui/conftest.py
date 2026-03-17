"""Shared fixtures for webui tests."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.horde.client import HordeClient
from app.horde.routing import ModelRouter
from app.log_store import RequestLogEntry
from app.main import create_app

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


MODELS_FIXTURE = load_fixture("horde_models.json")
USER_FIXTURE = load_fixture("horde_user.json")


@pytest.fixture
def webui_config(tmp_path) -> Settings:
    return Settings(
        horde_api_key="test-webui-key",
        horde_api_url="https://aihorde.net/api",
        host="127.0.0.1",
        port=18000,
        default_model="fast",
        model_aliases={"large": "some-large-model"},
    )


@pytest.fixture
def webui_app(webui_config, respx_mock):
    """FastAPI app with mocked Horde API."""
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.get("https://aihorde.net/api/v2/find_user").mock(
        return_value=httpx.Response(200, json=USER_FIXTURE)
    )
    respx_mock.get("https://aihorde.net/api/v2/workers").mock(
        return_value=httpx.Response(200, json=[])
    )
    return create_app(webui_config)


@pytest.fixture
async def webui_client(webui_app, webui_config):
    """Async HTTP client wired directly to the ASGI app."""
    horde = HordeClient(
        base_url=webui_config.horde_api_url,
        api_key=webui_config.horde_api_key,
        client_agent=webui_config.client_agent,
        global_min_request_delay=0,
    )
    webui_app.state.horde = horde
    webui_app.state.model_router = ModelRouter(webui_config)
    webui_app.state.request_log = []
    webui_app.state.active_requests = []
    transport = httpx.ASGITransport(app=webui_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await horde.close()


def make_log_entry(**kwargs) -> RequestLogEntry:
    defaults = dict(
        timestamp=datetime.now(),
        method="POST",
        path="/v1/chat/completions",
        status=200,
        duration=1.5,
        model="fast",
        real_model="some-model",
        worker="worker-1",
        worker_id="wid-1",
        kudos=12.5,
        response_text="Hello!",
        input_tokens=10,
        output_tokens=5,
    )
    defaults.update(kwargs)
    return RequestLogEntry(**defaults)
