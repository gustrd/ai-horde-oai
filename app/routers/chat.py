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
from app.horde.tool_parser import detect_tool_format, parse_tool_call
from app.horde.translate import chat_to_horde
from app.log_store import RequestLogEntry, estimate_tokens
from app.schemas.horde import HordeJobStatus
from app.schemas.openai import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    StreamChunk,
    StreamChoice,
    StreamDelta,
    ToolCall,
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
        models = await horde.get_enriched_models()
        real_model = await model_router.resolve(body.model, models, config=config)
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HordeError as e:
        raise _horde_error(e)

    # Build Horde request
    horde_req = chat_to_horde(body, real_model, config)

    # Enrich the active-request indicator with model + token budget
    active_req = getattr(request.state, "active_req", None)
    if active_req is not None:
        active_req["model"] = real_model
        active_req["max_tokens"] = body.max_tokens or config.default_max_tokens
        refresh_cb = getattr(request.app.state, "refresh_active_callback", None)
        if refresh_cb:
            refresh_cb()

    if body.stream:
        # Streaming: generator logs the entry when the stream ends
        request.state.log_extras = {"_streaming": True}
        tools_fmt: str | None = None
        if body.tools and body.tool_choice != "none":
            tools_fmt = detect_tool_format(real_model)
        return StreamingResponse(
            _stream_chat(
                horde, horde_req, body.model, real_model,
                config.stream_stall_timeout, request, log_messages,
                tools_fmt=tools_fmt,
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
    reasoning_content, response_text = _split_thinking(gen.text if gen else "")
    input_tokens = sum(
        estimate_tokens(str(m.get("content", ""))) for m in (log_messages or [])
    )

    # Tool call detection (non-streaming)
    tool_call: ToolCall | None = None
    if body.tools and body.tool_choice != "none":
        fmt = detect_tool_format(real_model)
        tool_call = parse_tool_call(response_text, fmt)

    request.state.log_extras = {
        "model": body.model,
        "real_model": real_model,
        "messages": log_messages,
        "worker": gen.worker_name or "" if gen else "",
        "worker_id": gen.worker_id or "" if gen else "",
        "kudos": status.kudos or 0.0,
        "response_text": response_text,
        "input_tokens": input_tokens,
        "output_tokens": estimate_tokens(response_text),
        "reasoning_content": reasoning_content or "",
        "reasoning_tokens": estimate_tokens(reasoning_content or ""),
    }
    if tool_call:
        return _build_tool_response(status, body.model, real_model, tool_call)
    return _build_response(status, body.model, real_model, response_text, reasoning_content)


async def _stream_chat(
    horde: HordeClient,
    horde_req,
    alias: str,
    real_model: str,
    stall_timeout: int = 120,
    request: Request | None = None,
    log_messages: list | None = None,
    tools_fmt: str | None = None,
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
    reasoning_content: str | None = None
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
                if request is not None:
                    _req = getattr(request.state, "active_req", None)
                    if _req is not None:
                        _req["queue_pos"] = status.queue_position
                        _req["eta"] = status.wait_time
                        refresh_cb = getattr(request.app.state, "refresh_active_callback", None)
                        if refresh_cb:
                            refresh_cb()

            await asyncio.sleep(2)

        gen = status.generations[0]
        reasoning_content, response_text = _split_thinking(gen.text)
        worker_name = gen.worker_name or ""
        worker_id = gen.worker_id or ""
        kudos = status.kudos or 0.0
        # Use the actual model Horde assigned (may differ from alias like "best")
        actual_model = gen.model or real_model

        # Tool call detection — parse before streaming
        tool_call: ToolCall | None = None
        if tools_fmt:
            tool_call = parse_tool_call(response_text, tools_fmt)

        if tool_call:
            # Emit tool_calls delta chunks (OpenClaw / OpenAI streaming protocol)
            # Chunk 1: tool name with empty arguments
            yield (
                f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': actual_model, 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': tool_call.id, 'type': 'function', 'function': {'name': tool_call.function.name, 'arguments': ''}}]}, 'finish_reason': None}]})}\n\n"
            )
            # Chunk 2: arguments in 4-char pieces
            args = tool_call.function.arguments
            chunk_size = 4
            for i in range(0, len(args), chunk_size):
                yield (
                    f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': actual_model, 'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': args[i:i + chunk_size]}}]}, 'finish_reason': None}]})}\n\n"
                )
            # Worker metadata comment
            yield (
                f": x-horde-worker"
                f" name={worker_name}"
                f" id={worker_id}"
                f" model={actual_model}"
                f" kudos={kudos:.1f}\n\n"
            )
            # Final chunk with finish_reason="tool_calls"
            yield (
                f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': actual_model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]})}\n\n"
            )
            yield "data: [DONE]\n\n"
        else:
            # Stream reasoning_content first (if present), then content
            chunk_size = 4
            if reasoning_content:
                for i in range(0, len(reasoning_content), chunk_size):
                    chunk = StreamChunk(
                        id=completion_id,
                        created=created,
                        model=actual_model,
                        choices=[StreamChoice(
                            index=0,
                            delta=StreamDelta(reasoning_content=reasoning_content[i:i + chunk_size]),
                            finish_reason=None,
                        )],
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"

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
                    input_tokens=sum(
                        estimate_tokens(str(m.get("content", "")))
                        for m in (log_messages or [])
                    ),
                    output_tokens=estimate_tokens(response_text),
                    reasoning_content=reasoning_content or "",
                    reasoning_tokens=estimate_tokens(reasoning_content or ""),
                )
                request_log = getattr(request.app.state, "request_log", None)
                if request_log is not None:
                    request_log.append(entry)
                log_callback = getattr(request.app.state, "log_callback", None)
                if log_callback is not None:
                    log_callback(entry)
            except Exception:
                pass


def _split_thinking(text: str) -> tuple[str | None, str]:
    """Split <think>...</think> reasoning from the actual response.

    Returns (reasoning_content, response_text).
    reasoning_content is None if no thinking block is present.
    If truncated mid-think (no closing tag), reasoning is None and the
    original text is returned as-is.
    """
    if "<think>" not in text:
        return None, text
    start = text.find("<think>")
    end = text.find("</think>")
    if end == -1:
        # Truncated mid-think — can't cleanly split
        return None, text
    reasoning = text[start + len("<think>"):end]
    response = text[end + len("</think>"):].lstrip("\n")
    return reasoning, response


def _build_response(
    status: HordeJobStatus,
    alias: str,
    real_model: str,
    response_text: str | None = None,
    reasoning_content: str | None = None,
) -> ChatCompletionResponse:
    choices = []
    for i, gen in enumerate(status.generations):
        if i == 0 and response_text is not None:
            rc, text = reasoning_content, response_text
        else:
            rc, text = _split_thinking(gen.text)
        choices.append(
            ChatChoice(
                index=i,
                message=ChatMessage(role="assistant", content=text, reasoning_content=rc),
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


def _build_tool_response(
    status: HordeJobStatus,
    alias: str,
    real_model: str,
    tool_call: ToolCall,
) -> ChatCompletionResponse:
    kudos = int(status.kudos)
    usage = Usage(
        prompt_tokens=0,
        completion_tokens=kudos,
        total_tokens=kudos,
    )
    return ChatCompletionResponse(
        model=alias,
        choices=[
            ChatChoice(
                index=0,
                message=ChatMessage(role="assistant", content=None, tool_calls=[tool_call]),
                finish_reason="tool_calls",
            )
        ],
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
