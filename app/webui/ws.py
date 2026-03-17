"""WebSocket connection manager for real-time push to web UI clients."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("ai-horde-oai.webui.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            try:
                self._connections.remove(ws)
            except ValueError:
                pass

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients; silently drop dead sockets."""
        text = json.dumps(message)
        async with self._lock:
            dead: list[WebSocket] = []
            for ws in self._connections:
                try:
                    await ws.send_text(text)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try:
                    self._connections.remove(ws)
                except ValueError:
                    pass

    def broadcast_sync(self, message: dict[str, Any]) -> None:
        """Thread-safe fire-and-forget broadcast callable from sync callbacks."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.broadcast(message))
        except RuntimeError:
            pass


# Singleton shared across the process
ws_manager = ConnectionManager()
