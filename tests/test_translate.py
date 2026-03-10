from __future__ import annotations

import pytest

from app.config import Settings
from app.horde.translate import _normalize_stop, _parse_size, chat_to_horde, completion_to_horde
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
    # max_tokens is passed through without capping
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


def test_normalize_stop_none():
    assert _normalize_stop(None) is None


def test_normalize_stop_str():
    assert _normalize_stop("END") == ["END"]


def test_normalize_stop_list():
    assert _normalize_stop(["A", "B"]) == ["A", "B"]


def test_parse_size():
    assert _parse_size("1024x768") == (1024, 768)
    assert _parse_size("invalid") == (1024, 1024)
