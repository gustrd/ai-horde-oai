from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.horde.client import HordeClient, HordeError
from app.horde.retry import HordeTimeoutError, with_retry
from app.horde.routing import ModelNotFoundError, ModelRouter
from app.horde.translate import chat_to_horde
from app.schemas.horde import HordeJobStatus
from app.schemas.openai import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    StreamChunk,
    StreamChoice,
    StreamDelta,
    Usage,
)

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionRequest):
    horde: HordeClient = request.app.state.horde
    model_router: ModelRouter = request.app.state.model_router
    config = request.app.state.config

    # Resolve model alias → real Horde model name
    try:
        models = await horde.get_models()
        real_model = await model_router.resolve(body.model, models)
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HordeError as e:
        raise _horde_error(e)

    # Build Horde request
    horde_req = chat_to_horde(body, real_model, config)

    if body.stream:
        return StreamingResponse(
            _stream_chat(horde, horde_req, body.model, real_model),
            media_type="text/event-stream",
        )

    # Non-streaming: submit, poll, return
    try:
        status = await with_retry(
            submit_fn=lambda: horde.submit_text_job(horde_req),
            poll_fn=horde.poll_text_status,
            cancel_fn=horde.cancel_text_job,
            max_retries=config.retry.max_retries,
            timeout_seconds=config.retry.timeout_seconds,
            broaden_on_retry=config.retry.broaden_on_retry,
        )
    except HordeTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except HordeError as e:
        raise _horde_error(e)

    return _build_response(status, body.model, real_model)


async def _stream_chat(
    horde: HordeClient,
    horde_req,
    alias: str,
    real_model: str,
) -> AsyncGenerator[str, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    # Send the initial role chunk
    first_chunk = StreamChunk(
        id=completion_id,
        created=created,
        model=alias,
        choices=[StreamChoice(index=0, delta=StreamDelta(role="assistant"), finish_reason=None)],
    )
    yield f"data: {first_chunk.model_dump_json()}\n\n"

    # Submit job and poll with queue position comments
    job_id: str | None = None
    try:
        job_id = await horde.submit_text_job(horde_req)

        while True:
            status = await horde.poll_text_status(job_id)

            if status.done and status.generations:
                break

            if status.faulted:
                yield "data: [DONE]\n\n"
                return

            # Emit SSE comment with queue position
            if status.queue_position is not None:
                yield f": queue_position={status.queue_position}, eta={status.wait_time}s\n\n"

            import asyncio
            await asyncio.sleep(2)

        text = status.generations[0].text

        # Stream the text word by word
        words = text.split(" ")
        for i, word in enumerate(words):
            chunk_text = word + (" " if i < len(words) - 1 else "")
            chunk = StreamChunk(
                id=completion_id,
                created=created,
                model=alias,
                choices=[StreamChoice(index=0, delta=StreamDelta(content=chunk_text), finish_reason=None)],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"

        # Final chunk with finish_reason
        final = StreamChunk(
            id=completion_id,
            created=created,
            model=alias,
            choices=[StreamChoice(index=0, delta=StreamDelta(), finish_reason="stop")],
        )
        yield f"data: {final.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    except Exception:
        if job_id:
            await horde.cancel_text_job(job_id)
        yield "data: [DONE]\n\n"


def _build_response(
    status: HordeJobStatus,
    alias: str,
    real_model: str,
) -> ChatCompletionResponse:
    choices = []
    for i, gen in enumerate(status.generations):
        choices.append(
            ChatChoice(
                index=i,
                message=ChatMessage(role="assistant", content=gen.text),
                finish_reason="stop",
            )
        )

    kudos = int(status.kudos)
    usage = Usage(
        prompt_tokens=0,
        completion_tokens=kudos,
        total_tokens=kudos,
    )

    return ChatCompletionResponse(
        model=alias,
        choices=choices,
        usage=usage,
    )


def _horde_error(e: HordeError) -> HTTPException:
    if e.status_code == 401:
        return HTTPException(status_code=401, detail={"error": {"type": "authentication_error", "message": e.message}})
    if e.status_code == 429:
        return HTTPException(status_code=429, detail={"error": {"type": "rate_limit_error", "message": e.message}})
    if e.status_code == 400:
        return HTTPException(status_code=400, detail={"error": {"type": "invalid_request_error", "message": e.message}})
    if e.status_code >= 500:
        return HTTPException(status_code=502, detail={"error": {"type": "server_error", "message": e.message}})
    return HTTPException(status_code=e.status_code, detail=e.message)
