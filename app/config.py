from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


CONFIG_DIR = Path.home() / ".ai-horde-oai"
CONFIG_PATH = CONFIG_DIR / "config.yaml"


class RetrySettings(BaseModel):
    max_retries: int = 2
    timeout_seconds: int = 300
    broaden_on_retry: bool = True
    backoff_base: float = 2.0  # seconds; doubled each retry attempt
    rate_limit_backoff: float = 5.0   # seconds to freeze submissions after a 429
    streaming_retry_delay: float = 2.0  # seconds between streaming retry attempts


class ImageDefaults(BaseModel):
    model: str = "stable_diffusion_xl"
    steps: int = 30
    cfg_scale: float = 7.5
    width: int = 1024
    height: int = 1024


class Settings(BaseModel):
    horde_api_key: str = "0000000000"
    horde_api_url: str = "https://aihorde.net/api"
    host: str = "0.0.0.0"
    port: int = 8000
    client_agent: str = "ai-horde-oai:0.1:github"

    @field_validator("client_agent")
    @classmethod
    def _validate_client_agent(cls, v: str) -> str:
        _BANNED_PLACEHOLDER = "My-Project:v0.0.1:My-Contact"
        if v == _BANNED_PLACEHOLDER:
            raise ValueError(
                "client_agent is the banned Horde placeholder. "
                "Set it to '<name>:<version>:<contact_url>'."
            )
        parts = v.split(":", 2)  # maxsplit=2 so URLs with :// are kept in the third part
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"client_agent must have format '<name>:<version>:<contact_url>', got {v!r}"
            )
        return v
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    default_max_tokens: int = 2048  # fallback when client doesn't specify max_tokens
    model_cache_ttl: int = 30       # seconds to cache /v2/status/models response
    stream_stall_timeout: int = 120  # seconds without progress before aborting SSE
    max_concurrent_requests: int = 3  # max simultaneous Horde jobs; 0 = unlimited

    # Model alias mapping
    default_model: str = "fast"
    model_aliases: dict[str, str] = Field(default_factory=dict)

    # Model filters
    model_min_context: int = 0
    model_min_max_length: int = 0
    model_whitelist: list[str] = Field(default_factory=list)
    model_blocklist: list[str] = Field(default_factory=list)

    # Worker filters
    trusted_workers: bool = False
    worker_whitelist: list[str] = Field(default_factory=list)
    worker_blocklist: list[str] = Field(default_factory=list)

    global_min_request_delay: float = 2.0 # absolute minimum seconds between any two Horde API hits; 0 = unlimited

    retry: RetrySettings = Field(default_factory=RetrySettings)
    image_defaults: ImageDefaults = Field(default_factory=ImageDefaults)


def load_config(path: Path | None = None) -> Settings:
    if path is None:
        path = CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {}
    if path.exists():
        with open(path) as f:
            loaded = yaml.safe_load(f)
            if loaded:
                data = loaded

    # Env var overrides
    if key := os.getenv("HORDE_API_KEY"):
        data["horde_api_key"] = key
    if url := os.getenv("HORDE_API_URL"):
        data["horde_api_url"] = url
    if host := os.getenv("HOST"):
        data["host"] = host
    if port := os.getenv("PORT"):
        data["port"] = int(port)

    return Settings(**data)


def save_config(settings: Settings, path: Path | None = None) -> None:
    if path is None:
        path = CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(settings.model_dump(), f, default_flow_style=False)
