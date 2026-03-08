from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.horde.client import HordeClient, HordeError
from app.horde.routing import ModelNotFoundError, ModelRouter
from app.horde.translate import image_to_horde
from app.routers.chat import _horde_error
from app.schemas.horde import HordeImageStatus
from app.schemas.openai import ImageData, ImageGenerationRequest, ImageGenerationResponse
import asyncio
import time

router = APIRouter()


@router.post("/v1/images/generations", response_model=ImageGenerationResponse)
async def image_generations(request: Request, body: ImageGenerationRequest) -> ImageGenerationResponse:
    horde: HordeClient = request.app.state.horde
    model_router: ModelRouter = request.app.state.model_router
    config = request.app.state.config

    # Resolve model — default to image model from config
    model_alias = body.model if body.model != "dall-e-3" else "default-image"
    try:
        real_model = config.image_defaults.model  # Simple: always use configured image model
        if body.model not in ("dall-e-3", "dall-e-2", "default-image"):
            # Try to resolve as alias
            pass
    except Exception:
        real_model = config.image_defaults.model

    horde_req = image_to_horde(body, real_model, config)

    try:
        job_id = await horde.submit_image_job(horde_req)
    except HordeError as e:
        raise _horde_error(e)

    # Poll until done
    deadline = time.monotonic() + config.retry.timeout_seconds
    status: HordeImageStatus | None = None
    while time.monotonic() < deadline:
        try:
            status = await horde.poll_image_status(job_id)
        except HordeError as e:
            raise _horde_error(e)

        if status.done and status.generations:
            break
        if status.faulted:
            raise HTTPException(status_code=502, detail="Image generation faulted on Horde")
        await asyncio.sleep(3)
    else:
        await horde.cancel_image_job(job_id)
        raise HTTPException(status_code=504, detail="Image generation timed out")

    images: list[ImageData] = []
    for gen in status.generations:
        if body.response_format == "b64_json":
            images.append(ImageData(b64_json=gen.img))
        else:
            images.append(ImageData(url=gen.img))

    return ImageGenerationResponse(data=images)
