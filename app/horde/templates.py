from __future__ import annotations

import json

from app.schemas.openai import ChatMessage


TEMPLATES: dict[str, dict] = {
    "chatml": {
        "system": "<|im_start|>system\n{content}<|im_end|>\n",
        "user": "<|im_start|>user\n{content}<|im_end|>\n",
        "assistant": "<|im_start|>assistant\n{content}<|im_end|>\n",
        "assistant_prefix": "<|im_start|>assistant\n",
    },
    "llama3": {
        "system": "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{content}<|eot_id|>",
        "user": "<|start_header_id|>user<|end_header_id|>\n\n{content}<|eot_id|>",
        "assistant": "<|start_header_id|>assistant<|end_header_id|>\n\n{content}<|eot_id|>",
        "assistant_prefix": "<|start_header_id|>assistant<|end_header_id|>\n\n",
    },
    "mistral": {
        "system": None,  # No system role; prepend to first user message
        "user": "[INST] {content} [/INST]",
        "assistant": "{content}",
        "assistant_prefix": "",
    },
    "alpaca": {
        "system": "### System:\n{content}\n\n",
        "user": "### Instruction:\n{content}\n\n",
        "assistant": "### Response:\n{content}\n\n",
        "assistant_prefix": "### Response:\n",
    },
}


def detect_template(model_name: str) -> str:
    name = model_name.lower()
    if "llama-3" in name or "llama3" in name:
        return "llama3"
    if "mistral" in name or "mixtral" in name:
        return "mistral"
    if "alpaca" in name:
        return "alpaca"
    return "chatml"


def format_tools_for_model(tools: list[dict], template_name: str) -> str:
    """Serialize tools + instructions into a string to inject into system prompt."""
    if template_name == "llama3":
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
    else:  # hermes / chatml (default)
        return (
            f"<tools>\n{json.dumps(tools)}\n</tools>\n\n"
            "If you need to call a tool, respond ONLY with a JSON object in this format:\n"
            "<tool_call>\n"
            '{"name": "<tool_name>", "arguments": {<args>}}\n'
            "</tool_call>"
        )


def format_tool_result(message: ChatMessage, template_name: str) -> str:
    """Render a tool-role message into the correct template string."""
    content = message.content_as_str() or ""
    if template_name == "llama3":
        return f"<|start_header_id|>ipython<|end_header_id|>\n\n{content}<|eot_id|>"
    else:  # hermes / chatml
        return f"<|im_start|>tool\n<tool_response>\n{content}\n</tool_response>\n<|im_end|>\n"


def render_messages(
    messages: list[ChatMessage],
    template_name: str = "chatml",
    tools: list[dict] | None = None,
) -> str:
    """Render a list of chat messages into a prompt string."""
    tmpl = TEMPLATES.get(template_name, TEMPLATES["chatml"])
    parts: list[str] = []
    system_content: str | None = None

    tool_injection = format_tools_for_model(tools, template_name) if tools else None
    tool_injection_used = False

    for msg in messages:
        content = msg.content_as_str() or ""
        if msg.role == "system":
            if tool_injection and not tool_injection_used:
                content = content + "\n\n" + tool_injection
                tool_injection_used = True
            if tmpl["system"] is None:
                # Mistral: prepend system to first user message
                system_content = content
            else:
                parts.append(tmpl["system"].format(content=content))
        elif msg.role == "user":
            if system_content is not None:
                # Inject system content before user content for mistral
                content = f"{system_content}\n\n{content}"
                system_content = None
            if tool_injection and not tool_injection_used and tmpl["system"] is None:
                # Mistral: no system role, inject into first user message
                content = f"{tool_injection}\n\n{content}"
                tool_injection_used = True
            parts.append(tmpl["user"].format(content=content))
        elif msg.role == "assistant":
            parts.append(tmpl["assistant"].format(content=content))
        elif msg.role == "tool":
            parts.append(format_tool_result(msg, template_name))

    # No system message at all — prepend synthetic system with tool injection
    if tool_injection and not tool_injection_used and tmpl["system"] is not None:
        parts.insert(0, tmpl["system"].format(content=tool_injection))

    # Add the assistant prefix to signal where the model should start generating
    parts.append(tmpl["assistant_prefix"])
    return "".join(parts)


def messages_to_prompt(
    messages: list[ChatMessage],
    model_name: str = "",
    tools: list[dict] | None = None,
) -> str:
    """Auto-detect template and render messages to prompt."""
    template = detect_template(model_name)
    return render_messages(messages, template, tools=tools)
