from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, load_config
from app.horde.client import HordeClient
from app.horde.routing import ModelRouter
from app.routers import chat, completions, images, models


@asynccontextmanager
async def lifespan(app: FastAPI):
    config: Settings = app.state.config
    horde = HordeClient(
        base_url=config.horde_api_url,
        api_key=config.horde_api_key,
        client_agent=config.client_agent,
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
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat.router)
    app.include_router(completions.router)
    app.include_router(models.router)
    app.include_router(images.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def cli() -> None:
    config = load_config()
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    cli()
