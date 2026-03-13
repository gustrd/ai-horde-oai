from __future__ import annotations

import pytest

from app.config import Settings
from app.horde.translate import _normalize_stop, cap_params_to_model, chat_to_horde, completion_to_horde
from app.schemas.horde import HordeModel, HordeTextRequest, HordeTextParams
from app.schemas.openai import ChatCompletionRequest, ChatMessage, CompletionRequest


def make_chat_request(**kwargs) -> ChatCompletionRequest:
    defaults = {
        "model": "default",
        "messages": [ChatMessage(role="user", content="Hello")],
    }
    defaults.update(kwargs)
    return ChatCompletionRequest(**defaults)


@pytest.fixture
def config():
    return Settings(trusted_workers=False)


def test_chat_to_horde_basic(config):
    req = make_chat_request(max_tokens=200, temperature=0.7)
    result = chat_to_horde(req, "aphrodite/llama-3.1-8b", config)
    assert result.models == ["aphrodite/llama-3.1-8b"]
    assert result.params.max_length == 200
    assert result.params.temperature == 0.7


def test_chat_to_horde_stop_string(config):
    req = make_chat_request(stop="<|end|>")
    result = chat_to_horde(req, "model/x", config)
    assert result.params.stop_sequence == ["<|end|>"]


def test_chat_to_horde_stop_list(config):
    req = make_chat_request(stop=["<|end|>", "</s>"])
    result = chat_to_horde(req, "model/x", config)
    assert result.params.stop_sequence == ["<|end|>", "</s>"]


def test_chat_to_horde_max_tokens_passed_through(config):
    # max_tokens is passed through when no model_info is provided
    req = make_chat_request(max_tokens=2000)
    result = chat_to_horde(req, "model/x", config)
    assert result.params.max_length == 2000


def test_chat_to_horde_worker_whitelist(config):
    config.worker_whitelist = ["worker-abc"]
    req = make_chat_request()
    result = chat_to_horde(req, "model/x", config)
    assert result.workers == ["worker-abc"]


def test_completion_to_horde(config):
    req = CompletionRequest(model="default", prompt="Tell me a story", max_tokens=100)
    result = completion_to_horde(req, "aphrodite/llama-3.1-8b", config)
    assert result.prompt == "Tell me a story"
    assert result.params.max_length == 100


def make_model_info(name="model/x", max_length=512, max_context_length=2048) -> HordeModel:
    return HordeModel(name=name, max_length=max_length, max_context_length=max_context_length)


# --- model_info capping tests ---

def test_chat_to_horde_uses_model_context_length(config):
    req = make_chat_request()
    info = make_model_info(max_context_length=2048)
    result = chat_to_horde(req, "model/x", config, model_info=info)
    assert result.params.max_context_length == 2048


def test_chat_to_horde_fallback_context_length_when_no_model_info(config):
    req = make_chat_request()
    result = chat_to_horde(req, "model/x", config, model_info=None)
    assert result.params.max_context_length == 4096


def test_chat_to_horde_caps_max_length_to_model(config):
    req = make_chat_request(max_tokens=2048)
    info = make_model_info(max_length=512)
    result = chat_to_horde(req, "model/x", config, model_info=info)
    assert result.params.max_length == 512


def test_chat_to_horde_max_length_not_capped_when_below_model_limit(config):
    req = make_chat_request(max_tokens=200)
    info = make_model_info(max_length=512)
    result = chat_to_horde(req, "model/x", config, model_info=info)
    assert result.params.max_length == 200


def test_completion_to_horde_uses_model_info(config):
    req = CompletionRequest(model="default", prompt="Hello", max_tokens=1000)
    info = make_model_info(max_length=512, max_context_length=2048)
    result = completion_to_horde(req, "model/x", config, model_info=info)
    assert result.params.max_length == 512
    assert result.params.max_context_length == 2048


def test_cap_params_to_model():
    req = HordeTextRequest(
        prompt="test",
        params=HordeTextParams(max_length=2048, max_context_length=4096),
        models=["old/model"],
    )
    info = make_model_info(name="new/model", max_length=512, max_context_length=2048)
    result = cap_params_to_model(req, info)
    assert result.models == ["new/model"]
    assert result.params.max_length == 512
    assert result.params.max_context_length == 2048
    # original unchanged
    assert req.models == ["old/model"]
    assert req.params.max_length == 2048


def test_cap_params_to_model_does_not_inflate():
    # If model can handle more than requested, keep the requested value
    req = HordeTextRequest(
        prompt="test",
        params=HordeTextParams(max_length=100, max_context_length=1024),
        models=["old/model"],
    )
    info = make_model_info(name="new/model", max_length=4096, max_context_length=8192)
    result = cap_params_to_model(req, info)
    assert result.params.max_length == 100
    assert result.params.max_context_length == 1024


def test_normalize_stop_none():
    assert _normalize_stop(None) is None


def test_normalize_stop_str():
    assert _normalize_stop("END") == ["END"]


def test_normalize_stop_list():
    assert _normalize_stop(["A", "B"]) == ["A", "B"]
