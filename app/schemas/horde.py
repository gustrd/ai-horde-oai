from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HordeModel(BaseModel):
    name: str
    count: int = 0          # number of workers serving this model
    queued: int = 0         # jobs queued
    jobs: float = 0         # jobs/hr
    eta: int = 0            # seconds to process current queue
    max_length: int = 512
    max_context_length: int = 1024
    performance: Any = ""
    type: str = "text"


class HordeUser(BaseModel):
    username: str = ""
    kudos: float = 0
    trusted: bool = False
    id: int = 0


class HordeTextParams(BaseModel):
    max_length: int = 512
    max_context_length: int = 1614
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequence: list[str] | None = None
    n: int = 1
    frmtrmblln: bool = False
    frmtrmspch: bool = False
    singleline: bool = False


class HordeTextRequest(BaseModel):
    prompt: str
    params: HordeTextParams = Field(default_factory=HordeTextParams)
    models: list[str] = Field(default_factory=list)
    workers: list[str] = Field(default_factory=list)
    trusted_workers: bool = False
    worker_blacklist: bool = False
    slow_workers: bool = True
    dry_run: bool = False
    client_agent: str = "ai-horde-oai:0.1:github"

    model_config = {"extra": "allow"}


class HordeGeneration(BaseModel):
    text: str = ""
    model: str = ""
    worker_id: str = ""
    worker_name: str = ""
    kudos: float = 0
    state: str = "ok"


class HordeJobStatus(BaseModel):
    done: bool = False
    faulted: bool = False
    processing: int = 0
    waiting: int = 0
    finished: int = 0
    queue_position: int | None = None
    wait_time: int = 0
    kudos: float = 0
    generations: list[HordeGeneration] = Field(default_factory=list)
    is_possible: bool = True


class HordeImageParams(BaseModel):
    steps: int = 30
    cfg_scale: float = 7.5
    width: int = 512
    height: int = 512
    seed: str | None = None
    sampler_name: str = "k_euler"
    karras: bool = True
    hires_fix: bool = False
    clip_skip: int = 1
    n: int = 1


class HordeImageRequest(BaseModel):
    prompt: str
    params: HordeImageParams = Field(default_factory=HordeImageParams)
    models: list[str] = Field(default_factory=list)
    r2: bool = True           # use R2 CDN for image delivery
    nsfw: bool = False
    trusted_workers: bool = False
    slow_workers: bool = True
    client_agent: str = "ai-horde-oai:0.1:github"
    shared: bool = False


class HordeImageGeneration(BaseModel):
    img: str = ""             # base64 or URL
    seed: str = ""
    worker_id: str = ""
    worker_name: str = ""
    model: str = ""
    state: str = "ok"
    censored: bool = False


class HordeImageStatus(BaseModel):
    done: bool = False
    faulted: bool = False
    processing: int = 0
    waiting: int = 0
    finished: int = 0
    queue_position: int | None = None
    wait_time: int = 0
    kudos: float = 0
    generations: list[HordeImageGeneration] = Field(default_factory=list)
    is_possible: bool = True
