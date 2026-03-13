"""Parse model output into OpenAI-compatible ToolCall objects."""
from __future__ import annotations

import json
import logging
import re

from app.schemas.openai import ToolCall, ToolCallFunction

logger = logging.getLogger(__name__)


from app.horde.chat_templates import detect_template_id


def detect_tool_format(model_name: str) -> str:
    """Return 'llama3' or 'hermes' based on model template."""
    tid = detect_template_id(model_name)
    if tid == "llama3":
        return "llama3"
    return "hermes"


def parse_tool_call(text: str, fmt: str) -> ToolCall | None:
    """
    Try to extract a single tool call from model output.
    Returns None if no valid tool call found.
    """
    # Universal: OpenClaw channel format (any model)
    result = _parse_openclaw_channel(text)
    if result is not None:
        return result
    if fmt == "llama3":
        return _parse_llama3(text)
    return _parse_hermes(text)


def _parse_openclaw_channel(text: str) -> ToolCall | None:
    """Parse <|start|>assistant<|channel|>tool {...}<|im_end|> format."""
    match = re.search(
        r"<\|start\|>assistant<\|[^|]*\|>tool\s*(.*?)(?:<\|im_end\|>|\Z)",
        text,
        re.DOTALL,
    )
    if match:
        try:
            raw = match.group(1).strip()
            # Strip optional "to=<name>" routing prefix (e.g. "to=read{..." → "{...")
            brace = raw.find("{")
            if brace > 0:
                raw = raw[brace:]
            data = json.loads(raw)
            result = _make_tool_call(data)
            if result is not None:
                logger.debug("tool call: parsed OpenClaw channel format")
            return result
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _make_tool_call(data: dict) -> ToolCall | None:
    name = data.get("name")
    if not name or not isinstance(name, str):
        return None
    args = data.get("arguments", data.get("parameters", {}))
    arguments = args if isinstance(args, str) else json.dumps(args)
    return ToolCall(function=ToolCallFunction(name=name, arguments=arguments))


def _parse_hermes(text: str) -> ToolCall | None:
    # Standard <tool_call>...</tool_call> (closing tag may be absent due to stop sequence)
    match = re.search(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return _make_tool_call(data)
        except (json.JSONDecodeError, TypeError):
            pass

    # Qwen/koboldcpp alternate format: [TOOL_CALLS]name[ARGS]{...}
    tc_match = re.search(r"\[TOOL_CALLS\]\s*(\w+)\s*\[ARGS\]\s*(\{.*)", text, re.DOTALL)
    if tc_match:
        name = tc_match.group(1)
        try:
            args = json.loads(tc_match.group(2))
            arguments = args if isinstance(args, str) else json.dumps(args)
            return ToolCall(function=ToolCallFunction(name=name, arguments=arguments))
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: model returned plain JSON instead of <tool_call> XML
    result = _parse_generic(text)
    if result is not None:
        logger.debug("tool call: parsed plain JSON fallback (model skipped <tool_call> wrapper)")
    return result


def _parse_llama3(text: str) -> ToolCall | None:
    # Handle <|python_tag|> prefix (llama3.1+)
    if "<|python_tag|>" in text:
        text = text.split("<|python_tag|>", 1)[1].strip()
    return _parse_generic(text)


def _parse_generic(text: str) -> ToolCall | None:
    text = text.strip()
    for stop in ["<|eot_id|>", "<|eom_id|>", "<|im_end|>"]:
        if text.endswith(stop):
            text = text[: -len(stop)].strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _make_tool_call(data)
    except (json.JSONDecodeError, ValueError):
        pass
    # Try to find JSON object starting with {"name":
    match = re.search(r'\{"name"\s*:', text)
    if match:
        try:
            data = json.loads(text[match.start():])
            return _make_tool_call(data)
        except (json.JSONDecodeError, ValueError):
            pass
    return None
