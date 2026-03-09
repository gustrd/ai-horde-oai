from __future__ import annotations

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


def render_messages(messages: list[ChatMessage], template_name: str = "chatml") -> str:
    """Render a list of chat messages into a prompt string."""
    tmpl = TEMPLATES.get(template_name, TEMPLATES["chatml"])
    parts: list[str] = []
    system_content: str | None = None

    for msg in messages:
        content = msg.content_as_str() or ""
        if msg.role == "system":
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
            parts.append(tmpl["user"].format(content=content))
        elif msg.role == "assistant":
            parts.append(tmpl["assistant"].format(content=content))

    # Add the assistant prefix to signal where the model should start generating
    parts.append(tmpl["assistant_prefix"])
    return "".join(parts)


def messages_to_prompt(messages: list[ChatMessage], model_name: str = "") -> str:
    """Auto-detect template and render messages to prompt."""
    template = detect_template(model_name)
    return render_messages(messages, template)
