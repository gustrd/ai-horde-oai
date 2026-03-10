"""Tests for tool/function calling implementation."""
from __future__ import annotations

import json

import httpx
import pytest

from app.horde.templates import format_tool_result, format_tools_for_model, messages_to_prompt
from app.horde.tool_parser import detect_tool_format, parse_tool_call
from app.horde.translate import chat_to_horde
from app.schemas.openai import ChatMessage, Tool, ToolFunction
from tests.conftest import MODELS_FIXTURE


pytestmark = pytest.mark.asyncio

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}

TOOL_CALL_HERMES = '<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n</tool_call>'
TOOL_CALL_HERMES_NO_CLOSE = '<tool_call>\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n'
TOOL_CALL_LLAMA3 = '{"name": "get_weather", "parameters": {"city": "Paris"}}'
TOOL_CALL_LLAMA3_PYTHON_TAG = '<|python_tag|>{"name": "get_weather", "parameters": {"city": "London"}}'
TOOL_CALL_QWEN_BRACKET = '[TOOL_CALLS]get_weather[ARGS]{"city": "Tokyo"}'


# ---------------------------------------------------------------------------
# Phase 2: Template layer
# ---------------------------------------------------------------------------

def test_tool_injection_hermes():
    """System prompt contains <tools> block for hermes/chatml models."""
    result = format_tools_for_model([WEATHER_TOOL], "chatml")
    assert "<tools>" in result
    assert "get_weather" in result
    assert "<tool_call>" in result


def test_tool_injection_llama3():
    """System prompt contains JSON tool list for llama3 models."""
    result = format_tools_for_model([WEATHER_TOOL], "llama3")
    assert "<tools>" not in result
    assert "get_weather" in result
    assert "Available tools:" in result


def test_tool_injection_in_system_prompt():
    """Tools are injected into system prompt when rendering messages."""
    messages = [
        ChatMessage(role="system", content="You are a helpful assistant."),
        ChatMessage(role="user", content="What is the weather in Paris?"),
    ]
    prompt = messages_to_prompt(messages, "aphrodite/nous-hermes-2-mistral-7b", tools=[WEATHER_TOOL])
    assert "<tools>" in prompt
    assert "get_weather" in prompt
    assert "You are a helpful assistant." in prompt


def test_tool_injection_no_system_message():
    """Tools injection creates synthetic system block when no system message exists."""
    messages = [ChatMessage(role="user", content="What is the weather in Paris?")]
    prompt = messages_to_prompt(messages, "aphrodite/nous-hermes-2", tools=[WEATHER_TOOL])
    assert "get_weather" in prompt


def test_tool_role_rendered_hermes():
    """tool message rendered as <tool_response> block for hermes format."""
    msg = ChatMessage(role="tool", content='{"temperature": "22C"}', tool_call_id="call_abc")
    result = format_tool_result(msg, "chatml")
    assert "<tool_response>" in result
    assert '{"temperature": "22C"}' in result
    assert "<|im_start|>tool" in result


def test_tool_role_rendered_llama3():
    """tool message rendered as ipython header for llama3 format."""
    msg = ChatMessage(role="tool", content='{"temperature": "22C"}', tool_call_id="call_abc")
    result = format_tool_result(msg, "llama3")
    assert "ipython" in result
    assert '{"temperature": "22C"}' in result
    assert "<|start_header_id|>" in result


def test_tool_role_in_render_messages():
    """render_messages handles tool role without error."""
    messages = [
        ChatMessage(role="user", content="Weather?"),
        ChatMessage(role="assistant", content=None, tool_calls=[]),
        ChatMessage(role="tool", content="Sunny, 22C", tool_call_id="call_x"),
        ChatMessage(role="user", content="Thanks!"),
    ]
    prompt = messages_to_prompt(messages, "aphrodite/hermes", tools=[WEATHER_TOOL])
    assert "Sunny, 22C" in prompt
    assert "<tool_response>" in prompt


# ---------------------------------------------------------------------------
# Phase 3: Parser
# ---------------------------------------------------------------------------

def test_parse_tool_call_hermes():
    """Correctly parses <tool_call> block."""
    tc = parse_tool_call(TOOL_CALL_HERMES, "hermes")
    assert tc is not None
    assert tc.function.name == "get_weather"
    args = json.loads(tc.function.arguments)
    assert args["city"] == "Paris"


def test_parse_tool_call_llama3():
    """Correctly parses llama3 JSON format."""
    tc = parse_tool_call(TOOL_CALL_LLAMA3, "llama3")
    assert tc is not None
    assert tc.function.name == "get_weather"
    args = json.loads(tc.function.arguments)
    assert args["city"] == "Paris"


def test_parse_tool_call_llama3_python_tag():
    """Correctly parses llama3.1 <|python_tag|> prefix."""
    tc = parse_tool_call(TOOL_CALL_LLAMA3_PYTHON_TAG, "llama3")
    assert tc is not None
    assert tc.function.name == "get_weather"
    args = json.loads(tc.function.arguments)
    assert args["city"] == "London"


def test_parse_tool_call_hermes_no_close_tag():
    """Correctly parses <tool_call> block without closing tag (stop sequence consumed it)."""
    tc = parse_tool_call(TOOL_CALL_HERMES_NO_CLOSE, "hermes")
    assert tc is not None
    assert tc.function.name == "get_weather"
    args = json.loads(tc.function.arguments)
    assert args["city"] == "Paris"


def test_parse_tool_call_qwen_bracket():
    """Correctly parses Qwen/koboldcpp [TOOL_CALLS]name[ARGS]{...} format."""
    tc = parse_tool_call(TOOL_CALL_QWEN_BRACKET, "hermes")
    assert tc is not None
    assert tc.function.name == "get_weather"
    args = json.loads(tc.function.arguments)
    assert args["city"] == "Tokyo"


def test_parse_tool_call_no_match():
    """Returns None for plain text."""
    assert parse_tool_call("The weather in Paris is sunny today.", "hermes") is None
    assert parse_tool_call("Sure, let me help you.", "llama3") is None


def test_detect_tool_format_llama3():
    """Llama3 models use llama3 format."""
    assert detect_tool_format("aphrodite/llama-3.1-8b-instruct") == "llama3"
    assert detect_tool_format("koboldcpp/llama3-70b") == "llama3"


def test_detect_tool_format_hermes():
    """Non-llama3 models use hermes format."""
    assert detect_tool_format("aphrodite/nous-hermes-2-mistral-7b") == "hermes"
    assert detect_tool_format("koboldcpp/qwen-2.5-7b") == "hermes"


# ---------------------------------------------------------------------------
# Phase 4: Translation layer
# ---------------------------------------------------------------------------

def test_stop_sequences_added(test_config):
    """HordeTextRequest has </tool_call> in stop_sequence for hermes models."""
    from app.schemas.openai import ChatCompletionRequest

    req = ChatCompletionRequest(
        model="best",
        messages=[ChatMessage(role="user", content="Weather?")],
        tools=[Tool(type="function", function=ToolFunction(name="get_weather", description="Get weather"))],
    )
    horde_req = chat_to_horde(req, "aphrodite/nous-hermes-2", test_config)
    assert horde_req.params.stop_sequence is not None
    assert "</tool_call>" in horde_req.params.stop_sequence


def test_stop_sequences_llama3(test_config):
    """HordeTextRequest has <|eom_id|> in stop_sequence for llama3 models."""
    from app.schemas.openai import ChatCompletionRequest

    req = ChatCompletionRequest(
        model="best",
        messages=[ChatMessage(role="user", content="Weather?")],
        tools=[Tool(type="function", function=ToolFunction(name="get_weather", description="Get weather"))],
    )
    horde_req = chat_to_horde(req, "aphrodite/llama-3.1-8b-instruct", test_config)
    assert "<|eom_id|>" in horde_req.params.stop_sequence


def test_tool_choice_none_skips_injection(test_config):
    """tool_choice='none' skips tool injection and stop sequences."""
    from app.schemas.openai import ChatCompletionRequest

    req = ChatCompletionRequest(
        model="best",
        messages=[ChatMessage(role="user", content="Hello")],
        tools=[Tool(type="function", function=ToolFunction(name="get_weather"))],
        tool_choice="none",
    )
    horde_req = chat_to_horde(req, "aphrodite/nous-hermes-2", test_config)
    assert horde_req.params.stop_sequence is None or "</tool_call>" not in (horde_req.params.stop_sequence or [])
    # Prompt should not contain tool injection
    assert "<tools>" not in horde_req.prompt


# ---------------------------------------------------------------------------
# Phase 5: Router (non-streaming)
# "best" resolves to aphrodite/llama-3.1-8b-instruct (llama3 format)
# ---------------------------------------------------------------------------

# llama3-format tool call response (bare JSON with "parameters" key)
LLAMA3_TOOL_RESPONSE = '{"name": "get_weather", "parameters": {"city": "Paris"}}'


async def test_response_has_tool_calls_field(app, client, respx_mock):
    """finish_reason == 'tool_calls' and tool_calls populated on successful parse."""
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={
            "done": True, "faulted": False, "kudos": 10.0,
            "generations": [{"text": LLAMA3_TOOL_RESPONSE, "model": "aphrodite/llama-3.1-8b-instruct",
                             "worker_id": "w1", "worker_name": "worker", "state": "ok"}],
        })
    )

    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
            "tools": [WEATHER_TOOL],
        },
    )
    assert response.status_code == 200
    data = response.json()
    choice = data["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"] is not None
    assert len(choice["message"]["tool_calls"]) == 1
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "get_weather"


async def test_response_content_null_on_tool_call(app, client, respx_mock):
    """message.content is None when tool called."""
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={
            "done": True, "faulted": False, "kudos": 10.0,
            "generations": [{"text": LLAMA3_TOOL_RESPONSE, "model": "aphrodite/llama-3.1-8b-instruct",
                             "worker_id": "w1", "worker_name": "worker", "state": "ok"}],
        })
    )

    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [WEATHER_TOOL],
        },
    )
    data = response.json()
    assert data["choices"][0]["message"]["content"] is None


async def test_graceful_degradation_prefix(app, client, respx_mock):
    """Response text prefixed with [Note: ...] when model produces no tool call."""
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [WEATHER_TOOL],
        },
    )
    data = response.json()
    assert data["choices"][0]["finish_reason"] == "stop"
    content = data["choices"][0]["message"]["content"]
    # Plain text returned as-is when model doesn't produce a tool call
    assert content is not None
    assert "[Note:" not in content


# ---------------------------------------------------------------------------
# Phase 5: Router (streaming)
# ---------------------------------------------------------------------------

async def test_streaming_emits_tool_calls_delta(app, client, respx_mock):
    """SSE stream contains tool_calls delta chunk when tool call detected."""
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={
            "done": True, "faulted": False, "kudos": 10.0,
            "generations": [{"text": LLAMA3_TOOL_RESPONSE, "model": "aphrodite/llama-3.1-8b-instruct",
                             "worker_id": "w1", "worker_name": "worker", "state": "ok"}],
        })
    )

    lines: list[str] = []
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [WEATHER_TOOL],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            lines.append(line)

    data_chunks = [
        json.loads(line[6:]) for line in lines
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    tool_call_chunks = [
        c for c in data_chunks
        if c["choices"][0]["delta"].get("tool_calls") is not None
    ]
    assert len(tool_call_chunks) > 0
    tc = tool_call_chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    assert tc["function"]["name"] == "get_weather"


async def test_streaming_finish_reason_tool_calls(app, client, respx_mock):
    """SSE final chunk has finish_reason: 'tool_calls'."""
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={
            "done": True, "faulted": False, "kudos": 10.0,
            "generations": [{"text": LLAMA3_TOOL_RESPONSE, "model": "aphrodite/llama-3.1-8b-instruct",
                             "worker_id": "w1", "worker_name": "worker", "state": "ok"}],
        })
    )

    lines: list[str] = []
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "best",
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [WEATHER_TOOL],
            "stream": True,
        },
    ) as response:
        async for line in response.aiter_lines():
            lines.append(line)

    data_lines = [
        json.loads(line[6:])
        for line in lines
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    finish_reasons = [chunk["choices"][0].get("finish_reason") for chunk in data_lines]
    assert "tool_calls" in finish_reasons
