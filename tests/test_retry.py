"""Tests for the retry module: backoff, generic types, timeout, failure."""
from __future__ import annotations

import asyncio
import unittest.mock
from unittest.mock import AsyncMock

import pytest

from app.horde.retry import HordeTimeoutError, with_retry
from app.schemas.horde import HordeGeneration, HordeImageGeneration, HordeImageStatus, HordeJobStatus


pytestmark = pytest.mark.asyncio


def _done_text_status():
    return HordeJobStatus(
        done=True, faulted=False,
        generations=[HordeGeneration(text="hello", model="m", worker_id="w", worker_name="n", kudos=1)],
        kudos=5.0,
    )


def _pending_text_status():
    return HordeJobStatus(done=False, faulted=False, generations=[], kudos=0)


def _faulted_text_status():
    return HordeJobStatus(done=False, faulted=True, generations=[], kudos=0)


def _done_image_status():
    return HordeImageStatus(
        done=True, faulted=False,
        generations=[HordeImageGeneration(img="http://img.url", seed="1", worker_id="w", worker_name="n", model="m")],
        kudos=10.0,
    )


async def test_success_first_attempt():
    """Returns immediately on first successful poll."""
    status = _done_text_status()
    submit_fn = AsyncMock(return_value="job-1")
    poll_fn = AsyncMock(return_value=status)
    cancel_fn = AsyncMock()

    result = await with_retry(submit_fn, poll_fn, cancel_fn, max_retries=2, poll_interval=0)
    assert result is status
    submit_fn.assert_awaited_once()
    cancel_fn.assert_not_awaited()


async def test_works_with_image_status():
    """with_retry works with HordeImageStatus (generic protocol)."""
    status = _done_image_status()
    submit_fn = AsyncMock(return_value="img-job-1")
    poll_fn = AsyncMock(return_value=status)
    cancel_fn = AsyncMock()

    result = await with_retry(submit_fn, poll_fn, cancel_fn, poll_interval=0)
    assert result is status


async def test_faulted_then_cancelled():
    """Faulted job is cancelled and counted as a failure."""
    submit_fn = AsyncMock(return_value="job-2")
    poll_fn = AsyncMock(return_value=_faulted_text_status())
    cancel_fn = AsyncMock()

    with pytest.raises(HordeTimeoutError):
        await with_retry(submit_fn, poll_fn, cancel_fn, max_retries=0, poll_interval=0, backoff_base=0)

    cancel_fn.assert_awaited_once_with("job-2")


async def test_timeout_cancels_job():
    """Job that never finishes is cancelled after timeout."""
    submit_fn = AsyncMock(return_value="job-3")
    poll_fn = AsyncMock(return_value=_pending_text_status())
    cancel_fn = AsyncMock()

    with pytest.raises(HordeTimeoutError):
        await with_retry(
            submit_fn, poll_fn, cancel_fn,
            max_retries=0, timeout_seconds=0, poll_interval=0, backoff_base=0
        )

    cancel_fn.assert_awaited()


async def test_retries_on_failure():
    """Retries the specified number of times before giving up."""
    submit_fn = AsyncMock(return_value="job-4")
    poll_fn = AsyncMock(return_value=_faulted_text_status())
    cancel_fn = AsyncMock()

    with pytest.raises(HordeTimeoutError):
        await with_retry(
            submit_fn, poll_fn, cancel_fn,
            max_retries=2, poll_interval=0, backoff_base=0
        )

    assert submit_fn.await_count == 3  # 1 initial + 2 retries


async def test_calls_on_broaden():
    """on_broaden callback is called before each retry."""
    call_count = 0

    def broaden():
        nonlocal call_count
        call_count += 1

    submit_fn = AsyncMock(return_value="job-5")
    poll_fn = AsyncMock(return_value=_faulted_text_status())
    cancel_fn = AsyncMock()

    with pytest.raises(HordeTimeoutError):
        await with_retry(
            submit_fn, poll_fn, cancel_fn,
            max_retries=2, poll_interval=0, backoff_base=0,
            broaden_on_retry=True, on_broaden=broaden
        )

    assert call_count == 2  # called before retry 1 and retry 2


async def test_exponential_backoff():
    """Backoff doubles between retries."""
    sleep_times = []

    async def mock_sleep(t):
        if t > 0:
            sleep_times.append(t)

    submit_fn = AsyncMock(return_value="job-6")
    poll_fn = AsyncMock(return_value=_faulted_text_status())
    cancel_fn = AsyncMock()

    with unittest.mock.patch("app.horde.retry.asyncio.sleep", side_effect=mock_sleep):
        with pytest.raises(HordeTimeoutError):
            await with_retry(
                submit_fn, poll_fn, cancel_fn,
                max_retries=3, poll_interval=0, backoff_base=1.0
            )

    # backoff_base=1: retry 1→1s, retry 2→2s, retry 3→4s
    assert sleep_times == [1.0, 2.0, 4.0]


async def test_no_backoff_first_attempt():
    """No sleep before the first attempt."""
    sleep_times = []

    async def mock_sleep(t):
        if t > 0:
            sleep_times.append(t)

    status = _done_text_status()
    submit_fn = AsyncMock(return_value="job-7")
    poll_fn = AsyncMock(return_value=status)
    cancel_fn = AsyncMock()

    with unittest.mock.patch("app.horde.retry.asyncio.sleep", side_effect=mock_sleep):
        result = await with_retry(
            submit_fn, poll_fn, cancel_fn,
            max_retries=2, poll_interval=0, backoff_base=1.0
        )

    assert result is status
    assert sleep_times == []  # no backoff on first attempt


async def test_on_status_callback():
    """on_status callback is called with each status update."""
    statuses = []
    pending = _pending_text_status()
    done = _done_text_status()

    submit_fn = AsyncMock(return_value="job-8")
    poll_fn = AsyncMock(side_effect=[pending, done])
    cancel_fn = AsyncMock()

    await with_retry(
        submit_fn, poll_fn, cancel_fn,
        max_retries=0, poll_interval=0,
        on_status=statuses.append
    )

    assert len(statuses) == 2
    assert statuses[0] is pending
    assert statuses[1] is done


async def test_done_but_no_generations():
    """Job done with empty generations counts as failure."""
    status = HordeJobStatus(done=True, faulted=False, generations=[], kudos=0)
    submit_fn = AsyncMock(return_value="job-9")
    poll_fn = AsyncMock(return_value=status)
    cancel_fn = AsyncMock()

    with pytest.raises(HordeTimeoutError):
        await with_retry(
            submit_fn, poll_fn, cancel_fn,
            max_retries=0, poll_interval=0, backoff_base=0
        )
