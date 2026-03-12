"""Tests for the retry module: backoff, generic types, timeout, failure."""
from __future__ import annotations

import asyncio
import unittest.mock
from unittest.mock import AsyncMock

import pytest

from app.horde.client import HordeClient, HordeError, HordeIPTimeoutError, HordeUnsafeIPError
from app.horde.retry import HordeCorruptPromptError, HordeTimeoutError, with_retry
from app.schemas.horde import HordeGeneration, HordeImageGeneration, HordeImageStatus, HordeJobStatus




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


async def test_horde_404_treated_as_job_failure():
    """HordeError 404 during polling is treated as job failure, allowing retry."""
    submit_fn = AsyncMock(return_value="job-404")
    poll_fn = AsyncMock(side_effect=HordeError(404, "not found"))
    cancel_fn = AsyncMock()

    with pytest.raises(HordeTimeoutError):
        await with_retry(
            submit_fn, poll_fn, cancel_fn,
            max_retries=1, poll_interval=0, backoff_base=0
        )

    assert submit_fn.await_count == 2  # retried after 404


async def test_horde_non_404_error_propagates():
    """HordeError with non-404 status propagates immediately."""
    submit_fn = AsyncMock(return_value="job-500")
    poll_fn = AsyncMock(side_effect=HordeError(500, "server error"))
    cancel_fn = AsyncMock()

    with pytest.raises(HordeError) as exc_info:
        await with_retry(submit_fn, poll_fn, cancel_fn, max_retries=2, poll_interval=0)

    assert exc_info.value.status_code == 500
    submit_fn.assert_awaited_once()  # did not retry


async def test_corrupt_prompt_propagates_immediately():
    """CorruptPrompt error propagates immediately without retrying."""
    submit_fn = AsyncMock(side_effect=HordeError(400, "Prompt is corrupt", rc="CorruptPrompt"))
    poll_fn = AsyncMock()
    cancel_fn = AsyncMock()

    with pytest.raises(HordeCorruptPromptError):
        await with_retry(submit_fn, poll_fn, cancel_fn, max_retries=2, poll_interval=0, backoff_base=0)

    submit_fn.assert_awaited_once()  # no retry
    poll_fn.assert_not_awaited()


async def test_corrupt_prompt_no_cancel():
    """CorruptPrompt never has a job_id so cancel_fn is not called."""
    submit_fn = AsyncMock(side_effect=HordeError(400, "Corrupt", rc="CorruptPrompt"))
    poll_fn = AsyncMock()
    cancel_fn = AsyncMock()

    with pytest.raises(HordeCorruptPromptError):
        await with_retry(submit_fn, poll_fn, cancel_fn, max_retries=1, poll_interval=0, backoff_base=0)

    cancel_fn.assert_not_awaited()


async def test_ip_block_raises_immediately():
    """HordeIPTimeoutError in submit raises immediately without retry."""
    submit_fn = AsyncMock(side_effect=HordeIPTimeoutError("IP banned", duration_hint=60.0))
    poll_fn = AsyncMock()
    cancel_fn = AsyncMock()

    with pytest.raises(HordeIPTimeoutError):
        await with_retry(submit_fn, poll_fn, cancel_fn, max_retries=2, poll_interval=0, backoff_base=0)

    submit_fn.assert_awaited_once()  # not retried (propagates like any non-HordeError)


def test_horde_error_has_rc():
    """HordeError carries rc field."""
    e = HordeError(400, "bad request", rc="CorruptPrompt")
    assert e.rc == "CorruptPrompt"
    assert e.status_code == 400
    assert e.message == "bad request"


def test_horde_error_default_rc():
    """HordeError rc defaults to empty string."""
    e = HordeError(500, "error")
    assert e.rc == ""


def test_check_ip_block_not_blocked():
    """check_ip_block() does nothing when no block is active."""
    import httpx
    horde = HordeClient(
        base_url="https://aihorde.net/api",
        api_key="test",
        client_agent="test:0.1:test",
        global_min_request_delay=0.0,
    )
    horde.check_ip_block()  # should not raise


def test_check_ip_block_timeout_ip():
    """check_ip_block() raises HordeIPTimeoutError when cooldown is active."""
    import time
    import httpx
    horde = HordeClient(
        base_url="https://aihorde.net/api",
        api_key="test",
        client_agent="test:0.1:test",
        global_min_request_delay=0.0,
    )
    horde._ip_blocked_until = time.monotonic() + 3600.0
    horde._ip_block_reason = "TimeoutIP"

    with pytest.raises(HordeIPTimeoutError):
        horde.check_ip_block()


def test_check_ip_block_unsafe_ip():
    """check_ip_block() raises HordeUnsafeIPError when VPN block is active."""
    import time
    horde = HordeClient(
        base_url="https://aihorde.net/api",
        api_key="test",
        client_agent="test:0.1:test",
        global_min_request_delay=0.0,
    )
    horde._ip_blocked_until = time.monotonic() + 21600.0
    horde._ip_block_reason = "UnsafeIP"

    with pytest.raises(HordeUnsafeIPError):
        horde.check_ip_block()


def test_check_ip_block_expired():
    """check_ip_block() does not raise when the cooldown has elapsed."""
    import time
    horde = HordeClient(
        base_url="https://aihorde.net/api",
        api_key="test",
        client_agent="test:0.1:test",
        global_min_request_delay=0.0,
    )
    horde._ip_blocked_until = time.monotonic() - 1.0  # already expired
    horde._ip_block_reason = "TimeoutIP"

    horde.check_ip_block()  # should not raise




async def test_cancelled_error_cancels_job_and_reraises():
    """CancelledError during polling cancels the in-flight job and re-raises."""
    submit_fn = AsyncMock(return_value="job-cancel")
    poll_fn = AsyncMock(side_effect=asyncio.CancelledError())
    cancel_fn = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await with_retry(submit_fn, poll_fn, cancel_fn, max_retries=0, poll_interval=0)

    cancel_fn.assert_awaited_once_with("job-cancel")
