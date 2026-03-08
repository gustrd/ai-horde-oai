from __future__ import annotations

import pytest

from app.horde.templates import detect_template, messages_to_prompt, render_messages
from app.schemas.openai import ChatMessage


def msg(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content)


def test_detect_llama3():
    assert detect_template("aphrodite/llama-3.1-8b-instruct") == "llama3"


def test_detect_mistral():
    assert detect_template("koboldcpp/mistral-nemo-12b") == "mistral"


def test_detect_chatml_default():
    assert detect_template("koboldcpp/phi-3-mini") == "chatml"


def test_chatml_rendering():
    messages = [
        msg("system", "You are helpful."),
        msg("user", "Hello!"),
        msg("assistant", "Hi there!"),
        msg("user", "How are you?"),
    ]
    prompt = render_messages(messages, "chatml")
    assert "<|im_start|>system" in prompt
    assert "<|im_start|>user" in prompt
    assert "<|im_start|>assistant" in prompt
    assert "You are helpful." in prompt
    assert "Hello!" in prompt
    assert "Hi there!" in prompt
    # Ends with assistant prefix
    assert prompt.endswith("<|im_start|>assistant\n")


def test_llama3_rendering():
    messages = [msg("user", "Hello")]
    prompt = render_messages(messages, "llama3")
    assert "<|start_header_id|>user<|end_header_id|>" in prompt


def test_mistral_system_injected():
    messages = [
        msg("system", "You are a pirate."),
        msg("user", "Hello"),
    ]
    prompt = render_messages(messages, "mistral")
    # System content should be injected into user message
    assert "You are a pirate." in prompt
    assert "[INST]" in prompt


def test_messages_to_prompt_auto_detects():
    messages = [msg("user", "Hi")]
    prompt = messages_to_prompt(messages, "aphrodite/llama-3.1-8b-instruct")
    assert "<|start_header_id|>" in prompt
