from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.horde.client import HordeClient, HordeError
from app.horde.retry import HordeTimeoutError, with_retry
from app.horde.routing import ModelNotFoundError, ModelRouter
from app.horde.translate import completion_to_horde
from app.routers.chat import _horde_error
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
        models = await horde.get_models()
        real_model = await model_router.resolve(body.model, models, config=config)
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HordeError as e:
        raise _horde_error(e)

    horde_req = completion_to_horde(body, real_model, config)

    try:
        status = await with_retry(
            submit_fn=lambda: horde.submit_text_job(horde_req),
            poll_fn=horde.poll_text_status,
            cancel_fn=horde.cancel_text_job,
            max_retries=config.retry.max_retries,
            timeout_seconds=config.retry.timeout_seconds,
            broaden_on_retry=config.retry.broaden_on_retry,
            backoff_base=config.retry.backoff_base,
        )
    except HordeTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except HordeError as e:
        raise _horde_error(e)

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
