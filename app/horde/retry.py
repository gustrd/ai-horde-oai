from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, TypeVar, runtime_checkable

from app.horde.client import HordeError


@runtime_checkable
class HordeStatusLike(Protocol):
    """Protocol satisfied by both HordeJobStatus and HordeImageStatus."""
    done: bool
    faulted: bool
    generations: list[Any]


StatusT = TypeVar("StatusT", bound=HordeStatusLike)


class HordeTimeoutError(Exception):
    pass


class HordeJobFailed(Exception):
    pass


class HordeImpossibleError(Exception):
    """Raised when Horde reports is_possible=False (no workers for the model)."""
    pass


class HordeCorruptPromptError(Exception):
    """Raised when Horde rejects a prompt as corrupt (rc=CorruptPrompt).

    Must never be retried — each attempt adds suspicion to both the IP and account.
    """
    pass


class HordeNoModelsRemainingError(Exception):
    """Raised when all available models for an alias have been exhausted via retries."""
    pass


async def _call_maybe_async(fn: Callable[[], Any]) -> None:
    """Call *fn* and await the result if it returns a coroutine."""
    result = fn()
    if inspect.isawaitable(result):
        await result


async def with_retry(
    submit_fn: Callable[[], Coroutine[Any, Any, str]],
    poll_fn: Callable[[str], Coroutine[Any, Any, StatusT]],
    cancel_fn: Callable[[str], Coroutine[Any, Any, None]],
    max_retries: int = 2,
    timeout_seconds: int = 300,
    broaden_on_retry: bool = True,
    backoff_base: float = 2.0,
    on_broaden: Callable[[], Any] | None = None,
    on_status: Callable[[Any], None] | None = None,
    on_submit: Callable[[str], None] | None = None,
    poll_interval: float = 2.0,
) -> StatusT:
    """Submit a Horde job with auto-retry, exponential backoff, and timeout.

    Works with both text (HordeJobStatus) and image (HordeImageStatus) jobs
    as long as they share the done/faulted/generations fields.

    Args:
        submit_fn: Async function that submits a job and returns its ID.
        poll_fn: Async function that polls job status by ID.
        cancel_fn: Async function that cancels a job by ID.
        max_retries: Number of additional attempts after the first.
        timeout_seconds: Per-attempt timeout.
        broaden_on_retry: Call on_broaden before each retry.
        backoff_base: Initial backoff delay in seconds (doubles each retry).
        on_broaden: Optional callback to relax filters before retry.
        on_status: Optional callback called with each status update.
        poll_interval: Seconds between polls.

    Returns:
        Completed status object with at least one generation.

    Raises:
        HordeTimeoutError: All attempts exhausted without a result.
    """
    last_exc: Exception | None = None
    is_impossible_retry = False

    attempts_used = 0

    while is_impossible_retry or attempts_used <= max_retries:
        if attempts_used > 0 or is_impossible_retry:
            # Exponential backoff before retrying, unless it was an "impossible" error
            # which we retry after exactly 2 seconds (DEFAULT RETRY INTERVAL).
            _was_impossible = is_impossible_retry
            if is_impossible_retry:
                backoff = 2.0
                is_impossible_retry = False
            else:
                backoff = backoff_base * (2 ** (attempts_used - 1))
            await asyncio.sleep(backoff)

            # Always call on_broaden for impossible retries (ban + re-resolve the model);
            # also call it for normal retries when broaden_on_retry is enabled.
            if on_broaden and (_was_impossible or broaden_on_retry):
                await _call_maybe_async(on_broaden)

        job_id: str | None = None
        try:
            job_id = await submit_fn()
            if on_submit:
                on_submit(job_id)
            deadline = time.monotonic() + timeout_seconds

            while time.monotonic() < deadline:
                status = await poll_fn(job_id)

                if on_status:
                    on_status(status)

                if getattr(status, "is_possible", True) is False:
                    await cancel_fn(job_id)
                    raise HordeImpossibleError(f"Job {job_id}: model has no active workers (is_possible=false)")

                if status.faulted:
                    raise HordeJobFailed(f"Job {job_id} faulted")

                if status.done:
                    if status.generations:
                        return status
                    # done but no generations — treat as failure
                    raise HordeJobFailed(f"Job {job_id} done but no generations")

                await asyncio.sleep(poll_interval)

            # Timed out on this attempt
            if job_id:
                await cancel_fn(job_id)
            last_exc = HordeTimeoutError(f"Attempt {attempts_used + 1} timed out after {timeout_seconds}s")
            attempts_used += 1
            is_impossible_retry = False

        except HordeImpossibleError as exc:
            if job_id:
                await cancel_fn(job_id)
            last_exc = exc
            is_impossible_retry = True
            # DO NOT increment attempts_used so we keep retrying until no models remain.

        except HordeJobFailed as exc:
            if job_id:
                await cancel_fn(job_id)
            last_exc = exc
            attempts_used += 1
            is_impossible_retry = False

        except HordeError as exc:
            # CorruptPrompt must never be retried — each attempt adds suspicion.
            if exc.rc == "CorruptPrompt":
                raise HordeCorruptPromptError(exc.message) from exc
            # 404 means the job ID is no longer known to Horde (expired/cancelled).
            # Treat it as a job failure so with_retry can move on to the next attempt.
            if exc.status_code == 404:
                last_exc = HordeJobFailed(f"Job {job_id} not found (404): {exc.message}")
                attempts_used += 1
                is_impossible_retry = False
            else:
                raise

        except asyncio.CancelledError:
            if job_id:
                await cancel_fn(job_id)
            raise

    raise HordeTimeoutError(
        f"All {1 + max_retries} attempts exhausted. Last error: {last_exc}"
    ) from last_exc
