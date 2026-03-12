from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.config import Settings
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Prevent tests from reading/writing the real user config or log file."""
    import app.config as _cfg
    import app.log_store as _log

    monkeypatch.setattr(_cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(_cfg, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(_log, "LOG_PATH", tmp_path / "requests.jsonl")


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


MODELS_FIXTURE = load_fixture("horde_models.json")
GENERATE_FIXTURE = load_fixture("horde_generate.json")
USER_FIXTURE = load_fixture("horde_user.json")
IMAGE_FIXTURE = load_fixture("horde_image.json")


@pytest.fixture
def test_config(tmp_path) -> Settings:
    return Settings(
        horde_api_key="test-key-0000",
        horde_api_url="https://aihorde.net/api",
        default_model="aphrodite/llama-3.1-8b-instruct",
        model_aliases={"large": "aphrodite/llama-3.1-70b-instruct"},
        model_blocklist=["yi"],
        retry={"max_retries": 1, "timeout_seconds": 10, "broaden_on_retry": False, "poll_interval": 0, "backoff_base": 0, "streaming_retry_delay": 0},
    )


@pytest.fixture
def mock_horde(respx_mock):
    respx_mock.get("https://aihorde.net/api/v2/status/models").mock(
        return_value=httpx.Response(200, json=MODELS_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/text/async").mock(
        return_value=httpx.Response(202, json={"id": "test-job-id"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json=GENERATE_FIXTURE)
    )
    respx_mock.delete("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={"message": "Cancelled"})
    )
    respx_mock.get("https://aihorde.net/api/v2/workers").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx_mock.get("https://aihorde.net/api/v2/find_user").mock(
        return_value=httpx.Response(200, json=USER_FIXTURE)
    )
    respx_mock.post("https://aihorde.net/api/v2/generate/async").mock(
        return_value=httpx.Response(202, json={"id": "test-image-job-id"})
    )
    respx_mock.get("https://aihorde.net/api/v2/generate/status/test-image-job-id").mock(
        return_value=httpx.Response(200, json=IMAGE_FIXTURE)
    )
    return respx_mock


@pytest.fixture
def app(test_config, mock_horde):
    return create_app(test_config)


@pytest.fixture
async def client(app):
    from app.horde.client import HordeClient
    from app.horde.routing import ModelRouter

    config = app.state.config
    horde = HordeClient(
        base_url=config.horde_api_url,
        api_key=config.horde_api_key,
        client_agent=config.client_agent,
        model_cache_ttl=config.model_cache_ttl,
        global_min_request_delay=0,  # disable throttle for tests
    )
    app.state.horde = horde
    app.state.model_router = ModelRouter(config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await horde.close()
