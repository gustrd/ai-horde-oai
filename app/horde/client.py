from __future__ import annotations

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
    def __init__(self, base_url: str, api_key: str, client_agent: str, timeout: float = 30.0):
        self.api_key = api_key
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
        r = self._check(await self.http.get("/v2/status/models", params={"type": type}))
        return [HordeModel(**m) for m in r.json()]

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
