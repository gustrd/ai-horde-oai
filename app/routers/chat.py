from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import nullcontext
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.horde.client import HordeClient, HordeError, HordeIPTimeoutError, HordeUnsafeIPError
from app.horde.retry import (
    HordeCorruptPromptError,
    HordeImpossibleError,
    HordeTimeoutError,
    with_retry,
)
from app.config import Settings
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


def _log_retry(
    request: Request,
    model: str,
    real_model: str,
    gen,
    kudos: float,
    log_messages: list | None,
    input_tokens: int,
    duration: float,
    reason: str,
    tool_info: str = "",
    response_text: str = "",
) -> None:
    """Emit a retry log entry (status='retry') to request_log and log_callback."""
    try:
        entry = RequestLogEntry(
            timestamp=datetime.now(),
            method=request.method,
            path=request.url.path,
            status="retry",
            duration=duration,
            model=model,
            real_model=real_model,
            worker=gen.worker_name or "" if gen else "",
            worker_id=gen.worker_id or "" if gen else "",
            kudos=kudos,
            messages=log_messages,
            error=reason,
            input_tokens=input_tokens,
            output_tokens=0,
            tool_info=tool_info,
            response_text=response_text,
        )
        request_log = getattr(request.app.state, "request_log", None)
        if request_log is not None:
            request_log.append(entry)
        log_callback = getattr(request.app.state, "log_callback", None)
        if log_callback is not None:
            log_callback(entry)
    except Exception:
        pass


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionRequest):
    horde: HordeClient = request.app.state.horde
    model_router: ModelRouter = request.app.state.model_router
    config = request.app.state.config

    # Capture request body for logging
    log_messages = [m.model_dump() for m in body.messages]

    # Check for active IP ban before touching the Horde API at all
    try:
        horde.check_ip_block()
    except HordeIPTimeoutError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HordeUnsafeIPError as e:
        raise HTTPException(status_code=503, detail=str(e))

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
        active_req["alias"] = body.model
        active_req["model"] = real_model
        active_req["max_tokens"] = body.max_tokens or config.default_max_tokens
        active_req["cancel_fn"] = horde.cancel_text_job
        active_req["messages"] = log_messages
        refresh_cb = getattr(request.app.state, "refresh_active_callback", None)
        if refresh_cb:
            refresh_cb()

    if body.stream:
        # Streaming: generator logs the entry when the stream ends
        request.state.log_extras = {"_streaming": True}
        tools_fmt: str | None = None
        if body.tools and body.tool_choice != "none":
            tools_fmt = detect_tool_format(real_model)
        _sem = getattr(request.app.state, "horde_semaphore", None) or nullcontext()
        return StreamingResponse(
            _stream_chat(
                horde, horde_req, body.model, real_model,
                config.stream_stall_timeout, request, log_messages,
                tools_fmt=tools_fmt, sem=_sem,
                max_retries=config.retry.max_retries,
                model_router=model_router,
                config=config,
            ),
            media_type="text/event-stream",
        )

    # Non-streaming: submit, poll, return (with tool-format retry)
    _TOOL_FORMAT_MAX_RETRIES = config.retry.max_retries
    gen = None
    response_text = ""
    reasoning_content: str | None = None
    tool_call: ToolCall | None = None
    _tool_info = ""
    _sem = getattr(request.app.state, "horde_semaphore", None) or nullcontext()

    input_tokens = sum(
        estimate_tokens(str(m.get("content", ""))) for m in (log_messages or [])
    )

    for _attempt in range(1 + _TOOL_FORMAT_MAX_RETRIES):
        _attempt_start = time.monotonic()

        def _on_submit(job_id: str) -> None:
            if active_req is not None:
                active_req["job_id"] = job_id

        try:
            async with _sem:
                status = await with_retry(
                    submit_fn=lambda: horde.submit_text_job(horde_req),
                    poll_fn=horde.poll_text_status,
                    cancel_fn=horde.cancel_text_job,
                    max_retries=config.retry.max_retries,
                    timeout_seconds=config.retry.timeout_seconds,
                    broaden_on_retry=config.retry.broaden_on_retry,
                    backoff_base=config.retry.backoff_base,
                    on_submit=_on_submit,
                )
        except HordeCorruptPromptError as e:
            err = str(e)
            request.state.log_extras = {
                "status": 400,
                "model": body.model,
                "real_model": real_model,
                "messages": log_messages,
                "error": err,
            }
            raise HTTPException(status_code=400, detail={"error": {"type": "invalid_request_error", "message": err}})
        except (HordeIPTimeoutError, HordeUnsafeIPError) as e:
            err = str(e)
            request.state.log_extras = {
                "status": 503,
                "model": body.model,
                "real_model": real_model,
                "messages": log_messages,
                "error": err,
            }
            raise HTTPException(status_code=503, detail=err)
        except HordeImpossibleError as e:
            horde.ban_model(real_model, duration=3600.0)
            err = f"Model {real_model!r} has no active workers on AI Horde"
            request.state.log_extras = {
                "status": "unav.",
                "model": body.model,
                "real_model": real_model,
                "messages": log_messages,
                "error": err,
            }
            raise HTTPException(status_code=503, detail=err)
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
        _attempt_dur = time.monotonic() - _attempt_start

        # Retry empty responses
        if not response_text.strip() and not reasoning_content:
            raw_len = len(gen.text if gen else "")
            _worker = gen.worker_name or "" if gen else ""
            logger.warning(
                "empty response (attempt %d/%d) raw_len=%d worker=%s",
                _attempt + 1, 1 + _TOOL_FORMAT_MAX_RETRIES, raw_len, _worker,
            )
            if _attempt < _TOOL_FORMAT_MAX_RETRIES:
                _log_retry(request, body.model, real_model, gen, status.kudos or 0.0,
                           log_messages, input_tokens, _attempt_dur, "empty response",
                           tool_info=f"raw_len={raw_len} worker={_worker}",
                           response_text=gen.text if gen else "")
                continue
            response_text = f"[GENERATION_FAILURE: empty response after {1 + _TOOL_FORMAT_MAX_RETRIES} attempts, last response {raw_len} chars]"

        # Tool call detection (non-streaming)
        tool_call = None
        _tool_info = ""
        if body.tools and body.tool_choice != "none":
            fmt = detect_tool_format(real_model)
            _snippet = response_text[:200].replace("\n", " ")
            logger.debug(
                "tool detection: attempt %d/%d fmt=%s response=%r",
                _attempt + 1, 1 + _TOOL_FORMAT_MAX_RETRIES, fmt, _snippet,
            )
            tool_call = parse_tool_call(response_text, fmt)
            if tool_call is not None:
                _tool_info = f"detected: {tool_call.function.name} (fmt={fmt})"
                logger.info("tool call detected: name=%s fmt=%s", tool_call.function.name, fmt)
            elif response_text.lstrip().startswith("<tool_call>"):
                _tool_info = f"retry: invalid format (attempt {_attempt + 1}/{1 + _TOOL_FORMAT_MAX_RETRIES}, fmt={fmt})"
                logger.warning(
                    "tool call with invalid formatting (attempt %d/%d) fmt=%s response=%r",
                    _attempt + 1, 1 + _TOOL_FORMAT_MAX_RETRIES, fmt, _snippet,
                )
                if _attempt < _TOOL_FORMAT_MAX_RETRIES:
                    _log_retry(request, body.model, real_model, gen, status.kudos or 0.0,
                               log_messages, input_tokens, _attempt_dur, "tool call invalid format",
                               tool_info=_tool_info, response_text=response_text)
                    continue
                response_text = f"[GENERATION_FAILURE: tool call invalid format after {1 + _TOOL_FORMAT_MAX_RETRIES} attempts | {_snippet}]"
            else:
                _tool_info = f"not detected: plain text response (fmt={fmt})"
                logger.info("tool call not detected: treating as text fmt=%s response=%r", fmt, _snippet)
        break

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
        "tool_info": _tool_info,
        "job_id": (getattr(request.state, "active_req", None) or {}).get("job_id") or "",
        **({"status": "tool"} if tool_call else {}),
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
    sem=None,
    max_retries: int = 2,
    model_router: ModelRouter | None = None,
    config: Settings | None = None,
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
    status_code: int | str = 200
    error_msg = ""
    _tool_info = ""

    _TOOL_FORMAT_MAX_RETRIES = max_retries
    _sem = sem or nullcontext()
    job_done = False
    try:
        # Tell the client which model was actually resolved (alias → real name)
        if real_model != alias:
            yield f": x-horde-resolved model={real_model}\n\n"

        tool_call: ToolCall | None = None
        _stream_input_tokens = sum(
            estimate_tokens(str(m.get("content", ""))) for m in (log_messages or [])
        )
        for _attempt in range(1 + _TOOL_FORMAT_MAX_RETRIES):
            _attempt_start = time.monotonic()

            # Delay between retry attempts (P1-B)
            if _attempt > 0:
                _retry_delay = (config.retry.streaming_retry_delay if config else 2.0)
                if _retry_delay > 0:
                    await asyncio.sleep(_retry_delay)

            # Check for active IP ban before submitting (P0-B)
            try:
                horde.check_ip_block()
            except (HordeIPTimeoutError, HordeUnsafeIPError) as _ip_exc:
                response_text = f"[IP_BLOCKED: {_ip_exc}]"
                status_code = 503
                break

            async with _sem:
                job_id = await horde.submit_text_job(horde_req)
                if request is not None:
                    _req = getattr(request.state, "active_req", None)
                    if _req is not None:
                        _req["job_id"] = job_id
                        _req["cancel_fn"] = horde.cancel_text_job

                last_progress = time.monotonic()
                last_queue_pos: int | None = None

                _poll_404 = False
                _abort_reason = ""  # "stall", "faulted", or ""
                _polled_once = False
                while True:
                    # Check stall timeout — no forward progress for too long.
                    # Only fires after at least one poll (can't stall before first response).
                    if _polled_once and time.monotonic() - last_progress > stall_timeout:
                        await horde.cancel_text_job(job_id)
                        _abort_reason = "stall"
                        yield f": x-horde-resubmit reason=stall\n\n"
                        break

                    try:
                        status = await horde.poll_text_status(job_id)
                    except HordeError as _poll_exc:
                        if _poll_exc.status_code == 404:
                            # Job ID expired/cancelled on Horde side — treat as empty
                            # so the outer retry loop can resubmit.
                            _poll_404 = True
                            break
                        raise
                    _polled_once = True

                    if status.done and status.generations:
                        break

                    if not status.is_possible:
                        await horde.cancel_text_job(job_id)
                        horde.ban_model(real_model, duration=3600.0)
                        _abort_reason = "impossible"
                        yield ": x-horde-abort reason=impossible\n\n"
                        break

                    if status.faulted:
                        await horde.cancel_text_job(job_id)
                        _abort_reason = "faulted"
                        break

                    # Track forward progress only: position must decrease (or job
                    # transitions to processing) to reset the stall timer.
                    # A position going backward (worker dropped/requeued) is NOT progress.
                    _pos = status.queue_position
                    if _pos is None:
                        # Transitioned to processing — always counts as progress
                        last_progress = time.monotonic()
                    elif last_queue_pos is None or _pos < last_queue_pos:
                        last_progress = time.monotonic()
                    last_queue_pos = _pos

                    # Emit SSE comment every poll — keeps the client from timing out
                    if status.queue_position is not None:
                        yield f": queue_position={status.queue_position}, eta={status.wait_time}s\n\n"
                    elif status.processing:
                        yield f": processing, eta={status.wait_time}s\n\n"
                    else:
                        yield ": polling\n\n"
                    if status.queue_position is not None and request is not None:
                        _req = getattr(request.state, "active_req", None)
                        if _req is not None:
                            _req["queue_pos"] = status.queue_position
                            _req["eta"] = status.wait_time
                            refresh_cb = getattr(request.app.state, "refresh_active_callback", None)
                            if refresh_cb:
                                refresh_cb()

                    await asyncio.sleep(2)

            if _abort_reason == "impossible":
                # Try re-resolving the alias against the now-filtered model list (P1-C)
                _fallback_model = None
                if model_router is not None:
                    try:
                        _fb_models = await horde.get_enriched_models()
                        _fallback_model = await model_router.resolve(alias, _fb_models, config=config)
                    except Exception:
                        _fallback_model = None
                if _fallback_model and _fallback_model != real_model and _attempt < _TOOL_FORMAT_MAX_RETRIES:
                    real_model = _fallback_model
                    horde_req = horde_req.model_copy(update={"models": [real_model]})
                    yield f": x-horde-resolved model={real_model}\n\n"
                    continue  # retry with new model
                # No fallback available — fail with clear message
                response_text = f"[MODEL_UNAVAILABLE: {real_model} has no active workers on AI Horde]"
                status_code = "unav."
                reasoning_content = None
                actual_model = real_model
                _attempt_dur = time.monotonic() - _attempt_start
                gen = None
                break
            if _poll_404 or _abort_reason:
                # Job gone or aborted — treat as empty so retry fires
                reasoning_content, response_text = None, ""
                actual_model = real_model
                _attempt_dur = time.monotonic() - _attempt_start
                gen = None
            else:
                gen = status.generations[0]
                reasoning_content, response_text = _split_thinking(gen.text)
                worker_name = gen.worker_name or ""
                worker_id = gen.worker_id or ""
                kudos = status.kudos or 0.0
                # Use the actual model Horde assigned (may differ from alias like "best")
                actual_model = gen.model or real_model
                _attempt_dur = time.monotonic() - _attempt_start

            # Retry empty responses
            if not response_text.strip() and not reasoning_content:
                raw_len = len(gen.text) if gen is not None else 0
                logger.warning(
                    "empty response (stream, attempt %d/%d) raw_len=%d worker=%s",
                    _attempt + 1, 1 + _TOOL_FORMAT_MAX_RETRIES, raw_len, worker_name,
                )
                if _attempt < _TOOL_FORMAT_MAX_RETRIES:
                    if request is not None and gen is not None:
                        _log_retry(request, alias, real_model, gen, kudos,
                                   log_messages, _stream_input_tokens, _attempt_dur, "empty response",
                                   tool_info=f"raw_len={raw_len} worker={worker_name}",
                                   response_text=gen.text)
                    continue
                response_text = f"[GENERATION_FAILURE: empty response after {1 + _TOOL_FORMAT_MAX_RETRIES} attempts, last response {raw_len} chars]"

            # Tool call detection — parse before streaming
            if tools_fmt:
                _snippet = response_text[:200].replace("\n", " ")
                logger.debug(
                    "tool detection (stream): attempt %d/%d fmt=%s response=%r",
                    _attempt + 1, 1 + _TOOL_FORMAT_MAX_RETRIES, tools_fmt, _snippet,
                )
                tool_call = parse_tool_call(response_text, tools_fmt)
                if tool_call is not None:
                    _tool_info = f"detected: {tool_call.function.name} (fmt={tools_fmt})"
                    logger.info("tool call detected (stream): name=%s fmt=%s", tool_call.function.name, tools_fmt)
                elif response_text.lstrip().startswith("<tool_call>"):
                    _tool_info = f"retry: invalid format (attempt {_attempt + 1}/{1 + _TOOL_FORMAT_MAX_RETRIES}, fmt={tools_fmt})"
                    logger.warning(
                        "tool call with invalid formatting (stream, attempt %d/%d) fmt=%s response=%r",
                        _attempt + 1, 1 + _TOOL_FORMAT_MAX_RETRIES, tools_fmt, _snippet,
                    )
                    if _attempt < _TOOL_FORMAT_MAX_RETRIES:
                        if request is not None and gen is not None:
                            _log_retry(request, alias, real_model, gen, kudos,
                                       log_messages, _stream_input_tokens, _attempt_dur, "tool call invalid format",
                                       tool_info=_tool_info, response_text=response_text)
                        continue
                    response_text = f"[GENERATION_FAILURE: tool call invalid format after {1 + _TOOL_FORMAT_MAX_RETRIES} attempts | {_snippet}]"
                else:
                    _tool_info = f"not detected: plain text response (fmt={tools_fmt})"
                    logger.info("tool call not detected (stream): treating as text fmt=%s response=%r", tools_fmt, _snippet)
            break

        job_done = True

        if tool_call:
            status_code = "tool"
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
        yield "data: [DONE]\n\n"

    finally:
        # Cancel the Horde job if the generator was closed before it completed
        # (handles client disconnect, CancelledError, GeneratorExit, and exceptions)
        if job_id and not job_done:
            try:
                await horde.cancel_text_job(job_id)
            except Exception:
                pass

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
                    tool_info=_tool_info,
                    job_id=job_id or "",
                )
                request_log = getattr(request.app.state, "request_log", None)
                if request_log is not None:
                    request_log.append(entry)
                log_callback = getattr(request.app.state, "log_callback", None)
                if log_callback is not None:
                    log_callback(entry)
            except Exception:
                pass


_EOS_TOKENS = (
    "<|im_end|>",
    "<|eot_id|>",
    "<|end_of_text|>",
    "</s>",
    "<|endoftext|>",
)


def _strip_eos(text: str) -> str:
    """Strip trailing EOS/stop tokens that some Horde workers leak into output."""
    t = text
    changed = True
    while changed:
        changed = False
        stripped = t.rstrip()
        for tok in _EOS_TOKENS:
            if stripped.endswith(tok):
                t = stripped[: -len(tok)]
                changed = True
                break
    return t


def _split_thinking(text: str) -> tuple[str | None, str]:
    """Split <think>...</think> reasoning from the actual response.

    Returns (reasoning_content, response_text).
    reasoning_content is None if no thinking block is present.
    If truncated mid-think (no closing tag), reasoning is None and the
    original text is returned as-is.
    """
    text = _strip_eos(text)
    if "<think>" not in text:
        return None, text
    start = text.find("<think>")
    end = text.find("</think>")
    if end == -1:
        # Truncated mid-think — can't cleanly split
        return None, text
    reasoning = text[start + len("<think>"):end]
    response = _strip_eos(text[end + len("</think>"):].lstrip("\n"))
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
