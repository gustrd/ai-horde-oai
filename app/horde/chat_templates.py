from __future__ import annotations

import json
import re
from typing import TypedDict, Protocol

from pydantic import BaseModel

from app.schemas.openai import ChatMessage


class TemplateSequences(TypedDict):
    system: str | None
    user: str
    assistant: str
    assistant_prefix: str


class ChatTemplate(BaseModel):
    id: str
    name: str
    sequences: TemplateSequences


TEMPLATES: dict[str, ChatTemplate] = {
    "chatml": ChatTemplate(
        id="chatml",
        name="ChatML",
        sequences={
            "system": "<|im_start|>system\n{content}<|im_end|>\n",
            "user": "<|im_start|>user\n{content}<|im_end|>\n",
            "assistant": "<|im_start|>assistant\n{content}<|im_end|>\n",
            "assistant_prefix": "<|im_start|>assistant\n",
        },
    ),
    "llama3": ChatTemplate(
        id="llama3",
        name="Llama-3",
        sequences={
            "system": "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{content}<|eot_id|>",
            "user": "<|start_header_id|>user<|end_header_id|>\n\n{content}<|eot_id|>",
            "assistant": "<|start_header_id|>assistant<|end_header_id|>\n\n{content}<|eot_id|>",
            "assistant_prefix": "<|start_header_id|>assistant<|end_header_id|>\n\n",
        },
    ),
    "mistral": ChatTemplate(
        id="mistral",
        name="Mistral",
        sequences={
            "system": None,  # Prepend to first user message
            "user": "[INST] {content} [/INST]",
            "assistant": "{content}",
            "assistant_prefix": "",
        },
    ),

    "kobold": ChatTemplate(
        id="kobold",
        name="KoboldAI",
        sequences={
            "system": "{{{{[SYSTEM]}}}}{content}{{{{[SYSTEM_END]}}}}",
            "user": "{{{{[INPUT]}}}}{content}{{{{[INPUT_END]}}}}",
            "assistant": "{{{{[OUTPUT]}}}}{content}{{{{[OUTPUT_END]}}}}",
            "assistant_prefix": "{{{{[OUTPUT]}}}}",
        },
    ),
}

# Detection rules: (regex, template_id)
# Evaluated in order.
DETECTION_RULES = [
    (r"llama-3|llama3", "llama3"),
    (r"mistral|mixtral|24b", "mistral"),
    (r"hermes|chatml|qwen", "chatml"),
]

DEFAULT_TEMPLATE_ID = "kobold"


def detect_template_id(model_name: str) -> str:
    """Identify the template ID for a given model name."""
    name = model_name.lower()
    for pattern, tid in DETECTION_RULES:
        if re.search(pattern, name):
            return tid
    return DEFAULT_TEMPLATE_ID


def get_template(template_id: str) -> ChatTemplate:
    """Retrieve a template by ID, falling back to the default."""
    return TEMPLATES.get(template_id, TEMPLATES[DEFAULT_TEMPLATE_ID])


def format_tools_for_model(tools: list[dict], template_id: str) -> str:
    """Serialize tools + instructions into a string to inject into system prompt."""
    if template_id == "llama3":
        tool_list = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "parameters": t["function"].get("parameters", {}),
            }
            for t in tools
        ]
        return (
            "You have access to the following tools. To use a tool, respond with a JSON object:\n"
            '{"name": "<tool_name>", "parameters": {<args>}}\n\n'
            f"Available tools:\n{json.dumps(tool_list)}"
        )
    else:  # hermes / chatml / generic
        return (
            f"<tools>\n{json.dumps(tools)}\n</tools>\n\n"
            "If you need to call a tool, respond ONLY with a JSON object in this format:\n"
            "<tool_call>\n"
            '{"name": "<tool_name>", "arguments": {<args>}}\n'
            "</tool_call>"
        )


def format_tool_result(message: ChatMessage, template_id: str) -> str:
    """Render a tool-role message into the correct template string."""
    content = message.content_as_str() or ""
    if template_id == "llama3":
        return f"<|start_header_id|>ipython<|end_header_id|>\n\n{content}<|eot_id|>"
    else:  # hermes / chatml
        return f"<|im_start|>tool\n<tool_response>\n{content}\n</tool_response>\n<|im_end|>\n"


def render_messages(
    messages: list[ChatMessage],
    template_id: str,
    tools: list[dict] | None = None,
) -> str:
    """Render a list of chat messages into a prompt string using a specific template."""
    tmpl = get_template(template_id)
    seqs = tmpl.sequences
    parts: list[str] = []
    system_content: str | None = None

    tool_injection = format_tools_for_model(tools, template_id) if tools else None
    tool_injection_used = False

    for msg in messages:
        content = msg.content_as_str() or ""
        if msg.role == "system":
            if tool_injection and not tool_injection_used:
                content = content + "\n\n" + tool_injection
                tool_injection_used = True
            if seqs["system"] is None:
                # Mistral style: prepend system to first user message
                system_content = content
            else:
                parts.append(seqs["system"].format(content=content))
        elif msg.role == "user":
            if system_content is not None:
                # Inject system content before user content for models without system role
                content = f"{system_content}\n\n{content}"
                system_content = None
            if tool_injection and not tool_injection_used and seqs["system"] is None:
                content = f"{tool_injection}\n\n{content}"
                tool_injection_used = True
            parts.append(seqs["user"].format(content=content))
        elif msg.role == "assistant":
            if msg.tool_calls:
                # Render the tool call so history turns aren't empty
                tc = msg.tool_calls[0]
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, ValueError):
                    args = tc.function.arguments
                tc_json = json.dumps({"name": tc.function.name, "arguments": args})
                if template_id == "llama3":
                    content = tc_json
                else:
                    content = f"<tool_call>\n{tc_json}\n</tool_call>"
            parts.append(seqs["assistant"].format(content=content))
        elif msg.role == "tool":
            parts.append(format_tool_result(msg, template_id))

    # No system message at all — prepend synthetic system with tool injection if needed
    if tool_injection and not tool_injection_used and seqs["system"] is not None:
        parts.insert(0, seqs["system"].format(content=tool_injection))

    # Add the assistant prefix to signal where the model should start generating
    parts.append(seqs["assistant_prefix"].format(content=""))
    return "".join(parts)


def messages_to_prompt(
    messages: list[ChatMessage],
    model_name: str = "",
    tools: list[dict] | None = None,
) -> str:
    """Auto-detect template and render messages to prompt."""
    template_id = detect_template_id(model_name)
    return render_messages(messages, template_id, tools=tools)
