from __future__ import annotations

from app.config import Settings
from app.horde.templates import messages_to_prompt
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
    prompt = messages_to_prompt(request.messages, real_model)

    cap = config.max_max_tokens
    params = HordeTextParams(
        max_length=max(16, min(request.max_tokens or cap, cap)),
        max_context_length=4096,
        temperature=request.temperature,
        top_p=request.top_p,
        stop_sequence=_normalize_stop(request.stop),
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

    cap = config.max_max_tokens
    params = HordeTextParams(
        max_length=max(16, min(request.max_tokens or cap, cap)),
        max_context_length=4096,
        temperature=request.temperature,
        top_p=request.top_p,
        stop_sequence=_normalize_stop(request.stop),
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
