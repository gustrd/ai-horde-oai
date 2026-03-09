from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.horde.client import HordeClient, HordeError
from app.horde.retry import HordeTimeoutError, with_retry
from app.horde.routing import ModelRouter
from app.horde.translate import image_to_horde
from app.routers.chat import _horde_error
from app.schemas.openai import ImageData, ImageGenerationRequest, ImageGenerationResponse

router = APIRouter()


@router.post("/v1/images/generations", response_model=ImageGenerationResponse)
async def image_generations(request: Request, body: ImageGenerationRequest) -> ImageGenerationResponse:
    horde: HordeClient = request.app.state.horde
    config = request.app.state.config

    real_model = config.image_defaults.model
    horde_req = image_to_horde(body, real_model, config)

    try:
        status = await with_retry(
            submit_fn=lambda: horde.submit_image_job(horde_req),
            poll_fn=horde.poll_image_status,
            cancel_fn=horde.cancel_image_job,
            max_retries=config.retry.max_retries,
            timeout_seconds=config.retry.timeout_seconds,
            broaden_on_retry=False,  # no filter broadening for images
            backoff_base=config.retry.backoff_base,
        )
    except HordeTimeoutError as e:
        request.state.log_extras = {
            "model": real_model,
            "real_model": real_model,
            "prompt": body.prompt,
            "error": str(e),
        }
        raise HTTPException(status_code=504, detail=str(e))
    except HordeError as e:
        request.state.log_extras = {
            "model": real_model,
            "real_model": real_model,
            "prompt": body.prompt,
            "error": e.message,
        }
        raise _horde_error(e)

    request.state.log_extras = {
        "model": real_model,
        "real_model": real_model,
        "prompt": body.prompt,
    }

    images: list[ImageData] = []
    for gen in status.generations:
        if body.response_format == "b64_json":
            # When r2=False, Horde returns base64-encoded image data directly in gen.img
            images.append(ImageData(b64_json=gen.img))
        else:
            images.append(ImageData(url=gen.img))

    return ImageGenerationResponse(data=images)
