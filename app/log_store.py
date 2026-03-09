"""Canonical RequestLogEntry definition shared by the FastAPI server and TUI."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class RequestLogEntry:
    timestamp: datetime
    method: str
    path: str
    status: int
    duration: float
    model: str = ""
    real_model: str = ""
    worker: str = ""
    worker_id: str = ""
    kudos: float = 0.0
    messages: list | None = None   # list[dict] for chat, None for others
    prompt: str = ""               # for /v1/completions and /v1/images
    response_text: str = ""
    error: str = ""
    source: str = "api"            # always "api"; reserved for future use
