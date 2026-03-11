from __future__ import annotations

import asyncio
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


async def with_retry(
    submit_fn: Callable[[], Coroutine[Any, Any, str]],
    poll_fn: Callable[[str], Coroutine[Any, Any, StatusT]],
    cancel_fn: Callable[[str], Coroutine[Any, Any, None]],
    max_retries: int = 2,
    timeout_seconds: int = 300,
    broaden_on_retry: bool = True,
    backoff_base: float = 2.0,
    on_broaden: Callable[[], None] | None = None,
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

    for attempt in range(1 + max_retries):
        if attempt > 0:
            # Exponential backoff before retrying
            backoff = backoff_base * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)

            if broaden_on_retry and on_broaden:
                on_broaden()

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
            last_exc = HordeTimeoutError(f"Attempt {attempt + 1} timed out after {timeout_seconds}s")

        except HordeImpossibleError:
            raise  # propagate immediately — no point retrying with same model

        except HordeJobFailed as exc:
            if job_id:
                await cancel_fn(job_id)
            last_exc = exc

        except HordeError as exc:
            # CorruptPrompt must never be retried — each attempt adds suspicion.
            if exc.rc == "CorruptPrompt":
                raise HordeCorruptPromptError(exc.message) from exc
            # 404 means the job ID is no longer known to Horde (expired/cancelled).
            # Treat it as a job failure so with_retry can move on to the next attempt.
            if exc.status_code == 404:
                last_exc = HordeJobFailed(f"Job {job_id} not found (404): {exc.message}")
            else:
                raise

        except asyncio.CancelledError:
            if job_id:
                await cancel_fn(job_id)
            raise

    raise HordeTimeoutError(
        f"All {1 + max_retries} attempts exhausted. Last error: {last_exc}"
    ) from last_exc
