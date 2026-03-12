from __future__ import annotations

from contextlib import nullcontext

from fastapi import APIRouter, HTTPException, Request

from app.horde.client import HordeClient, HordeError
from app.horde.retry import HordeNoModelsRemainingError, HordeTimeoutError, with_retry
from app.horde.routing import ModelNotFoundError, ModelRouter
from app.horde.translate import completion_to_horde
from app.routers.chat import _horde_error, _log_model_ban
from app.log_store import estimate_tokens
from app.schemas.horde import HordeJobStatus
from app.schemas.openai import CompletionChoice, CompletionRequest, CompletionResponse, Usage

router = APIRouter()


@router.post("/v1/completions", response_model=CompletionResponse)
async def completions(request: Request, body: CompletionRequest) -> CompletionResponse:
    horde: HordeClient = request.app.state.horde
    model_router: ModelRouter = request.app.state.model_router
    config = request.app.state.config

    if body.stream:
        raise HTTPException(
            status_code=400,
            detail={"error": {"type": "invalid_request_error", "message": "Streaming is not supported for /v1/completions. Use /v1/chat/completions with stream=true instead."}},
        )

    try:
        models = await horde.get_enriched_models()
        real_model = await model_router.resolve(body.model, models, config=config)
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HordeError as e:
        raise _horde_error(e)

    horde_req = completion_to_horde(body, real_model, config)
    _sem = getattr(request.app.state, "horde_semaphore", None) or nullcontext()

    _current_real_model = real_model
    _current_horde_req = horde_req
    _transient_count = 0
    _tried_models: set[str] = set()

    async def _on_broaden():
        nonlocal _current_real_model, _current_horde_req, _transient_count
        _count = horde.cached_model_count(_current_real_model)
        _max_transient = config.retry.unavailable_max_transient_retries
        if _count is not None and _count > 0 and _transient_count < _max_transient:
            # Model still has workers — is_possible=False was transient, retry same model
            _transient_count += 1
            return
        # Skip this model for the rest of the request.
        # Only ban when count == 0 (truly offline); count > 0 means transient failure.
        _transient_count = 0
        _tried_models.add(_current_real_model)
        if _count is not None and _count == 0:
            horde.ban_model(_current_real_model, duration=3600.0)
            _log_model_ban(request, body.model, _current_real_model)
        try:
            _models = await horde.get_enriched_models()
            _new_model = await model_router.resolve(
                body.model, _models, config=config, exclude_models=_tried_models
            )
            if _new_model not in _tried_models:
                _current_real_model = _new_model
                _current_horde_req = _current_horde_req.model_copy(update={"models": [_current_real_model]})
            else:
                raise HordeNoModelsRemainingError(f"No models remaining for alias {body.model!r}")
        except HordeNoModelsRemainingError:
            raise
        except Exception as e:
            raise HordeNoModelsRemainingError(f"No models remaining for alias {body.model!r}") from e

    try:
        async with _sem:
            status = await with_retry(
                submit_fn=lambda: horde.submit_text_job(_current_horde_req),
                poll_fn=horde.poll_text_status,
                cancel_fn=horde.cancel_text_job,
                max_retries=config.retry.max_retries,
                timeout_seconds=config.retry.timeout_seconds,
                broaden_on_retry=config.retry.broaden_on_retry,
                backoff_base=config.retry.backoff_base,
                on_broaden=_on_broaden,
            )
    except (HordeTimeoutError, HordeNoModelsRemainingError) as e:
        request.state.log_extras = {
            "model": body.model,
            "real_model": _current_real_model,
            "prompt": body.prompt if isinstance(body.prompt, str) else str(body.prompt),
            "error": str(e),
        }
        raise HTTPException(status_code=504, detail=str(e))
    except HordeError as e:
        request.state.log_extras = {
            "model": body.model,
            "real_model": _current_real_model,
            "prompt": body.prompt if isinstance(body.prompt, str) else str(body.prompt),
            "error": e.message,
        }
        raise _horde_error(e)

    real_model = _current_real_model
    gen = status.generations[0] if status.generations else None
    prompt_str = body.prompt if isinstance(body.prompt, str) else str(body.prompt)
    response_text = gen.text if gen else ""
    request.state.log_extras = {
        "model": body.model,
        "real_model": real_model,
        "prompt": prompt_str,
        "worker": gen.worker_name or "" if gen else "",
        "worker_id": gen.worker_id or "" if gen else "",
        "kudos": status.kudos or 0.0,
        "response_text": response_text,
        "input_tokens": estimate_tokens(prompt_str),
        "output_tokens": estimate_tokens(response_text),
    }

    choices = [
        CompletionChoice(index=i, text=gen.text, finish_reason="stop")
        for i, gen in enumerate(status.generations)
    ]
    kudos = int(status.kudos)
    return CompletionResponse(
        model=body.model,
        choices=choices,
        usage=Usage(prompt_tokens=0, completion_tokens=kudos, total_tokens=kudos),
    )
