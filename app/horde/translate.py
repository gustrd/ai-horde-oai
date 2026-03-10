from __future__ import annotations

from app.config import Settings
from app.horde.templates import messages_to_prompt
from app.horde.tool_parser import detect_tool_format
from app.schemas.horde import (
    HordeImageParams,
    HordeImageRequest,
    HordeTextParams,
    HordeTextRequest,
)
from app.schemas.openai import (
    ChatCompletionRequest,
    CompletionRequest,
    ImageGenerationRequest,
)


def chat_to_horde(
    request: ChatCompletionRequest,
    real_model: str,
    config: Settings,
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
        max_length=max(16, request.max_tokens or config.default_max_tokens),
        max_context_length=4096,
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
) -> HordeTextRequest:
    """Translate an OpenAI CompletionRequest to a HordeTextRequest."""
    prompt = request.prompt if isinstance(request.prompt, str) else request.prompt[0]

    params = HordeTextParams(
        max_length=max(16, request.max_tokens or config.default_max_tokens),
        max_context_length=4096,
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


def image_to_horde(
    request: ImageGenerationRequest,
    real_model: str,
    config: Settings,
) -> HordeImageRequest:
    """Translate an OpenAI ImageGenerationRequest to a HordeImageRequest."""
    width, height = _parse_size(request.size)
    steps = 50 if request.quality == "hd" else config.image_defaults.steps

    params = HordeImageParams(
        steps=steps,
        cfg_scale=config.image_defaults.cfg_scale,
        width=width,
        height=height,
        n=request.n,
    )

    return HordeImageRequest(
        prompt=request.prompt,
        params=params,
        models=[real_model],
        r2=(request.response_format == "url"),
        client_agent=config.client_agent,
    )


def _normalize_stop(stop: str | list[str] | None) -> list[str] | None:
    if stop is None:
        return None
    if isinstance(stop, str):
        return [stop]
    return stop


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = size.split("x")
        return int(w), int(h)
    except Exception:
        return 1024, 1024
