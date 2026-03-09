# Code Review: ai-horde-oai

**Date:** 2026-03-08
**Reviewer:** Claude Opus 4.6
**Overall Quality:** 7.5/10 — clean architecture, good async patterns, partial test coverage

---

## Critical Issues

### 1. Image endpoint has no retry logic
**File:** `app/routers/images.py`
The chat endpoint uses `with_retry()` for automatic failure recovery, but the image endpoint does a single submit+poll with no retries. Image generation on Horde is even more failure-prone than text.

**Fix:** Wrap image submission in the same `with_retry()` used by chat.

### 2. SSE streaming has no per-message timeout
**File:** `app/routers/chat.py`
The streaming generator polls Horde indefinitely. If Horde stalls mid-job, the client connection hangs forever with no timeout detection between poll cycles.

**Fix:** Add a `last_progress` timestamp and disconnect if no progress (queue position change or text) within a configurable window (e.g., 120s).

### 3. No integration tests for endpoints
**Files:** `tests/`
Unit tests exist for translate, routing, and filters, but there are no tests for the actual FastAPI endpoints (`/v1/chat/completions`, `/v1/completions`, `/v1/models`, `/v1/images/generations`). The conftest.py fixtures are set up but unused by any endpoint test.

**Fix:** Add `test_chat.py`, `test_completions.py`, `test_models.py`, `test_images.py` using `httpx.AsyncClient` + `respx` mocks against the FastAPI app.

### 4. b64_json response format not implemented for images
**File:** `app/routers/images.py`
When `response_format="b64_json"` is requested, the code likely just passes the Horde URL through instead of downloading the image and base64-encoding it.

**Fix:** Download the image from the Horde URL, base64-encode it, and return as `b64_json`.

---

## Moderate Issues

### 5. Max tokens hard-capped to 512
**File:** `app/horde/translate.py`
`chat_to_horde()` caps `max_tokens` at 512 regardless of what the client requests. Many use cases need longer outputs.

**Fix:** Make the cap configurable via `config.yaml` (e.g., `max_max_tokens: 1024`) and document the limitation.

### 6. No exponential backoff in retry logic
**File:** `app/horde/retry.py`
`with_retry()` re-submits immediately after a failed attempt. Rapid retries on Horde can burn kudos and trigger rate limiting.

**Fix:** Add exponential backoff between retry attempts (e.g., 2s, 4s, 8s).

### 7. Model list not cached
**Files:** `app/horde/routing.py`, `app/routers/models.py`
Every request that needs model resolution fetches the full model list from Horde. The model list changes infrequently.

**Fix:** Cache the model list with a TTL (e.g., 60s) in `HordeClient` or `ModelRouter`.

### 8. No request logging middleware
**File:** `app/main.py`
Request/response logging only happens in the TUI chat screen. The API server itself has no structured logging.

**Fix:** Add a FastAPI middleware that logs method, path, status code, and duration. Use Python's `logging` module.

### 9. Word-level streaming chunking is imprecise
**File:** `app/routers/chat.py`
SSE streaming splits the completed text by spaces to simulate token-level streaming. This produces unnatural chunk boundaries and doesn't match OpenAI's behavior.

**Fix:** Consider character-level streaming with small delays, or send the full text as a single chunk (more honest about Horde's non-streaming nature).

### 10. Select widget handling in chat screen is fragile
**File:** `app/tui/screens/chat.py`
Multiple conditional checks for `Select.BLANK` and `None` values suggest unclear widget state management.

**Fix:** Initialize the Select widget with a valid default value and use a wrapper method to safely get the selected model.

---

## Minor Issues / Polish

### 11. No `/v1/completions` streaming support
**File:** `app/routers/completions.py`
The legacy completions endpoint doesn't support `stream=true`, but the schema accepts it.

**Fix:** Either add streaming support (similar to chat) or explicitly reject `stream=true` with a clear error.

### 12. CORS allows all origins
**File:** `app/main.py`
`allow_origins=["*"]` is permissive. Acceptable for local proxy use, but should be configurable.

**Fix:** Add `cors_origins` to config with default `["*"]`.

### 13. History saved even on error
**File:** `app/tui/screens/chat.py`
Chat sessions are saved to history JSON even when the request fails, potentially saving incomplete/error data.

**Fix:** Only save history when at least one successful assistant response exists.

### 14. TUI file I/O on mount can block
**Files:** `app/tui/screens/history.py`, `app/tui/screens/models.py`
File and network I/O happens during screen mount, which can cause UI freezes.

**Fix:** Use Textual's `work` decorator (already used in some places) consistently for all I/O operations.

### 15. No validation that model aliases point to real models
**File:** `app/tui/screens/config.py`
The config editor accepts any model alias → target mapping without checking if the target exists on Horde.

**Fix:** Add optional validation (warning, not blocking) when saving config.

### 16. Template detection is substring-based
**File:** `app/horde/templates.py`
Model name matching (`"llama-3" in name`) could produce false positives for future model names.

**Fix:** Use more specific patterns or a configurable template mapping in config.

### 17. No error path tests
**Files:** `tests/`
No tests for Horde 401, 429, timeout, or job failure scenarios.

**Fix:** Add error path tests using `respx` to mock error responses.

### 18. Worker filter fields inconsistent naming
**Files:** `app/config.py`, `app/schemas/horde.py`
Config uses `worker_blocklist` but Horde API uses `worker_blacklist`. The translation happens silently.

**Fix:** Add a code comment documenting this intentional rename, or create a mapping constant.

---

## Suggestions for Enhancement

### A. Add OpenAI-compatible error codes
Return `error.code` field (e.g., `model_not_found`, `context_length_exceeded`) in addition to `error.type` for better client compatibility.

### B. Add `/v1/embeddings` stub
Return a clear "not supported" error instead of a 404, so clients get a helpful message.

### C. Add health check with Horde status
Extend `/health` to ping Horde and return connectivity status + current kudos balance.

### D. Add configurable max concurrent requests
Prevent overloading Horde with a semaphore limiting simultaneous jobs.

### E. Add model info endpoint
`/v1/models/{model_id}` returning context length, max tokens, and other capabilities for the resolved model.

### F. Add request/response body logging (optional)
Behind a `debug_logging: true` config flag, log full request/response bodies for troubleshooting.

### G. Support tool/function calling translation
Map OpenAI tool_choice/functions to appropriate Horde prompt formatting (e.g., JSON mode instructions).

### H. Add Prometheus metrics endpoint
`/metrics` with request counts, latencies, Horde queue times, kudos usage for monitoring.

---

## Architecture Strengths

1. **Clean separation of concerns** — client, translate, routing, filters, retry are all independent modules
2. **Pydantic v2 throughout** — strong type safety on all request/response boundaries
3. **Model alias abstraction** — clients never see real Horde model names
4. **Smart routing** — best/fast selection with configurable filters
5. **Auto-retry with broadening** — transparent failure recovery
6. **Async throughout** — httpx, FastAPI, Textual all properly async
7. **Config layering** — YAML file + env var overrides + sensible defaults
8. **TUI for setup/testing** — interactive configuration without external tools
9. **Good test fixtures** — realistic Horde API response data
10. **Extra="allow" on OpenAI schemas** — forward-compatible with new OpenAI fields
