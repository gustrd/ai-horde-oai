# Code Review: ai-horde-oai

## Pending Issues

### [PENDING] Log table "checked" flag not displaying correctly
The `*` checked flag in the log table (`app/tui/screens/logs.py`) is not showing
after toggling with Space. Multiple approaches attempted — `update_cell_at` with
proper `Coordinate`, full `_rebuild_table()` after toggle, and JSONL persistence
via `save_entries()`. The data layer is correct; the visual update is the unresolved
problem. Suggestions to try: call `table.refresh()` or `table.refresh_row(row)`
after `update_cell_at`, or use a column key instead of coordinate index.

---

**Date:** 2026-03-11 (updated)
**Reviewer:** Claude Opus 4.6
**Overall Quality:** 8/10 — clean architecture, good async patterns, solid test coverage

---

## Status Legend

- FIXED — implemented and tested
- PARTIAL — partially done, work remains
- OPEN — not yet addressed

---

## Critical Issues

### 1. Image endpoint has no retry logic — FIXED
**File:** `app/routers/images.py`
Now uses `with_retry()` with `broaden_on_retry=False`, `backoff_base` from config.

### 2. SSE streaming has no per-message timeout — FIXED
**File:** `app/routers/chat.py`
Tracks `last_progress` timestamp; aborts if no queue position change within `stream_stall_timeout` seconds (configurable, default 120s).

### 3. No integration tests for endpoints — FIXED
**Files:** `tests/test_chat.py`, `tests/test_completions.py`, `tests/test_models.py`, `tests/test_images.py`
Full endpoint tests with `httpx.AsyncClient` + `respx` mocks: basic happy path, error codes (401, 429, 500→502, 504), streaming, model aliases.

### 4. b64_json response format for images — PARTIAL
**File:** `app/routers/images.py`
Works when Horde returns inline base64 (`r2=False`). Does NOT download from R2 CDN URL and encode. Current workaround: code sets `r2=True` and assumes Horde returns base64 directly when `response_format="b64_json"`.

---

## Moderate Issues

### 5. Max tokens hard-capped to 512 — FIXED
**File:** `app/config.py` → `max_max_tokens: int = 512`
Now configurable via `config.yaml`. `chat_to_horde()` and `completion_to_horde()` use `config.max_max_tokens`.

### 6. No exponential backoff in retry logic — FIXED
**File:** `app/horde/retry.py`
`backoff_base * (2 ** (attempt - 1))` before each retry. Default 2.0s → 2s, 4s, 8s. Tested in `test_retry.py`.

### 7. Model list not cached — FIXED
**File:** `app/horde/client.py`
TTL cache (`model_cache_ttl: 60` configurable). `invalidate_model_cache()` for forced refresh. Tested in `test_client.py`.

### 8. No request logging middleware — FIXED
**File:** `app/main.py`
HTTP middleware logs: `POST /v1/chat/completions → 200 (150ms)`. Uses Python `logging` module.

### 9. Streaming chunking is imprecise — PARTIAL
**File:** `app/routers/chat.py`
Changed from word-split to 4-char groups. Better but still not token-aligned. Acceptable for a polling-based proxy.

### 10. Select widget handling in chat screen is fragile — OPEN
**File:** `app/tui/screens/chat.py`
Multiple conditional checks for `Select.BLANK`, `None`, `hasattr(Select, "NULL")`. Works but could be cleaner.

---

## Minor Issues / Polish

### 11. No `/v1/completions` streaming support — FIXED
**File:** `app/routers/completions.py`
Explicitly rejects `stream=true` with HTTP 400 and clear error message (`invalid_request_error`, "streaming not supported").

### 12. CORS allows all origins — FIXED
**File:** `app/config.py` → `cors_origins: list[str] = ["*"]`
Configurable via config. `app/main.py` reads from `config.cors_origins`.

### 13. History saved even on error — OPEN
**File:** `app/tui/screens/chat.py`
`_save_history()` is only called when `content` is non-empty, but an error mid-response could still save partial data.

### 14. TUI file I/O on mount can block — PARTIAL
**Files:** `app/tui/screens/models.py`, `app/tui/screens/history.py`
Models screen uses `run_worker()` for async loading. History screen may still block on file reads.

### 15. No validation that model aliases point to real models — OPEN
**File:** `app/tui/screens/config.py`
Config editor accepts any alias target without validation.

### 16. Template detection is substring-based — OPEN
**File:** `app/horde/templates.py`
`"llama-3" in name` could match future model names incorrectly. Low risk but imprecise.

### 17. No error path tests — FIXED
**Files:** `tests/test_chat.py`, `tests/test_completions.py`, `tests/test_images.py`
Tests cover: Horde 401→401, 429→429, 500→502, timeout→504, faulted→504.

### 18. Worker filter fields inconsistent naming — OPEN
Config uses `worker_blocklist` but Horde API uses `worker_blacklist`. Works correctly, needs a code comment.

---

## Recent Fixes (this session)

### 19. Config save drops unrelated settings — FIXED
**File:** `app/tui/screens/config.py`
`action_save` now uses `model_copy(update={...})` instead of creating a new `Settings(...)`, preserving `model_aliases`, `image_defaults`, `max_max_tokens`, `model_min_max_length`, `cors_origins`, etc.

### 20. Dashboard model count always shows 0 — FIXED
**Files:** `app/tui/app.py`, `app/tui/screens/models.py`, `app/tui/screens/dashboard.py`
`ModelsScreen._load_models()` stores counts on `app.model_count`/`app.model_total`. Dashboard reads them via `on_screen_resume()`.

### 21. ModelRouter uses stale startup config — FIXED
**File:** `app/horde/routing.py`
`resolve()` now accepts `config` parameter. Chat and completions routers pass `request.app.state.config` at request time.

### 22. "best"/"fast" ignores filters / routes to non-whitelisted models — FIXED
**File:** `app/horde/routing.py`
`_pick_best()` and `_pick_fast()` previously fell back to the unfiltered model list when all models were filtered out, bypassing the whitelist. Now they raise `ModelNotFoundError` instead. Routers also use `get_enriched_models()` so context-length filters work correctly.

### 23. No actual model / worker info in chat — FIXED
**Files:** `app/routers/chat.py`, `app/tui/screens/chat.py`
SSE stream now includes `x-horde-worker` comment with `name=`, `id=`, `model=`, `kudos=`. TUI parses this and shows actual model + worker in status bar, saves to history and request log.

### 24. Logs screen not live-updating — FIXED
**File:** `app/tui/screens/logs.py`
Added `on_screen_resume()` that syncs entries from `app.request_log`. Log table now shows Model and Worker columns.

### 25. ModelTable filters not applied — FIXED
**Files:** `app/tui/widgets/model_table.py`, `app/tui/screens/models.py`
Widget owns all filtering (whitelist, blocklist, min_context, min_max_length + text search). Info label updates dynamically. Long model names wrapped at 40 chars.

### 26. Models tab doesn't set default on Enter — FIXED
**File:** `app/tui/screens/models.py`
`on_data_table_row_selected` sets `default_model` in config, calls `save_config()`, notifies, switches to Chat. Guard checks model against current filters.

### 27. Config screen doesn't re-apply filters on resume — FIXED
**File:** `app/tui/screens/models.py`
`on_screen_resume()` calls `widget.update_filters()` with current config values.

### 28. "Broaden on Retry" UI option removed
**File:** `app/tui/screens/config.py`
The toggle was removed from the config screen. The `broaden_on_retry` field remains in `RetrySettings` for programmatic use but is no longer user-configurable.

### 29. Tool call format retry on malformed `<tool_call>` responses — FIXED
**File:** `app/routers/chat.py`
Some models (e.g. Qwen3.5-27B via koboldcpp) emit a second `<tool_call>` opening tag instead of `</tool_call>`, bypassing the stop sequence and producing unparseable JSON. Both the non-streaming and streaming paths now detect this pattern (`response_text` starts with `<tool_call>` but `parse_tool_call` returns `None`), log a warning, and resubmit the job to Horde up to 3 times before giving up.

### 30. Robust Model Unavailability Retries — FIXED
**Files:** `app/horde/retry.py`, `app/routers/chat.py`
Retries triggered by `is_possible=False` now bypass the `max_retries` configuration, cycling through all available model fallbacks (with a configurable `poll_interval` delay and automatic model banning) until a request succeeds or fallback models are exhausted.

### 31. `poll_interval` config field — FIXED
**File:** `app/config.py`, `app/horde/retry.py`, `app/routers/chat.py`
Added `retry.poll_interval: float = 2.0` to `RetrySettings`. Previously the job-status polling interval and the impossible-model retry delay were hardcoded to `2.0`. Now both use this config value, making them tunable without code changes.

### 32. `exclude_model` in `ModelRouter.resolve()` — FIXED
**File:** `app/horde/routing.py`
`resolve()` gains an `exclude_model` parameter. When a model is banned as unavailable, the next `resolve()` call explicitly excludes it so the router can't accidentally pick it again (important for direct model names that also appear in the model list). Both `chat.py` and `completions.py` pass `exclude_model=_current_real_model` after a ban.

### 33. Direct model name fallback in `ModelRouter.resolve()` — FIXED
**File:** `app/horde/routing.py`
Previously unknown aliases (direct Horde model names) passed through unmodified, which meant a banned/unavailable model would be re-submitted as-is. Now, if the requested name appears in the live model list it's returned directly; if it doesn't (or has been excluded), the router falls back to `_pick_fast()` instead of failing.

### 34. Shared `HordeClient` between TUI and FastAPI — FIXED
**Files:** `app/tui/app.py`, `app/main.py`
The TUI's `HordeClient` instance is now injected into `app.state.horde` before the FastAPI lifespan starts. The lifespan detects this pre-injected instance and skips creating its own, so model bans, 429 cooldowns, and IP blocks are perfectly shared between the TUI and the HTTP server.

### 35. Raw response text in log detail — FIXED
**Files:** `app/routers/chat.py`, `app/tui/screens/logs.py`
For tool-call responses the log detail modal now shows both the parsed JSON tool call (labelled "Response (Enhanced)") and the raw AI output (labelled "Raw Response (AI output)"). The `raw_response_text` field is captured in `request.state.log_extras` and forwarded to `RequestLogEntry`.

### 36. Log clear persists to disk — FIXED
**File:** `app/tui/screens/logs.py`
`action_clear()` now calls `save_entries()` after clearing `app.request_log`, so the JSONL log file is truncated immediately rather than retaining stale entries until the next write.

---

## Suggestions for Enhancement

### B. `/v1/embeddings` stub — OPEN
Return clear "not supported" error instead of 404.

### D. Configurable max concurrent requests — FIXED
`max_concurrent_requests: int = 3` in `Settings` (0 = unlimited). An `asyncio.Semaphore` is created at startup and stored on `app.state.horde_semaphore`. All three generation routers (chat, completions, images) acquire it for the full job lifecycle (submit → poll → result). Exposed in the TUI Config screen under "Max Concurrent Jobs". Requires server restart to take effect.

### E. Model info endpoint — OPEN
`/v1/models/{model_id}` returning context length, max tokens, capabilities.

### F. Debug request/response body logging — OPEN
Behind `debug_logging: true` config flag.

### G. Tool/function calling translation — FIXED
Map OpenAI `tool_choice`/`functions` to Horde prompt formatting.
Implemented via prompt injection + output parsing. See `BETTER_TOOLS.md` for design details.
Files: `app/schemas/openai.py`, `app/horde/tool_parser.py` (new), `app/horde/templates.py`, `app/horde/translate.py`, `app/routers/chat.py`. Tests: `tests/test_tools.py` (21 tests).

---

## Test Coverage Summary

| Test File | Tests | Coverage |
|---|---|---|
| `test_chat.py` | 35 | Chat endpoint: basic, aliases, system msg, 401, 500→502, 404, timeout, faulted, streaming, worker comment, IP blocks, corrupt prompt, rate limit cooldown, streaming retry delay, impossible model fallback |
| `test_tools.py` | 39 | Tool/function calling: hermes, llama3, format detection, streaming, retry on bad format |
| `test_retry.py` | 22 | Retry: success, image, faulted, timeout, retries, backoff, corrupt prompt, IP block, check_ip_block, cancelled error |
| `test_routing.py` | 19 | Routing: best/fast/default/alias/fallback, blocklist, exclude_model, config passing |
| `test_model_table.py` | 19 | ModelTable: text filter, settings filters, wrapping, screen integration |
| `test_tui.py` | 27 | TUI: welcome, dashboard, config, models, chat, kudos, model table, history |
| `test_unified_log_tui.py` | 16 | Log viewer: table display, detail modal, checked flag, filtering |
| `test_e2e.py` | 16 | End-to-end integration |
| `test_config.py` | 14 | Config: load/save/env overrides, client_agent validation, retry fields, suspicion |
| `test_client.py` | 11 | Client: models, caching, invalidation, errors, cancel, user, rate limiting |
| `test_translate.py` | 10 | OpenAI ↔ Horde translation |
| `test_log_middleware.py` | 10 | Request logging middleware |
| `test_log_store.py` | 16 | Log store: entry fields, JSONL persistence |
| `test_filters.py` | 9 | Model/worker filtering |
| `test_api.py` | 9 | API: completions, images, models |
| `test_templates.py` | 7 | Chat templates: hermes, llama3, detection |
| `test_images.py` | 7 | Images: url/b64/default format, submit error, timeout, faulted, timestamp |
| `test_completions.py` | 6 | Completions: basic, stream rejected, alias, 429, prompt list, model field |
| `test_models.py` | 5 | Models list: fields, get found/not found, real names hidden |

**Total: 306 passing, 1 skipped**

---

## Architecture Strengths

1. **Clean separation of concerns** — client, translate, routing, filters, retry are independent modules
2. **Pydantic v2 throughout** — strong type safety on all request/response boundaries
3. **Model alias abstraction** — clients never see real Horde model names
4. **Smart routing with fallback** — best/fast selection with graceful degradation
5. **Auto-retry with backoff** — transparent failure recovery
6. **Async throughout** — httpx, FastAPI, Textual all properly async
7. **Config layering** — YAML file + env var overrides + sensible defaults
8. **TUI for setup/testing** — interactive configuration, chat, model browsing, request logs
9. **Worker attribution** — actual model and worker name visible in status/logs/history
10. **Live config updates** — router uses per-request config, ModelTable re-applies filters on resume
