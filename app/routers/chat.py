from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.horde.client import HordeClient, HordeError
from app.horde.retry import HordeTimeoutError, with_retry
from app.horde.routing import ModelNotFoundError, ModelRouter
from app.horde.translate import chat_to_horde
from app.log_store import RequestLogEntry
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

    # Capture request body for logging
    log_messages = [m.model_dump() for m in body.messages]

    # Resolve model alias → real Horde model name
    try:
        models = await horde.get_models()
        real_model = await model_router.resolve(body.model, models, config=config)
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HordeError as e:
        raise _horde_error(e)

    # Build Horde request
    horde_req = chat_to_horde(body, real_model, config)

    if body.stream:
        # Streaming: generator logs the entry when the stream ends
        request.state.log_extras = {"_streaming": True}
        return StreamingResponse(
            _stream_chat(
                horde, horde_req, body.model, real_model,
                config.stream_stall_timeout, request, log_messages,
            ),
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
            backoff_base=config.retry.backoff_base,
        )
    except HordeTimeoutError as e:
        request.state.log_extras = {
            "model": body.model,
            "real_model": real_model,
            "messages": log_messages,
            "error": str(e),
        }
        raise HTTPException(status_code=504, detail=str(e))
    except HordeError as e:
        request.state.log_extras = {
            "model": body.model,
            "real_model": real_model,
            "messages": log_messages,
            "error": e.message,
        }
        raise _horde_error(e)

    gen = status.generations[0] if status.generations else None
    request.state.log_extras = {
        "model": body.model,
        "real_model": real_model,
        "messages": log_messages,
        "worker": gen.worker_name or "" if gen else "",
        "worker_id": gen.worker_id or "" if gen else "",
        "kudos": gen.kudos or 0.0 if gen else 0.0,
        "response_text": gen.text if gen else "",
    }
    return _build_response(status, body.model, real_model)


async def _stream_chat(
    horde: HordeClient,
    horde_req,
    alias: str,
    real_model: str,
    stall_timeout: int = 120,
    request: Request | None = None,
    log_messages: list | None = None,
) -> AsyncGenerator[str, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    gen_start = time.monotonic()

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
    worker_name = ""
    worker_id = ""
    kudos = 0.0
    response_text = ""
    status_code = 200
    error_msg = ""

    try:
        job_id = await horde.submit_text_job(horde_req)

        # Tell the client which model was actually resolved (alias → real name)
        if real_model != alias:
            yield f": x-horde-resolved model={real_model}\n\n"

        last_progress = time.monotonic()
        last_queue_pos: int | None = None

        while True:
            # Check stall timeout — no progress (queue movement) for too long
            if time.monotonic() - last_progress > stall_timeout:
                yield "data: [DONE]\n\n"
                return

            status = await horde.poll_text_status(job_id)

            if status.done and status.generations:
                break

            if status.faulted:
                yield "data: [DONE]\n\n"
                return

            # Track progress: queue position changing counts as progress
            if status.queue_position != last_queue_pos:
                last_progress = time.monotonic()
                last_queue_pos = status.queue_position

            # Emit SSE comment with queue position
            if status.queue_position is not None:
                yield f": queue_position={status.queue_position}, eta={status.wait_time}s\n\n"

            await asyncio.sleep(2)

        gen = status.generations[0]
        response_text = gen.text
        worker_name = gen.worker_name or ""
        worker_id = gen.worker_id or ""
        kudos = gen.kudos or 0.0
        # Use the actual model Horde assigned (may differ from alias like "best")
        actual_model = gen.model or real_model

        # Stream the text in small chunks (character groups) to simulate token streaming
        chunk_size = 4
        for i in range(0, len(response_text), chunk_size):
            chunk_text = response_text[i:i + chunk_size]
            chunk = StreamChunk(
                id=completion_id,
                created=created,
                model=actual_model,
                choices=[StreamChoice(index=0, delta=StreamDelta(content=chunk_text), finish_reason=None)],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"

        # Emit worker metadata as an SSE comment before closing
        yield (
            f": x-horde-worker"
            f" name={worker_name}"
            f" id={worker_id}"
            f" model={actual_model}"
            f" kudos={kudos:.1f}\n\n"
        )

        # Final chunk with finish_reason
        final = StreamChunk(
            id=completion_id,
            created=created,
            model=actual_model,
            choices=[StreamChoice(index=0, delta=StreamDelta(), finish_reason="stop")],
        )
        yield f"data: {final.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as exc:
        error_msg = str(exc) or type(exc).__name__
        status_code = 500
        if job_id:
            await horde.cancel_text_job(job_id)
        yield "data: [DONE]\n\n"

    finally:
        # Log the completed streaming request
        if request is not None:
            try:
                entry = RequestLogEntry(
                    timestamp=datetime.now(),
                    method="POST",
                    path="/v1/chat/completions",
                    status=status_code,
                    duration=time.monotonic() - gen_start,
                    model=alias,
                    real_model=real_model,
                    worker=worker_name,
                    worker_id=worker_id,
                    kudos=kudos,
                    messages=log_messages,
                    response_text=response_text,
                    error=error_msg,
                )
                request_log = getattr(request.app.state, "request_log", None)
                if request_log is not None:
                    request_log.append(entry)
                log_callback = getattr(request.app.state, "log_callback", None)
                if log_callback is not None:
                    log_callback(entry)
            except Exception:
                pass


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
