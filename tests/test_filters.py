from __future__ import annotations

import pytest

from app.horde.filters import filter_models
from app.schemas.horde import HordeModel


def make_model(name: str, ctx: int = 4096, max_len: int = 512) -> HordeModel:
    return HordeModel(name=name, max_context_length=ctx, max_length=max_len)


MODELS = [
    make_model("aphrodite/llama-3.1-8b", ctx=8192),
    make_model("aphrodite/llama-3.1-70b", ctx=4096),
    make_model("koboldcpp/mistral-nemo-12b", ctx=16384),
    make_model("koboldcpp/yi-34b", ctx=200000),
    make_model("koboldcpp/phi-3-mini", ctx=4096),
]


def test_no_filters():
    result = filter_models(MODELS)
    assert len(result) == 5


def test_whitelist():
    result = filter_models(MODELS, whitelist=["llama"])
    names = [m.name for m in result]
    assert all("llama" in n for n in names)
    assert len(result) == 2


def test_whitelist_multiple():
    result = filter_models(MODELS, whitelist=["llama", "mistral"])
    assert len(result) == 3


def test_blocklist():
    result = filter_models(MODELS, blocklist=["yi"])
    assert all("yi" not in m.name for m in result)
    assert len(result) == 4


def test_blocklist_multiple():
    result = filter_models(MODELS, blocklist=["yi", "phi"])
    assert len(result) == 3


def test_min_context():
    result = filter_models(MODELS, min_context=8192)
    assert all(m.max_context_length >= 8192 for m in result)
    assert len(result) == 3  # llama-8b, mistral-nemo, and yi-34b


def test_min_max_length():
    models = [
        make_model("a", max_len=256),
        make_model("b", max_len=512),
        make_model("c", max_len=1024),
    ]
    result = filter_models(models, min_max_length=512)
    assert len(result) == 2


def test_whitelist_then_blocklist():
    # Whitelist llama, then blocklist 70b
    result = filter_models(MODELS, whitelist=["llama"], blocklist=["70b"])
    assert len(result) == 1
    assert "8b" in result[0].name


def test_empty_result():
    result = filter_models(MODELS, whitelist=["nonexistent"])
    assert result == []
