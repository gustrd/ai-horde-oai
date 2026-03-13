from __future__ import annotations

from app.config import Settings
from app.horde.chat_templates import messages_to_prompt
from app.horde.tool_parser import detect_tool_format
from app.schemas.horde import (
    HordeModel,
    HordeTextParams,
    HordeTextRequest,
)
from app.schemas.openai import (
    ChatCompletionRequest,
    CompletionRequest,
)

_FALLBACK_MAX_CONTEXT_LENGTH = 4096


def chat_to_horde(
    request: ChatCompletionRequest,
    real_model: str,
    config: Settings,
    model_info: HordeModel | None = None,
) -> HordeTextRequest:
    """Translate an OpenAI ChatCompletionRequest to a HordeTextRequest."""
    # Serialize tools for prompt injection (skip if tool_choice == "none")
    tools: list[dict] | None = None
    if request.tools and request.tool_choice != "none":
        tools = [t.model_dump() for t in request.tools]

    prompt = messages_to_prompt(request.messages, real_model, tools=tools)

    stop_seqs = _normalize_stop(request.stop) or []
    if tools:
        fmt = detect_tool_format(real_model)
        extra_stops = ["</tool_call>"] if fmt == "hermes" else ["<|eom_id|>"]
        stop_seqs = stop_seqs + extra_stops

    params = HordeTextParams(
        max_length=_cap_max_length(request.max_tokens or config.default_max_tokens, model_info),
        max_context_length=_cap_max_context_length(model_info),
        temperature=request.temperature,
        top_p=request.top_p,
        stop_sequence=stop_seqs or None,
        n=request.n,
    )

    horde_req = HordeTextRequest(
        prompt=prompt,
        params=params,
        models=[real_model],
        trusted_workers=config.trusted_workers,
        client_agent=config.client_agent,
    )

    # Apply worker filters
    if config.worker_whitelist:
        horde_req.workers = config.worker_whitelist
    if config.worker_blocklist:
        # Horde: set workers list + worker_blacklist=True to treat workers as a blacklist
        horde_req.workers = config.worker_blocklist
        horde_req.worker_blacklist = True

    return horde_req


def completion_to_horde(
    request: CompletionRequest,
    real_model: str,
    config: Settings,
    model_info: HordeModel | None = None,
) -> HordeTextRequest:
    """Translate an OpenAI CompletionRequest to a HordeTextRequest."""
    prompt = request.prompt if isinstance(request.prompt, str) else request.prompt[0]

    params = HordeTextParams(
        max_length=_cap_max_length(request.max_tokens or config.default_max_tokens, model_info),
        max_context_length=_cap_max_context_length(model_info),
        temperature=request.temperature,
        top_p=request.top_p,
        n=request.n,
    )

    return HordeTextRequest(
        prompt=prompt,
        params=params,
        models=[real_model],
        trusted_workers=config.trusted_workers,
        client_agent=config.client_agent,
    )


def cap_params_to_model(req: HordeTextRequest, model_info: HordeModel) -> HordeTextRequest:
    """Return a copy of req with params capped to model_info's capabilities."""
    return req.model_copy(update={
        "models": [model_info.name],
        "params": req.params.model_copy(update={
            "max_length": min(req.params.max_length, model_info.max_length),
            "max_context_length": min(req.params.max_context_length, model_info.max_context_length),
        }),
    })


def _cap_max_length(requested: int, model_info: HordeModel | None) -> int:
    value = max(16, requested)
    if model_info and model_info.max_length > 0:
        value = min(value, model_info.max_length)
    return value


def _cap_max_context_length(model_info: HordeModel | None) -> int:
    if model_info and model_info.max_context_length > 0:
        return model_info.max_context_length
    return _FALLBACK_MAX_CONTEXT_LENGTH


def _normalize_stop(stop: str | list[str] | None) -> list[str] | None:
    if stop is None:
        return None
    if isinstance(stop, str):
        return [stop]
    return stop

