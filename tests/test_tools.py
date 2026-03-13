"""Tests for tool/function calling implementation."""
from __future__ import annotations

import json

import httpx
import pytest

from app.horde.chat_templates import format_tool_result, format_tools_for_model, messages_to_prompt
from app.horde.tool_parser import detect_tool_format, parse_tool_call
from app.horde.translate import chat_to_horde
from app.main import create_app
from app.schemas.openai import ChatMessage, Tool, ToolCall, ToolCallFunction, ToolFunction
from tests.conftest import MODELS_FIXTURE



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

# Plain JSON responses (no <tool_call> wrapper) — fallback format
# OpenClaw channel format: <|start|>assistant<|channel|>tool {...}<|im_end|>
TOOL_CALL_OPENCLAW = '<|start|>assistant<|channel|>tool {"name": "get_weather", "arguments": {"city": "Paris"}} <|im_end|>'
TOOL_CALL_OPENCLAW_MULTILINE = '''\
<|start|>assistant<|channel|>tool {
  "name": "read",
  "arguments": {
    "path": "/root/.openclaw/workspace/HEARTBEAT.md"
  }
}
<|im_end|>'''
TOOL_CALL_OPENCLAW_NO_CLOSE = '<|start|>assistant<|channel|>tool {"name": "get_weather", "arguments": {"city": "Tokyo"}}'
TOOL_CALL_OPENCLAW_WITH_PREAMBLE = 'Reading HEARTBEAT.md.<|channel|>commentary<|message|>Reading HEARTBEAT.md.<|end|><|start|>assistant<|channel|>tool {"name": "read", "arguments": {"path": "/tmp/foo.md"}} <|im_end|>'
TOOL_CALL_OPENCLAW_TO_PREFIX = '<|channel|>analysis<|message|>Let\'s read memory/2026-03-10.md.<|end|><|start|>assistant<|channel|>tool to=read{\n "name": "read",\n "arguments": {"path": "/root/.openclaw/workspace/memory/2026-03-10.md"}\n }'
TOOL_CALL_OPENCLAW_SPACE_IN_TOKEN = '<|end|><|start|>assistant<|chann el|>tool to=process{\n "name": "process",\n "arguments": {"action": "list"}\n }<|im_end|>'

TOOL_CALL_JSON_COMPACT = '{"name": "get_weather", "arguments": {"city": "Paris"}}'
TOOL_CALL_JSON_PRETTY = '''{
  "name": "web_search",
  "arguments": {
    "query": "bolsas de valores hoje",
    "count": 3,
    "country": "BR",
    "language": "pt"
  }
}'''
TOOL_CALL_JSON_PARAMETERS_KEY = '{"name": "get_weather", "parameters": {"city": "Berlin"}}'

# Plain JSON response for a hermes-format model (koboldcpp/mistral-nemo-12b)
HERMES_JSON_TOOL_RESPONSE = '{"name": "get_weather", "arguments": {"city": "Paris"}}'


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


def _make_assistant_tool_call_msg(name: str, arguments: dict) -> ChatMessage:
    tc = ToolCall(function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))
    return ChatMessage(role="assistant", content=None, tool_calls=[tc])


def test_assistant_tool_call_renders_nonempty_hermes():
    """Assistant message with tool_calls renders <tool_call> JSON, not empty string."""
    messages = [
        ChatMessage(role="user", content="What's the weather?"),
        _make_assistant_tool_call_msg("get_weather", {"city": "Paris"}),
        ChatMessage(role="tool", content='{"temp": "22C"}', tool_call_id="call_1"),
    ]
    prompt = messages_to_prompt(messages, "aphrodite/nous-hermes-2", tools=[WEATHER_TOOL])
    assert "<tool_call>" in prompt
    assert "get_weather" in prompt
    assert "Paris" in prompt
    # Must not contain an empty assistant block
    assert "<|im_start|>assistant\n<|im_end|>" not in prompt


def test_assistant_tool_call_renders_nonempty_llama3():
    """Assistant message with tool_calls renders raw JSON for llama3, not empty string."""
    messages = [
        ChatMessage(role="user", content="What's the weather?"),
        _make_assistant_tool_call_msg("get_weather", {"city": "London"}),
        ChatMessage(role="tool", content='{"temp": "18C"}', tool_call_id="call_2"),
    ]
    prompt = messages_to_prompt(messages, "aphrodite/llama-3.1-8b-instruct", tools=[WEATHER_TOOL])
    assert "get_weather" in prompt
    assert "London" in prompt
    # llama3 assistant block must not be empty
    assert "<|end_header_id|>\n\n<|eot_id|>" not in prompt


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


def test_parse_tool_call_openclaw_channel():
    """Parses <|start|>assistant<|channel|>tool {...}<|im_end|> format."""
    tc = parse_tool_call(TOOL_CALL_OPENCLAW, "hermes")
    assert tc is not None
    assert tc.function.name == "get_weather"
    assert json.loads(tc.function.arguments)["city"] == "Paris"


def test_parse_tool_call_openclaw_channel_multiline():
    """Parses multi-line JSON in OpenClaw channel format."""
    tc = parse_tool_call(TOOL_CALL_OPENCLAW_MULTILINE, "hermes")
    assert tc is not None
    assert tc.function.name == "read"
    assert json.loads(tc.function.arguments)["path"] == "/root/.openclaw/workspace/HEARTBEAT.md"


def test_parse_tool_call_openclaw_no_close_tag():
    """Parses OpenClaw channel format without closing <|im_end|>."""
    tc = parse_tool_call(TOOL_CALL_OPENCLAW_NO_CLOSE, "hermes")
    assert tc is not None
    assert tc.function.name == "get_weather"
    assert json.loads(tc.function.arguments)["city"] == "Tokyo"


def test_parse_tool_call_openclaw_with_preamble():
    """Parses OpenClaw channel format with commentary preamble before the tool block."""
    tc = parse_tool_call(TOOL_CALL_OPENCLAW_WITH_PREAMBLE, "hermes")
    assert tc is not None
    assert tc.function.name == "read"
    assert json.loads(tc.function.arguments)["path"] == "/tmp/foo.md"


def test_parse_tool_call_openclaw_llama3_format():
    """OpenClaw channel format is parsed regardless of model format hint."""
    tc = parse_tool_call(TOOL_CALL_OPENCLAW, "llama3")
    assert tc is not None
    assert tc.function.name == "get_weather"


def test_parse_tool_call_openclaw_to_prefix():
    """Parses OpenClaw format with 'to=<name>' token between 'tool' and JSON."""
    tc = parse_tool_call(TOOL_CALL_OPENCLAW_TO_PREFIX, "hermes")
    assert tc is not None
    assert tc.function.name == "read"
    assert json.loads(tc.function.arguments)["path"] == "/root/.openclaw/workspace/memory/2026-03-10.md"


def test_parse_tool_call_openclaw_space_in_channel_token():
    """Parses OpenClaw format when model emits '<|chann el|>' with a space in the token."""
    tc = parse_tool_call(TOOL_CALL_OPENCLAW_SPACE_IN_TOKEN, "hermes")
    assert tc is not None
    assert tc.function.name == "process"
    assert json.loads(tc.function.arguments)["action"] == "list"


def test_parse_tool_call_hermes_plain_json_compact():
    """Hermes parser falls back to plain JSON when no <tool_call> wrapper present."""
    tc = parse_tool_call(TOOL_CALL_JSON_COMPACT, "hermes")
    assert tc is not None
    assert tc.function.name == "get_weather"
    args = json.loads(tc.function.arguments)
    assert args["city"] == "Paris"


def test_parse_tool_call_hermes_plain_json_pretty():
    """Hermes parser handles pretty-printed JSON tool call (multi-line, no wrapper)."""
    tc = parse_tool_call(TOOL_CALL_JSON_PRETTY, "hermes")
    assert tc is not None
    assert tc.function.name == "web_search"
    args = json.loads(tc.function.arguments)
    assert args["query"] == "bolsas de valores hoje"
    assert args["count"] == 3
    assert args["country"] == "BR"


def test_parse_tool_call_hermes_plain_json_parameters_key():
    """Hermes JSON fallback accepts 'parameters' as alias for 'arguments'."""
    tc = parse_tool_call(TOOL_CALL_JSON_PARAMETERS_KEY, "hermes")
    assert tc is not None
    assert tc.function.name == "get_weather"
    args = json.loads(tc.function.arguments)
    assert args["city"] == "Berlin"


def test_parse_tool_call_hermes_plain_json_missing_name():
    """Hermes JSON fallback returns None when JSON has no 'name' field."""
    assert parse_tool_call('{"arguments": {"city": "Paris"}}', "hermes") is None


def test_parse_tool_call_hermes_xml_still_works_after_fallback():
    """Standard <tool_call> XML format still parses correctly (regression)."""
    tc = parse_tool_call(TOOL_CALL_HERMES, "hermes")
    assert tc is not None
    assert tc.function.name == "get_weather"


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


# ---------------------------------------------------------------------------
# Phase 5b: Router — hermes model returning plain JSON (no <tool_call> wrapper)
# Uses koboldcpp/mistral-nemo-12b which is hermes format
# ---------------------------------------------------------------------------

@pytest.fixture
def hermes_app(mock_horde):
    from app.config import Settings
    config = Settings(
        horde_api_key="test-key-0000",
        horde_api_url="https://aihorde.net/api",
        default_model="koboldcpp/mistral-nemo-12b",
        retry={"max_retries": 1, "timeout_seconds": 10, "broaden_on_retry": False},
    )
    return create_app(config)


@pytest.fixture
async def hermes_client(hermes_app):
    from app.horde.client import HordeClient
    from app.horde.routing import ModelRouter

    config = hermes_app.state.config
    horde = HordeClient(
        base_url=config.horde_api_url,
        api_key=config.horde_api_key,
        client_agent=config.client_agent,
        model_cache_ttl=config.model_cache_ttl,
        global_min_request_delay=0,
    )
    hermes_app.state.horde = horde
    hermes_app.state.model_router = ModelRouter(config)
    transport = httpx.ASGITransport(app=hermes_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await horde.close()


async def test_hermes_plain_json_tool_call_non_streaming(hermes_app, hermes_client, respx_mock):
    """Hermes model returning plain JSON (no XML wrapper) is parsed as a tool call."""
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={
            "done": True, "faulted": False, "kudos": 8.0,
            "generations": [{"text": HERMES_JSON_TOOL_RESPONSE, "model": "koboldcpp/mistral-nemo-12b",
                             "worker_id": "w2", "worker_name": "worker2", "state": "ok"}],
        })
    )

    response = await hermes_client.post(
        "/v1/chat/completions",
        json={
            "model": "koboldcpp/mistral-nemo-12b",
            "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
            "tools": [WEATHER_TOOL],
        },
    )
    assert response.status_code == 200
    data = response.json()
    choice = data["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    tc = choice["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"])["city"] == "Paris"


async def test_hermes_plain_json_tool_call_streaming(hermes_app, hermes_client, respx_mock):
    """Hermes model returning plain JSON emits correct tool_calls delta in SSE stream."""
    respx_mock.get("https://aihorde.net/api/v2/generate/text/status/test-job-id").mock(
        return_value=httpx.Response(200, json={
            "done": True, "faulted": False, "kudos": 8.0,
            "generations": [{"text": HERMES_JSON_TOOL_RESPONSE, "model": "koboldcpp/mistral-nemo-12b",
                             "worker_id": "w2", "worker_name": "worker2", "state": "ok"}],
        })
    )

    lines: list[str] = []
    async with hermes_client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "koboldcpp/mistral-nemo-12b",
            "messages": [{"role": "user", "content": "Weather in Paris?"}],
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
    finish_reasons = [c["choices"][0].get("finish_reason") for c in data_chunks]
    assert "tool_calls" in finish_reasons


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
