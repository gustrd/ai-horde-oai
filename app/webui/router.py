"""FastAPI router for the web UI: static files, REST API, and WebSocket."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, save_config
from app.log_store import entry_to_dict, load_entries, save_entries
from app.webui.ws import ws_manager

logger = logging.getLogger("ai-horde-oai.webui")

STATIC_DIR = Path(__file__).parent / "static"

webui_router = APIRouter(prefix="/ui", tags=["webui"])


# ---------------------------------------------------------------------------
# Static files + index
# ---------------------------------------------------------------------------

def _mount_static(app) -> None:
    """Mount static file directory on the parent app (called once from lifespan)."""
    app.mount("/ui/static", StaticFiles(directory=str(STATIC_DIR)), name="webui-static")


@webui_router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------

def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


@webui_router.get("/api/config")
async def get_config(request: Request):
    config: Settings = request.app.state.config
    data = config.model_dump()
    data["horde_api_key"] = _mask_key(data["horde_api_key"])
    return data


@webui_router.put("/api/config")
async def put_config(request: Request, body: dict):
    config: Settings = request.app.state.config
    # Merge body into current config, ignoring masked key
    current = config.model_dump()
    for k, v in body.items():
        if k == "horde_api_key" and "*" in str(v):
            continue  # skip masked placeholder
        current[k] = v
    try:
        new_config = Settings(**current)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    save_config(new_config)
    request.app.state.config = new_config
    return {"ok": True}


# ---------------------------------------------------------------------------
# Dashboard endpoint
# ---------------------------------------------------------------------------

@webui_router.get("/api/dashboard")
async def get_dashboard(request: Request):
    config: Settings = request.app.state.config
    horde = getattr(request.app.state, "horde", None)
    request_log = getattr(request.app.state, "request_log", [])

    user_info: dict[str, Any] = {}
    if horde:
        try:
            user = await horde.get_user()
            user_info = {
                "username": user.username,
                "kudos": user.kudos,
                "trusted": user.trusted,
                "suspicion": user.suspicion,
            }
        except Exception:
            pass

    now = time.monotonic()
    banned: list[dict] = []
    if horde:
        for name, expiry in horde.banned_models.items():
            remaining = max(0.0, expiry - now)
            banned.append({"name": name, "remaining": round(remaining)})

    ip_blocked = False
    ip_block_reason = ""
    ip_block_remaining = 0
    rate_limited = False
    rate_limit_remaining = 0
    if horde:
        ip_blocked = now < horde.ip_blocked_until
        if ip_blocked:
            ip_block_reason = horde.ip_block_reason
            ip_block_remaining = max(0, int(horde.ip_blocked_until - now))
        rate_limited = now < horde.rate_limited_until
        if rate_limited:
            rate_limit_remaining = max(0, int(horde.rate_limited_until - now))

    model_count = getattr(request.app.state, "model_count_hint", 0)
    model_total = getattr(request.app.state, "model_total_hint", 0)

    last_request = None
    session_kudos = 0.0
    if request_log:
        last = request_log[-1]
        last_request = {
            "timestamp": last.timestamp.isoformat(),
            "model": last.model,
            "duration": last.duration,
            "status": last.status,
        }
        session_kudos = sum(e.kudos for e in request_log)

    return {
        "server": f"{config.host}:{config.port}",
        "api_key_masked": _mask_key(config.horde_api_key),
        "horde_url": config.horde_api_url,
        "user": user_info,
        "banned_models": banned,
        "ip_blocked": ip_blocked,
        "ip_block_reason": ip_block_reason,
        "ip_block_remaining": ip_block_remaining,
        "rate_limited": rate_limited,
        "rate_limit_remaining": rate_limit_remaining,
        "model_count": model_count,
        "model_total": model_total,
        "request_count": len(request_log),
        "session_kudos": round(session_kudos, 2),
        "last_request": last_request,
        "default_model": config.default_model,
    }


@webui_router.post("/api/dashboard/unban")
async def unban_all(request: Request):
    horde = getattr(request.app.state, "horde", None)
    if horde:
        horde.unban_all_models()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Models endpoints
# ---------------------------------------------------------------------------

@webui_router.get("/api/models")
async def get_models(request: Request):
    horde = getattr(request.app.state, "horde", None)
    if not horde:
        return []
    try:
        models = await horde.get_enriched_models()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    total = len(models)
    # Store hints for dashboard
    request.app.state.model_count_hint = total
    request.app.state.model_total_hint = total

    return [
        {
            "name": m.name,
            "count": m.count,
            "queued": m.queued,
            "eta": m.eta,
            "max_context_length": m.max_context_length,
            "max_length": m.max_length,
            "performance": m.performance,
        }
        for m in models
    ]


@webui_router.post("/api/models/invalidate")
async def invalidate_models(request: Request):
    horde = getattr(request.app.state, "horde", None)
    if horde:
        horde.invalidate_model_cache()
    return {"ok": True}


@webui_router.post("/api/models/set-default")
async def set_default_model(request: Request, body: dict):
    model = body.get("model", "")
    if not model:
        raise HTTPException(status_code=422, detail="model required")
    config: Settings = request.app.state.config
    new_config = config.model_copy(update={"default_model": model})
    save_config(new_config)
    request.app.state.config = new_config
    return {"ok": True, "default_model": model}


# ---------------------------------------------------------------------------
# Logs endpoints
# ---------------------------------------------------------------------------

@webui_router.get("/api/logs")
async def get_logs(request: Request):
    request_log = getattr(request.app.state, "request_log", [])
    return list(reversed([entry_to_dict(e) for e in request_log]))


@webui_router.get("/api/logs/{index}")
async def get_log_entry(request: Request, index: int):
    request_log = getattr(request.app.state, "request_log", [])
    # index 0 = newest (reversed)
    rev_index = len(request_log) - 1 - index
    if rev_index < 0 or rev_index >= len(request_log):
        raise HTTPException(status_code=404, detail="Log entry not found")
    return entry_to_dict(request_log[rev_index])


@webui_router.patch("/api/logs/{index}/check")
async def toggle_log_check(request: Request, index: int):
    request_log = getattr(request.app.state, "request_log", [])
    rev_index = len(request_log) - 1 - index
    if rev_index < 0 or rev_index >= len(request_log):
        raise HTTPException(status_code=404, detail="Log entry not found")
    entry = request_log[rev_index]
    entry.checked = not entry.checked
    save_entries(request_log)
    return {"checked": entry.checked}


@webui_router.delete("/api/logs")
async def clear_logs(request: Request):
    request_log = getattr(request.app.state, "request_log", [])
    request_log.clear()
    save_entries(request_log)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Chat proxy endpoint
# ---------------------------------------------------------------------------

@webui_router.post("/api/chat")
async def chat_proxy(request: Request, body: dict):
    config: Settings = request.app.state.config
    port = config.port
    host = "127.0.0.1" if config.host in ("0.0.0.0", "") else config.host

    # Ensure stream is False for non-streaming
    body["stream"] = False

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"http://{host}:{port}/v1/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {config.horde_api_key}"},
            )
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@webui_router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
            except Exception:
                continue
            if msg.get("type") == "cancel_job":
                job_id = msg.get("job_id", "")
                if job_id:
                    horde = getattr(ws.app.state, "horde", None)
                    if horde:
                        try:
                            await horde.cancel_text_job(job_id)
                        except Exception:
                            pass
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Startup hook: wire callbacks and initialize request_log
# ---------------------------------------------------------------------------

def setup_webui_callbacks(app) -> None:
    """Called from create_app after the router is included.

    Hooks ws_manager broadcasts into the existing TUI callbacks so both the
    TUI and web UI receive real-time updates.
    """
    # Initialize request_log if not set (standalone server, no TUI)
    if not hasattr(app.state, "request_log"):
        app.state.request_log = load_entries()

    # Chain log_callback
    original_log_cb = getattr(app.state, "log_callback", None)

    def _log_cb(entry):
        if original_log_cb:
            original_log_cb(entry)
        ws_manager.broadcast_sync({"type": "log_entry", "data": entry_to_dict(entry)})

    app.state.log_callback = _log_cb

    # Chain refresh_active_callback
    original_active_cb = getattr(app.state, "refresh_active_callback", None)

    def _active_cb():
        active = getattr(app.state, "active_requests", [])
        if original_active_cb:
            original_active_cb()
        ws_manager.broadcast_sync({"type": "active_requests", "data": active})

    app.state.refresh_active_callback = _active_cb
