# Double-Check Before Banning an Unavailable Model

## Problem Report

**User observation:** `koboldcpp/Cydonia-24B-v4.3` was banned by this proxy as
"unavailable" (`is_possible=False`), yet the exact same request sent directly
to Horde by another client succeeded immediately.

**Submitted payload:**
```json
{
  "models": ["koboldcpp/Cydonia-24B-v4.3"],
  "params": {
    "max_context_length": 4096,
    "max_length": 504,
    ...
  },
  "workers": []
}
```

No exotic parameters. No worker filter. The model has active workers — the other
client proved this. The proxy banned it for 1 hour based on a single
`is_possible=False` poll response.

---

## Root Cause Analysis

### What `is_possible=False` actually means

Horde sets `is_possible=False` on a queued job when, **at that specific polling
moment**, no online worker's capabilities match the job's requirements. This is a
**snapshot** — it reflects Horde's view at one instant and can flip back to
`is_possible=True` within seconds if:

| Scenario | Duration |
|---|---|
| A worker briefly disconnects (network hiccup, restart) | Seconds to minutes |
| A worker is between jobs (idle cooldown) | Seconds |
| Horde's internal worker-capability index is momentarily stale | Seconds |
| Another job ahead in queue is occupying the only matching worker | Variable |

### How the proxy currently reacts

Both the non-streaming (`with_retry`) and streaming (`_stream_chat`) paths treat
`is_possible=False` as a hard signal and **immediately ban the model for 1 hour**
on the first occurrence:

```
# retry.py line 127-129
if getattr(status, "is_possible", True) is False:
    await cancel_fn(job_id)
    raise HordeImpossibleError(...)      ← triggers on_broaden → ban_model(1h)

# _stream_chat line 457-462
if not status.is_possible:
    horde.ban_model(real_model, duration=3600.0)   ← immediate 1h ban
```

A single transient `is_possible=False` response silences the model for the
entire next hour, even if the model had workers 2 seconds later.

### Why the other client is unaffected

Direct Horde API clients re-submit without banning. The model re-enters the
queue, gets picked up by a now-available worker, and completes. The proxy's
aggressive local ban is the only reason it fails.

---

## Proposed Fix: Cross-Reference the Live Model List Before Banning

Before committing to a ban, the proxy should **verify the model's current worker
count** using the already-cached model list (no extra API call in the common
case):

```
is_possible=False detected
        │
        ▼
  horde.get_models()  ← uses TTL cache (no new request if < 60 s old)
        │
   model found?
   ┌────┴────┐
  Yes        No
   │          │
 count > 0?  Ignore, the model was out anyway
 ┌───┴───┐
Yes      No
 │        │
Retry    Ban
(transient) (truly offline)
```

If the model still appears in Horde's model list with **at least one worker**
(`count > 0`), the `is_possible=False` was transient. Retry without banning
(up to a configurable number of transient retries). Only ban when:
- The model no longer appears in the live model list, **or**
- Its worker count is 0, **or**
- It fails the transient-retry budget

---

## Implementation Plan

### 1. New config field — `RetrySettings`

**File:** `app/config.py`

```python
class RetrySettings(BaseModel):
    ...
    unavailable_recheck: bool = True
    # When is_possible=False, verify worker count before banning.
    # Disable only if you want the old aggressive-ban behavior.

    unavailable_max_transient_retries: int = 2
    # Max times to retry a model that shows is_possible=False but still has
    # workers in the live model list. Each retry uses poll_interval as delay.
```

---

### 2. `HordeClient` — `model_worker_count(name)` helper

**File:** `app/horde/client.py`

```python
async def model_worker_count(self, name: str) -> int:
    """Return the worker count for *name* from the cached model list.

    Returns 0 if the model is not found. Uses the existing TTL cache —
    no additional API call if the cache is fresh.
    """
    models = await self.get_models(type="text")
    for m in models:
        if m.name == name:
            return m.count
    return 0
```

---

### 3. `with_retry` — verify before ban

**File:** `app/horde/retry.py`

Add an optional `verify_fn` parameter:

```python
async def with_retry(
    ...
    verify_fn: Callable[[str], Coroutine[Any, Any, bool]] | None = None,
    # Given a model name, returns True if it should be banned (truly unavailable).
    # If None or returns False, the impossible error is treated as transient.
    unavailable_max_transient_retries: int = 2,
) -> StatusT:
```

When `HordeImpossibleError` is caught:
```python
except HordeImpossibleError as exc:
    if job_id:
        await cancel_fn(job_id)
    last_exc = exc
    # Verify before banning
    if verify_fn:
        truly_unavailable = await verify_fn(current_model)
        if not truly_unavailable and transient_count < unavailable_max_transient_retries:
            transient_count += 1
            is_impossible_retry = True        # retry same model, no ban
            continue
    # Either verify says it's truly unavailable, or verify is disabled,
    # or we exceeded the transient budget → let on_broaden ban it.
    is_impossible_retry = True
    await _call_maybe_async(on_broaden)       # ban + re-resolve
```

---

### 4. Streaming path — same guard

**File:** `app/routers/chat.py` — `_stream_chat`

```python
if not status.is_possible:
    await horde.cancel_text_job(job_id)
    # Verify before banning
    if config and config.retry.unavailable_recheck:
        count = await horde.model_worker_count(real_model)
        if count > 0 and _transient_count < config.retry.unavailable_max_transient_retries:
            _transient_count += 1
            # Transient — resubmit same model without banning
            _abort_reason = "transient-unavailable"
            break  # break inner poll loop, outer attempt loop retries
    horde.ban_model(real_model, duration=3600.0)
    _log_model_ban(request, alias, real_model)
    _abort_reason = "impossible"
    yield ": x-horde-abort reason=impossible\n\n"
    break
```

---

### 5. Routers — pass `verify_fn`

**Files:** `app/routers/chat.py`, `app/routers/completions.py`

```python
# In chat_completions / completions
async def _verify_unavailable(model_name: str) -> bool:
    count = await horde.model_worker_count(model_name)
    return count == 0  # True = really unavailable, should ban

status = await with_retry(
    ...
    verify_fn=_verify_unavailable if config.retry.unavailable_recheck else None,
    unavailable_max_transient_retries=config.retry.unavailable_max_transient_retries,
)
```

---

## Expected Behaviour After Fix

| Scenario | Before | After |
|---|---|---|
| Worker briefly restarts (count still > 0) | 1h ban | Retry up to N times, no ban |
| Model truly offline (count = 0) | 1h ban | 1h ban (unchanged) |
| Model removed from Horde entirely | 1h ban | 1h ban (unchanged) |
| Transient budget exhausted | — | Ban after N failed transient retries |

---

## Log Entries

Each transient retry should emit a `"transient-unavailable"` log entry (status
field) so the operator can see that a recheck occurred and the model was saved
from a false ban:

```
13:04:11  transient  koboldcpp/Cydonia-24B-v4.3  model still has workers — skipping ban, retrying
13:04:13  200        koboldcpp/Cydonia-24B-v4.3  Hello! ...
```

---

## Priority

**P1** — this is a correctness bug. The current behaviour silently degrades
quality by removing working models from the available pool for an hour, with no
way for the operator to know the ban was a false positive.

### Status

| Item | Status |
|---|---|
| Root cause identified | ✅ Done |
| `cached_model_count()` in client | ✅ Done |
| `unavailable_max_transient_retries` config field | ✅ Done |
| Transient guard in non-streaming `_on_broaden` (chat + completions) | ✅ Done |
| Streaming path guard in `_stream_chat` | ✅ Done |
| `HordeNoModelsRemainingError` when re-resolve returns same model | ✅ Done |
| Tests — ban (count=0), transient no-ban (count>0), streaming variants | ✅ Done |

### Implementation Notes

The final implementation differs slightly from the plan:

- `cached_model_count(name)` reads directly from `_model_cache` / `_enriched_cache` without going through TTL — zero API calls.
- The `verify_fn` / `with_retry` parameter approach was not used. Instead the logic lives directly in `_on_broaden` closures and in the `_stream_chat` `is_possible` block.
- A transient counter (`_transient_count`) is tracked per-request; once exhausted, the model is re-resolved (not banned) if count > 0.
- A `_new_model == _current_real_model` guard raises `HordeNoModelsRemainingError` immediately when re-resolve can't find a different model, preventing infinite loops.
- `count is None` (model not in Horde list at all) → no ban, re-resolve to find alternative.
