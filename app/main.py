from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

import app.config as _app_config
from app.config import Settings, load_config
from app.horde.client import HordeClient
from app.horde.routing import ModelRouter
from app.log_store import RequestLogEntry
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
    async def reload_config_if_changed(request: Request, call_next) -> Response:
        """Reload config from disk when the YAML file has been modified (e.g. by TUI)."""
        try:
            cfg_path = _app_config.CONFIG_PATH  # resolve at call-time so tests can monkeypatch
            if cfg_path.exists():
                mtime = cfg_path.stat().st_mtime
                if mtime > getattr(app.state, "_config_mtime", 0.0):
                    app.state.config = _app_config.load_config()
                    app.state._config_mtime = mtime
        except Exception:
            pass
        return await call_next(request)

    @app.middleware("http")
    async def log_requests(request: Request, call_next) -> Response:
        start = time.monotonic()

        if request.url.path.startswith("/v1/"):
            start_callback = getattr(request.app.state, "start_callback", None)
            if start_callback is not None:
                try:
                    active_req: dict = {
                        "method": request.method,
                        "path": request.url.path,
                        "model": "",
                        "max_tokens": 0,
                        "queue_pos": None,
                        "eta": None,
                    }
                    request.state.active_req = active_req
                    start_callback(active_req)
                except Exception:
                    pass

        response = await call_next(request)
        duration = time.monotonic() - start

        logger.info(
            "%s %s → %d (%.0fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration * 1000,
        )

        # Only log /v1/ API routes; skip health checks and other system paths.
        # Streaming routes set log_extras["_streaming"] = True and log themselves.
        if request.url.path.startswith("/v1/"):
            extras: dict = getattr(request.state, "log_extras", {})
            if not extras.get("_streaming"):
                try:
                    entry = RequestLogEntry(
                        timestamp=datetime.now(),
                        method=request.method,
                        path=request.url.path,
                        status=response.status_code,
                        duration=duration,
                        model=extras.get("model", ""),
                        real_model=extras.get("real_model", ""),
                        worker=extras.get("worker", ""),
                        worker_id=extras.get("worker_id", ""),
                        kudos=extras.get("kudos", 0.0),
                        messages=extras.get("messages"),
                        prompt=extras.get("prompt", ""),
                        response_text=extras.get("response_text", ""),
                        error=extras.get("error", ""),
                        input_tokens=extras.get("input_tokens", 0),
                        output_tokens=extras.get("output_tokens", 0),
                        reasoning_content=extras.get("reasoning_content", ""),
                        reasoning_tokens=extras.get("reasoning_tokens", 0),
                    )
                    request_log = getattr(request.app.state, "request_log", None)
                    if request_log is not None:
                        request_log.append(entry)
                    log_callback = getattr(request.app.state, "log_callback", None)
                    if log_callback is not None:
                        log_callback(entry)
                except Exception:
                    pass  # never break requests due to logging failure

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
