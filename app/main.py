from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, load_config
from app.horde.client import HordeClient
from app.horde.routing import ModelRouter
from app.routers import chat, completions, images, models

logger = logging.getLogger("ai-horde-oai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config: Settings = app.state.config
    horde = HordeClient(
        base_url=config.horde_api_url,
        api_key=config.horde_api_key,
        client_agent=config.client_agent,
        model_cache_ttl=config.model_cache_ttl,
    )
    app.state.horde = horde
    app.state.model_router = ModelRouter(config)
    yield
    await horde.close()


def create_app(config: Settings | None = None) -> FastAPI:
    if config is None:
        config = load_config()

    app = FastAPI(
        title="ai-horde-oai",
        description="OpenAI-compatible proxy for AI Horde",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.config = config

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            "%s %s → %d (%.0fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    app.include_router(chat.router)
    app.include_router(completions.router)
    app.include_router(models.router)
    app.include_router(images.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    config = load_config()
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    cli()
