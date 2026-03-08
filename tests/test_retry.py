from __future__ import annotations

import pytest

from app.horde.retry import HordeTimeoutError, with_retry
from app.schemas.horde import HordeGeneration, HordeJobStatus


def make_status(done: bool = False, generations=None, faulted: bool = False) -> HordeJobStatus:
    if generations is None:
        generations = [HordeGeneration(text="Hi", model="test")] if done else []
    return HordeJobStatus(done=done, faulted=faulted, generations=generations)


@pytest.mark.asyncio
async def test_immediate_success():
    call_count = [0]

    async def submit():
        return "job-1"

    async def poll(job_id):
        call_count[0] += 1
        return make_status(done=True)

    async def cancel(job_id):
        pass

    status = await with_retry(submit, poll, cancel, max_retries=0, timeout_seconds=5)
    assert status.done
    assert status.generations[0].text == "Hi"


@pytest.mark.asyncio
async def test_retry_on_failure():
    attempt_count = [0]

    async def submit():
        attempt_count[0] += 1
        return f"job-{attempt_count[0]}"

    async def poll(job_id):
        if attempt_count[0] == 1:
            return make_status(done=True, generations=[])  # Empty — triggers retry
        return make_status(done=True)

    async def cancel(job_id):
        pass

    status = await with_retry(submit, poll, cancel, max_retries=1, timeout_seconds=5, poll_interval=0)
    assert attempt_count[0] == 2
    assert status.done


@pytest.mark.asyncio
async def test_timeout_exhausted():
    async def submit():
        return "job-1"

    async def poll(job_id):
        # Never done
        return make_status(done=False)

    async def cancel(job_id):
        pass

    with pytest.raises(HordeTimeoutError):
        await with_retry(submit, poll, cancel, max_retries=0, timeout_seconds=0, poll_interval=0)


@pytest.mark.asyncio
async def test_on_broaden_called():
    broadened = [False]
    attempt = [0]

    async def submit():
        attempt[0] += 1
        return f"job-{attempt[0]}"

    async def poll(job_id):
        if attempt[0] == 1:
            return make_status(done=True, generations=[])
        return make_status(done=True)

    async def cancel(job_id):
        pass

    def on_broaden():
        broadened[0] = True

    await with_retry(
        submit, poll, cancel,
        max_retries=1, timeout_seconds=5,
        broaden_on_retry=True, on_broaden=on_broaden,
        poll_interval=0,
    )
    assert broadened[0] is True


@pytest.mark.asyncio
async def test_faulted_job_retries():
    attempt = [0]

    async def submit():
        attempt[0] += 1
        return f"job-{attempt[0]}"

    async def poll(job_id):
        if attempt[0] == 1:
            return make_status(faulted=True)
        return make_status(done=True)

    async def cancel(job_id):
        pass

    status = await with_retry(submit, poll, cancel, max_retries=1, timeout_seconds=5, poll_interval=0)
    assert status.done
    assert attempt[0] == 2
