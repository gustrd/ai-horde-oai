# Ban Mitigation Plan for ai-horde-oai

Concrete strategies to implement in the proxy, mapped to each risk from `AVOID_BAN.md`.

Legend: ✅ Implemented · 🔲 Not yet implemented

---

## 1. Rate Limit Protection (AVOID_BAN §4)

### 1.1 Respect 429 with backoff ✅

**Implemented:** `_check()` in `client.py` parses `Retry-After` header and records
a cooldown in `_rate_limited_until`. `_wait_rate_limit()` is called before every
`submit_text_job` / `submit_image_job` — it sleeps until the cooldown expires
transparently. The 429 error still propagates to the client on the current request
but all subsequent requests wait out the cooldown before hitting Horde.

**Config fields added:**
```yaml
retry:
  rate_limit_backoff: 5.0   # seconds to freeze after a 429 (default; Retry-After overrides)
```

### 1.2 Token-bucket rate limiter (pre-emptive) ✅

**Implemented:** `_TokenBucket` class in `client.py` gates all `submit_*_job` calls.
`max_requests_per_second=1.0` by default — well under Horde's 2 req/s per-key limit.
Set to `0.0` to disable.

**Config fields added:**
```yaml
max_requests_per_second: 1.0   # 0 = unlimited
```

**Files:** `app/horde/client.py`, `app/config.py`, `app/main.py`

### 1.3 Stay under the concurrent quota ✅ (no code change needed)

`max_concurrent_requests: 3` (well under Horde's 30-job cap). Setting above ~20
risks `TooManyPrompts` (HTTP 429).

---

## 2. IP Timeout / Suspicion Prevention (AVOID_BAN §1, §5)

### 2.1 Detect and short-circuit `TimeoutIP` ✅

**Implemented:** `_check()` detects `rc=TimeoutIP`, sets `_ip_blocked_until` for 1 h,
and raises `HordeIPTimeoutError`. `check_ip_block()` is called at the start of every
submission path (non-streaming: before model resolution; streaming: at the top of
each retry attempt). While blocked, requests are rejected locally as HTTP 503 — no
Horde calls are made.

### 2.2 Detect `UnsafeIP` (VPN/proxy block) ✅

**Implemented:** Same mechanism as §2.1. `rc=UnsafeIP` sets a 6 h local cooldown
(matching Horde's Redis cache TTL) and raises `HordeUnsafeIPError` → HTTP 503.

**Files:** `app/horde/client.py`, `app/routers/chat.py`

**New exceptions:** `HordeIPTimeoutError`, `HordeUnsafeIPError` in `client.py`

### 2.3 Prompt content pre-screening 🔲

**Strategy:** Add an optional lightweight regex filter that checks prompts against
known Horde abuse patterns *before* submitting. On match, reject locally with
HTTP 400 — never forward to Horde.

**Config addition:**
```yaml
prompt_prefilter: false  # opt-in; false by default to avoid false positives
```

**Files:** new `app/horde/prefilter.py`, `app/routers/chat.py`

---

## 3. Retry Timing and Backoff (AVOID_BAN §4, §1)

### 3.1 Streaming retry delay ✅

**Implemented:** `_stream_chat()` sleeps `streaming_retry_delay` seconds before each
retry attempt (attempt > 0). Prevents burst-resubmissions after stall/fault.

**Config fields added:**
```yaml
retry:
  streaming_retry_delay: 2.0   # seconds between streaming retry attempts
```

**Files:** `app/routers/chat.py`

### 3.2 Stall timeout vs. Horde processing time 🔲

`stream_stall_timeout` defaults to 120 s. Consider raising to 180 s for high-load
queues. A short cooldown before resubmitting after stall would further reduce burst
submission rate.

---

## 4. HTTP Error Code Response Matrix

| Horde HTTP | Horde `rc` | Status | Behavior |
|---|---|---|---|
| **429** | (any) | ✅ | Cooldown recorded; future submits wait; current request → 429 |
| **403** | `TimeoutIP` | ✅ | Local 1 h short-circuit; no Horde calls while blocked |
| **403** | `UnsafeIP` | ✅ | Local 6 h short-circuit; HTTP 503 with advisory message |
| **403** | `BannedClientAgent` | 🔲 | Should log critical + halt (misconfiguration) |
| **403** | `DeletedUser` | 🔲 | Should log critical (key revoked); currently → 403 |
| **403** | `TooManyPrompts` | 🔲 | Could retry after delay; currently → 429 |
| **400** | `CorruptPrompt` | ✅ | Never retried; → HTTP 400; no suspicion escalation |
| **400** | `KudosUpfront` | ✅ | Forwarded as-is (client should reduce params) |
| **404** | (job poll) | ✅ | Treated as job failure → retry via `with_retry` |
| **503/502** | (server error) | ✅ | Forwarded as 502; retried by `with_retry` |

---

## 5. Model Unavailability Handling (AVOID_BAN §10)

### 5.1 Model ban + streaming alias re-resolve ✅

**Implemented:**
- `is_possible=False` → `horde.ban_model(real_model, duration=3600.0)` excludes the
  model from future alias resolution for 1 h.
- **Streaming path:** after banning, attempts to re-resolve the alias against the
  filtered model list. If a different model is available and retry budget remains,
  submits with the new model transparently.
- **Non-streaming path:** bans the model and returns HTTP 503. No inline re-resolve
  (the next request will naturally resolve to a different model via the ban).

**Files:** `app/routers/chat.py`, `app/horde/client.py`

---

## 6. Account Health Monitoring

### 6.1 `suspicion` field on `HordeUser` ✅

**Implemented:** `HordeUser.suspicion: int = 0` added to the schema. The existing
`get_user()` call returns the suspicion count, ready for display and threshold checks.

**Remaining:** TUI dashboard integration to display suspicion count and warn when
`suspicion >= 4` (one below the hard threshold of 5).

### 6.2 Adaptive throttling on repeated 429s 🔲

**Strategy:** In-memory rolling-hour counter for 429 responses. If count exceeds 10,
automatically halve `max_requests_per_second`. Reset counter after 1 h of clean
responses.

---

## 7. Client Agent Safety (AVOID_BAN §3)

### 7.1 Format validation at config load ✅

**Implemented:** `field_validator` in `Settings.client_agent` rejects:
- The hardcoded banned placeholder `My-Project:v0.0.1:My-Contact`
- Any value that is not exactly `<name>:<version>:<contact_url>` (3 colon-separated
  parts, none empty, using `maxsplit=2` so URLs with `://` are valid)

Raises `pydantic.ValidationError` at startup — misconfiguration is caught before
any Horde requests are made.

**Files:** `app/config.py`

---

## 8. Implementation Status

| Priority | Item | Status | Tests |
|---|---|---|---|
| **P0** | Never retry `CorruptPrompt` | ✅ Done | `test_corrupt_prompt_*` (3 tests) |
| **P0** | Detect & short-circuit `TimeoutIP` | ✅ Done | `test_chat_completions_timeout_ip_*` (2 tests) |
| **P0** | Detect `UnsafeIP` | ✅ Done | `test_chat_completions_unsafe_ip_returns_503` |
| **P1** | 429 cooldown recording | ✅ Done | `test_rate_limit_cooldown_recorded` |
| **P1** | Streaming retry delay | ✅ Done | `test_stream_chat_retry_delay_applied` |
| **P1** | Re-resolve alias on model ban (streaming) | ✅ Done | `test_stream_chat_impossible_fallback_to_new_model` |
| **P2** | Token-bucket rate limiter | ✅ Done | `test_token_bucket_*` (3 tests) |
| **P2** | `HordeUser.suspicion` field | ✅ Done | `test_horde_user_suspicion_*` (2 tests) |
| **P3** | Client agent validation | ✅ Done | `test_client_agent_*` (4 tests) |
| **P3** | Prompt pre-screening | 🔲 Not started | — |
| **P3** | Adaptive throttling (6.2) | 🔲 Not started | — |
| **P3** | TUI suspicion display (6.1) | 🔲 Not started | — |
| **P3** | `BannedClientAgent` / `DeletedUser` fatal handlers | 🔲 Not started | — |

**Total new tests added:** 28 · **Total suite:** 309 passing, 1 skipped

---

## 9. Ban Detection Feature (New)

The features above *react* to bans. This section plans **proactive ban detection** —
monitoring signals that indicate a ban is accumulating *before* it locks out the key.

### 9.1 Overview

Horde bans arrive in three distinct stages:

```
Stage 1: Suspicion accumulating   →  rc=CorruptPrompt, user.suspicion rising
Stage 2: Soft block (IP timeout)  →  rc=TimeoutIP (Fibonacci 3s → 4 days)
Stage 3: Hard block               →  rc=DeletedUser / flagged account / CIDR ban
```

Ban detection means catching Stage 1 signals before Stage 2 kicks in.

### 9.2 Signals to track

| Signal | Source | Severity | Reset |
|---|---|---|---|
| `rc=CorruptPrompt` on submit | `_check()` already raises | ⚠️ Medium | 24 h (IP), never (user) |
| `rc=TimeoutIP` received | `_check()` already raises | 🔴 High | Fibonacci escalation |
| `rc=UnsafeIP` received | `_check()` already raises | 🔴 High | 6 h cache |
| `user.suspicion > 0` | `GET /v2/find_user` | ⚠️ Medium | 24 h |
| `user.suspicion >= 4` | `GET /v2/find_user` | 🔴 High (threshold = 5) | 24 h |
| 429 rate > 5/hr | Rolling counter | ⚠️ Medium | 1 h |
| `rc=BannedClientAgent` | `_check()` | 🔴 Fatal | Manual fix |
| `rc=DeletedUser` | `_check()` | 🔴 Fatal | Account recovery |

### 9.3 `BanMonitor` class

**New file:** `app/horde/ban_monitor.py`

```python
@dataclass
class BanEvent:
    timestamp: datetime
    kind: str          # "corrupt_prompt", "timeout_ip", "unsafe_ip", "rate_limit",
                       #  "suspicion_warning", "suspicion_critical", "deleted_user",
                       #  "banned_agent"
    detail: str
    severity: str      # "warning", "critical", "fatal"

class BanMonitor:
    SUSPICION_WARN = 3
    SUSPICION_CRITICAL = 4
    RATE_LIMIT_WARN_PER_HOUR = 5

    def record(self, kind: str, detail: str) -> BanEvent
    def recent(self, window_seconds: int = 3600) -> list[BanEvent]
    def corrupt_prompt_count(self, window: int = 3600) -> int
    def rate_limit_count(self, window: int = 3600) -> int
    def has_active_ip_block(self) -> bool
    def highest_severity(self) -> str | None   # "warning" | "critical" | "fatal" | None
```

### 9.4 Integration points

**`app/horde/client.py`** — `HordeClient` receives a `BanMonitor` instance and
calls `monitor.record(...)` at these points:

| Where | Event recorded |
|---|---|
| `_check()` on `rc=CorruptPrompt` | `"corrupt_prompt"` |
| `_check()` on `rc=TimeoutIP` | `"timeout_ip"` |
| `_check()` on `rc=UnsafeIP` | `"unsafe_ip"` |
| `_check()` on `rc=BannedClientAgent` | `"banned_agent"` (fatal) |
| `_check()` on `rc=DeletedUser` | `"deleted_user"` (fatal) |
| `_check()` on HTTP 429 | `"rate_limit"` |

**`app/routers/chat.py`** — After catching `HordeCorruptPromptError`, call
`monitor.record("corrupt_prompt", ...)` before raising HTTP 400. This ensures the
event is logged even if the exception path skips the client-level recording.

**`app/main.py`** — Instantiate `BanMonitor` in the lifespan, attach to
`app.state.ban_monitor`.

### 9.5 Periodic user-suspicion polling

Add an async background task that runs every 5 minutes:

```python
async def _poll_suspicion(horde: HordeClient, monitor: BanMonitor) -> None:
    user = await horde.get_user()
    if user.suspicion >= BanMonitor.SUSPICION_CRITICAL:
        monitor.record("suspicion_critical", f"suspicion={user.suspicion}")
        logger.error("Account suspicion=%d — approaching auto-pause threshold (5)", user.suspicion)
    elif user.suspicion >= BanMonitor.SUSPICION_WARN:
        monitor.record("suspicion_warning", f"suspicion={user.suspicion}")
        logger.warning("Account suspicion=%d — avoid prompts with prohibited content", user.suspicion)
```

### 9.6 TUI integration

**`app/tui/screens/dashboard.py`** — Add a `BanStatusWidget` that:
- Shows green "No ban signals" when `monitor.highest_severity() is None`
- Shows yellow "⚠ N corrupt prompts / N rate limits (last hour)" at warning level
- Shows red "🔴 IP timeout active — N min remaining" at critical level
- Shows blinking red "FATAL: BannedClientAgent / DeletedUser" at fatal level

The widget refreshes on every TUI tick (same interval as the active-queue bar).

### 9.7 Config

```yaml
ban_monitor:
  enabled: true
  suspicion_poll_interval: 300   # seconds between GET /v2/find_user calls
  corrupt_prompt_warn_threshold: 1   # warn after this many in the window
  rate_limit_warn_threshold: 5       # warn after this many 429s per hour
```

### 9.8 Implementation priority

| Step | Item | Effort |
|---|---|---|
| 1 | `BanMonitor` dataclass + `record()` / query methods | Small |
| 2 | Wire into `HordeClient._check()` for all rc codes | Small |
| 3 | Background suspicion poll task in `main.py` | Small |
| 4 | Logger warnings from `BanMonitor` events | Small |
| 5 | TUI `BanStatusWidget` on dashboard | Medium |
| 6 | `BannedClientAgent` / `DeletedUser` fatal detection in `_check()` | Small |

---

## 10. Config Summary (all implemented fields)

```yaml
retry:
  max_retries: 2
  timeout_seconds: 300
  broaden_on_retry: true
  backoff_base: 2.0
  rate_limit_backoff: 5.0       # NEW: seconds to freeze after a 429
  streaming_retry_delay: 2.0    # NEW: seconds between streaming retry attempts

max_requests_per_second: 1.0    # NEW: token-bucket rate; 0 = unlimited
client_agent: "ai-horde-oai:0.1:github"   # validated at startup

# Planned:
# prompt_prefilter: false
# ban_monitor:
#   enabled: true
#   suspicion_poll_interval: 300
```
