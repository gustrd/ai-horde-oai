import pytest
from app.horde.chat_templates import detect_template_id, render_messages, get_template
from app.schemas.openai import ChatMessage

def test_template_detection():
    assert detect_template_id("Meta-Llama-3-8B-Instruct") == "llama3"
    assert detect_template_id("Mistral-7B-v0.1") == "mistral"
    assert detect_template_id("Mixtral-8x7B-Instruct-v0.1") == "mistral"
    assert detect_template_id("Alpaca-7B") == "alpaca"
    assert detect_template_id("Nous-Hermes-2-ChatML") == "chatml"
    # Fallback
    assert detect_template_id("Unknown-Model-Name") == "kobold"

def test_render_kobold_fallback():
    messages = [
        ChatMessage(role="system", content="sys msg"),
        ChatMessage(role="user", content="hello"),
    ]
    prompt = render_messages(messages, "kobold")
    assert "{{[SYSTEM]}}sys msg{{[SYSTEM_END]}}" in prompt
    assert "{{[INPUT]}}hello{{[INPUT_END]}}" in prompt
    assert prompt.endswith("{{[OUTPUT]}}")

def test_render_llama3():
    messages = [
        ChatMessage(role="system", content="sys msg"),
        ChatMessage(role="user", content="hello"),
    ]
    prompt = render_messages(messages, "llama3")
    assert "<|start_header_id|>system<|end_header_id|>\n\nsys msg<|eot_id|>" in prompt
    assert "<|start_header_id|>user<|end_header_id|>\n\nhello<|eot_id|>" in prompt
    assert prompt.endswith("<|start_header_id|>assistant<|end_header_id|>\n\n")

def test_render_mistral_system_injection():
    # Mistral has no system role, it should prepend to user message
    messages = [
        ChatMessage(role="system", content="sys msg"),
        ChatMessage(role="user", content="hello"),
    ]
    prompt = render_messages(messages, "mistral")
    assert "[INST] sys msg\n\nhello [/INST]" in prompt

def test_render_assistant_message():
    messages = [
        ChatMessage(role="user", content="ping"),
        ChatMessage(role="assistant", content="pong"),
    ]
    prompt = render_messages(messages, "chatml")
    assert "<|im_start|>user\nping<|im_end|>\n" in prompt
    assert "<|im_start|>assistant\npong<|im_end|>\n" in prompt
    assert prompt.endswith("<|im_start|>assistant\n")

def test_tool_injection_chatml():
    tools = [{"function": {"name": "test_tool", "parameters": {}}}]
    messages = [
        ChatMessage(role="system", content="sys msg"),
    ]
    prompt = render_messages(messages, "chatml", tools=tools)
    assert "<tool_call>" in prompt
    assert '"name": "test_tool"' in prompt
    assert "sys msg" in prompt

def test_tool_injection_no_system_msg():
    tools = [{"function": {"name": "test_tool", "parameters": {}}}]
    messages = [
        ChatMessage(role="user", content="hello"),
    ]
    # Should insert a synthetic system message for tool injection
    prompt = render_messages(messages, "chatml", tools=tools)
    assert "<|im_start|>system\n" in prompt
    assert "<tool_call>" in prompt
    assert "<|im_start|>user\nhello<|im_end|>\n" in prompt
