from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


CONFIG_DIR = Path.home() / ".ai-horde-oai"
CONFIG_PATH = CONFIG_DIR / "config.yaml"


class RetrySettings(BaseModel):
    max_retries: int = 2
    timeout_seconds: int = 300
    broaden_on_retry: bool = True


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

    # Model alias mapping
    default_model: str = "best"
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

    retry: RetrySettings = Field(default_factory=RetrySettings)
    image_defaults: ImageDefaults = Field(default_factory=ImageDefaults)


def load_config(path: Path = CONFIG_PATH) -> Settings:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

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


def save_config(settings: Settings, path: Path = CONFIG_PATH) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(settings.model_dump(), f, default_flow_style=False)
