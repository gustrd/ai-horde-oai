from __future__ import annotations

import asyncio
import time

import httpx

from app import constants
from app.schemas.horde import (
    HordeJobStatus,
    HordeModel,
    HordeTextRequest,
    HordeUser,
)


class HordeError(Exception):
    def __init__(self, status_code: int, message: str, rc: str = ""):
        self.status_code = status_code
        self.message = message
        self.rc = rc
        super().__init__(message)


class HordeIPTimeoutError(Exception):
    """IP is in Horde's Fibonacci timeout cooldown."""
    def __init__(self, message: str, duration_hint: float = 3600.0):
        self.duration_hint = duration_hint
        super().__init__(message)


class HordeUnsafeIPError(Exception):
    """IP flagged as VPN/proxy by Horde."""
    pass



class _GlobalDelay:
    """Ensures an absolute minimum time delay between any two API requests."""
    """Ensures an absolute minimum time delay between any two API requests."""

    def __init__(self, delay: float):
        self.delay = max(delay, 0.0)
        self._last_request_time: float = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self.delay <= 0:
            return

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.delay:
                await asyncio.sleep(self.delay - elapsed)

            # Update the timestamp AFTER the sleep finishes to correctly
            # space out staggered requests.
            self._last_request_time = time.monotonic()


class HordeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        client_agent: str,
        timeout: float = 30.0,
        model_cache_ttl: int = 60,
        rate_limit_backoff: float = 5.0,
        global_min_request_delay: float = 2.0,
    ):
        self.api_key = api_key
        self._model_cache_ttl = model_cache_ttl
        self._model_cache: list[HordeModel] = []
        self._model_cache_expires: float = 0.0
        self._enriched_cache: list[HordeModel] = []
        self._enriched_cache_expires: float = 0.0
        self._banned_models: dict[str, float] = {}  # name → expiry (monotonic)

        # IP block state (P0-B)
        self._ip_blocked_until: float = 0.0
        self._ip_block_reason: str = ""

        # Rate-limit cooldown (P1-A)
        self._rate_limited_until: float = 0.0
        self._rate_limit_backoff = rate_limit_backoff

        # Global request delayer
        self._global_delay = _GlobalDelay(delay=global_min_request_delay)
        self.http = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "apikey": api_key,
                "Client-Agent": client_agent,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Central HTTP dispatcher enforcing global API delay."""
        await self._global_delay.wait()
        return await self.http.request(method, url, **kwargs)

    def cached_model_count(self, name: str) -> int | None:
        """Return the cached worker count for *name* without applying ban filters.

        Returns:
            None   — model not found in cache (unknown to Horde, already gone)
            0      — model found but has no active workers (truly offline)
            > 0    — model found and has workers (is_possible=False was transient)
        """
        for cache in (self._enriched_cache, self._model_cache):
            for m in cache:
                if m.name == name:
                    return m.count
        return None

    @property
    def banned_models(self) -> dict[str, float]:
        """Return active model bans as {name: expiry_monotonic}. Expired bans are excluded."""
        now = time.monotonic()
        return {n: exp for n, exp in self._banned_models.items() if exp > now}

    @property
    def ip_blocked_until(self) -> float:
        return self._ip_blocked_until

    @property
    def ip_block_reason(self) -> str:
        return self._ip_block_reason

    @property
    def rate_limited_until(self) -> float:
        return self._rate_limited_until

    def check_ip_block(self) -> None:
        """Raise immediately if the IP is in a local cooldown (TimeoutIP / UnsafeIP)."""
        now = time.monotonic()
        if now < self._ip_blocked_until:
            remaining = int(self._ip_blocked_until - now)
            if self._ip_block_reason == "UnsafeIP":
                raise HordeUnsafeIPError(
                    f"IP flagged as unsafe by Horde (VPN/proxy). Cooldown: {remaining}s remaining."
                )
            raise HordeIPTimeoutError(
                f"IP is in Horde timeout. Cooldown: {remaining}s remaining.",
                duration_hint=float(remaining),
            )

    async def _wait_rate_limit(self) -> None:
        """Sleep until any active 429 cooldown expires."""
        now = time.monotonic()
        if now < self._rate_limited_until:
            await asyncio.sleep(self._rate_limited_until - now)

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = body.get("message", response.text)
                rc = body.get("rc", "")
            except Exception:
                detail, rc = response.text, ""

            if rc == "TimeoutIP":
                self._ip_blocked_until = time.monotonic() + constants.TIMEOUT_IP_COOLDOWN
                self._ip_block_reason = "TimeoutIP"
                raise HordeIPTimeoutError(detail)
            if rc == "UnsafeIP":
                self._ip_blocked_until = time.monotonic() + constants.UNSAFE_IP_COOLDOWN
                self._ip_block_reason = "UnsafeIP"
                raise HordeUnsafeIPError(detail)
            if response.status_code == 429:
                retry_after_str = response.headers.get("Retry-After")
                try:
                    retry_after: float | None = float(retry_after_str) if retry_after_str else None
                except (TypeError, ValueError):
                    retry_after = None
                self._rate_limited_until = time.monotonic() + (
                    retry_after if retry_after is not None else self._rate_limit_backoff
                )

            raise HordeError(response.status_code, detail, rc=rc)
        return response

    async def get_models(self, type: str = "text") -> list[HordeModel]:
        """Fetch available models, using a TTL cache to avoid hammering the API."""
        now = time.monotonic()
        if self._model_cache and now < self._model_cache_expires:
            return self._filter_banned(self._model_cache)
        r = self._check(await self._request("GET", "/v2/status/models", params={"type": type}))
        models = [HordeModel(**m) for m in r.json()]
        self._model_cache = models
        self._model_cache_expires = now + self._model_cache_ttl
        return self._filter_banned(models)

    def invalidate_model_cache(self) -> None:
        """Force the next get_models() / get_enriched_models() call to fetch fresh data."""
        self._model_cache_expires = 0.0
        self._enriched_cache_expires = 0.0

    def ban_model(self, name: str, duration: float = constants.MODEL_BAN_DURATION) -> None:
        """Ban a model from routing for *duration* seconds and remove it from caches."""
        self._banned_models[name] = time.monotonic() + duration
        self._model_cache = [m for m in self._model_cache if m.name != name]
        self._enriched_cache = [m for m in self._enriched_cache if m.name != name]
        self._model_cache_expires = 0.0
        self._enriched_cache_expires = 0.0

    def unban_all_models(self) -> None:
        """Clear all local model bans and invalidate caches."""
        self._banned_models.clear()
        self.invalidate_model_cache()

    def _filter_banned(self, models: list[HordeModel]) -> list[HordeModel]:
        """Remove currently-banned models, cleaning up expired bans as a side-effect."""
        now = time.monotonic()
        self._banned_models = {n: exp for n, exp in self._banned_models.items() if exp > now}
        if not self._banned_models:
            return models
        return [m for m in models if m.name not in self._banned_models]

    async def get_enriched_models(self, type: str = "text") -> list[HordeModel]:
        """Fetch models enriched with real max_context_length/max_length from online workers.

        Uses the same TTL as the model cache. Falls back to bare models if workers
        cannot be fetched.
        """
        now = time.monotonic()
        if self._enriched_cache and now < self._enriched_cache_expires:
            return self._filter_banned(self._enriched_cache)

        models, workers = await asyncio.gather(
            self.get_models(type=type),
            self.get_text_workers(),
            return_exceptions=True,
        )

        if isinstance(models, Exception):
            raise models

        if not isinstance(workers, Exception) and workers:
            ctx_map: dict[str, int] = {}
            len_map: dict[str, int] = {}
            for w in workers:
                if not w.get("online"):
                    continue
                max_ctx = w.get("max_context_length", 0)
                max_len = w.get("max_length", 0)
                for name in w.get("models", []):
                    if max_ctx > ctx_map.get(name, 0):
                        ctx_map[name] = max_ctx
                    if max_len > len_map.get(name, 0):
                        len_map[name] = max_len
            models = [
                m.model_copy(update={
                    "max_context_length": ctx_map.get(m.name, m.max_context_length),
                    "max_length": len_map.get(m.name, m.max_length),
                })
                for m in models
            ]

        self._enriched_cache = models
        self._enriched_cache_expires = now + self._model_cache_ttl
        return self._filter_banned(models)

    async def get_text_workers(self) -> list[dict]:
        r = self._check(await self._request("GET", "/v2/workers", params={"type": "text"}))
        return r.json()

    async def get_user(self) -> HordeUser:
        r = self._check(await self._request("GET", "/v2/find_user"))
        return HordeUser(**r.json())

    async def submit_text_job(self, payload: HordeTextRequest) -> str:
        self.check_ip_block()
        await self._wait_rate_limit()
        r = self._check(
            await self._request(
                "POST",
                "/v2/generate/text/async",
                content=payload.model_dump_json(exclude_none=True),
            )
        )
        return r.json()["id"]

    async def poll_text_status(self, job_id: str) -> HordeJobStatus:
        r = self._check(await self._request("GET", f"/v2/generate/text/status/{job_id}"))
        return HordeJobStatus(**r.json())

    async def cancel_text_job(self, job_id: str) -> None:
        try:
            await self._request("DELETE", f"/v2/generate/text/status/{job_id}")
        except Exception:
            pass
