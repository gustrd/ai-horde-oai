from __future__ import annotations

import time

import httpx

from app.schemas.horde import (
    HordeImageRequest,
    HordeImageStatus,
    HordeJobStatus,
    HordeModel,
    HordeTextRequest,
    HordeUser,
)


class HordeError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class HordeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        client_agent: str,
        timeout: float = 30.0,
        model_cache_ttl: int = 60,
    ):
        self.api_key = api_key
        self._model_cache_ttl = model_cache_ttl
        self._model_cache: list[HordeModel] = []
        self._model_cache_expires: float = 0.0
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

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            try:
                detail = response.json().get("message", response.text)
            except Exception:
                detail = response.text
            raise HordeError(response.status_code, detail)
        return response

    async def get_models(self, type: str = "text") -> list[HordeModel]:
        """Fetch available models, using a TTL cache to avoid hammering the API."""
        now = time.monotonic()
        if self._model_cache and now < self._model_cache_expires:
            return self._model_cache
        r = self._check(await self.http.get("/v2/status/models", params={"type": type}))
        models = [HordeModel(**m) for m in r.json()]
        self._model_cache = models
        self._model_cache_expires = now + self._model_cache_ttl
        return models

    def invalidate_model_cache(self) -> None:
        """Force the next get_models() call to fetch fresh data."""
        self._model_cache_expires = 0.0

    async def get_text_workers(self) -> list[dict]:
        r = self._check(await self.http.get("/v2/workers", params={"type": "text"}))
        return r.json()

    async def get_user(self) -> HordeUser:
        r = self._check(await self.http.get("/v2/find_user"))
        return HordeUser(**r.json())

    async def submit_text_job(self, payload: HordeTextRequest) -> str:
        r = self._check(
            await self.http.post(
                "/v2/generate/text/async",
                content=payload.model_dump_json(exclude_none=True),
            )
        )
        return r.json()["id"]

    async def poll_text_status(self, job_id: str) -> HordeJobStatus:
        r = self._check(await self.http.get(f"/v2/generate/text/status/{job_id}"))
        return HordeJobStatus(**r.json())

    async def cancel_text_job(self, job_id: str) -> None:
        try:
            await self.http.delete(f"/v2/generate/text/status/{job_id}")
        except Exception:
            pass

    async def submit_image_job(self, payload: HordeImageRequest) -> str:
        r = self._check(
            await self.http.post(
                "/v2/generate/async",
                content=payload.model_dump_json(exclude_none=True),
            )
        )
        return r.json()["id"]

    async def poll_image_status(self, job_id: str) -> HordeImageStatus:
        r = self._check(await self.http.get(f"/v2/generate/status/{job_id}"))
        return HordeImageStatus(**r.json())

    async def cancel_image_job(self, job_id: str) -> None:
        try:
            await self.http.delete(f"/v2/generate/status/{job_id}")
        except Exception:
            pass
