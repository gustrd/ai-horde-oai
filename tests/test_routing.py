from __future__ import annotations

import pytest

from app.config import Settings
from app.horde.routing import ModelNotFoundError, ModelRouter
from app.schemas.horde import HordeModel


def make_model(name: str, count: int = 1, queued: int = 0, eta: int = 10) -> HordeModel:
    return HordeModel(name=name, count=count, queued=queued, eta=eta)


MODELS = [
    make_model("aphrodite/llama-3.1-8b", count=5, queued=2, eta=10),
    make_model("aphrodite/llama-3.1-70b", count=2, queued=5, eta=30),
    make_model("koboldcpp/mistral-nemo-12b", count=3, queued=1, eta=5),
]


@pytest.fixture
def config():
    return Settings(
        default_model="aphrodite/llama-3.1-8b",
        model_aliases={"large": "aphrodite/llama-3.1-70b", "creative": "koboldcpp/mistral-nemo-12b"},
    )


@pytest.fixture
def router(config):
    return ModelRouter(config)


@pytest.mark.asyncio
async def test_resolve_default(router):
    result = await router.resolve("default", MODELS)
    assert result == "aphrodite/llama-3.1-8b"


@pytest.mark.asyncio
async def test_resolve_alias(router):
    result = await router.resolve("large", MODELS)
    assert result == "aphrodite/llama-3.1-70b"


@pytest.mark.asyncio
async def test_resolve_best(router):
    # "best" = most workers = llama-3.1-8b (count=5)
    result = await router.resolve("best", MODELS)
    assert result == "aphrodite/llama-3.1-8b"


@pytest.mark.asyncio
async def test_resolve_fast(router):
    # "fast" = lowest queue+eta = mistral-nemo (queued=1, eta=5)
    result = await router.resolve("fast", MODELS)
    assert result == "koboldcpp/mistral-nemo-12b"


@pytest.mark.asyncio
async def test_resolve_unknown_passthrough(router):
    # Unknown alias passes through as-is
    result = await router.resolve("some-real-horde-model", MODELS)
    assert result == "some-real-horde-model"


def test_reverse_alias(router):
    assert router.reverse("aphrodite/llama-3.1-70b") == "large"


def test_reverse_default(router):
    assert router.reverse("aphrodite/llama-3.1-8b") == "default"


def test_reverse_unknown(router):
    assert router.reverse("unknown/model") == "unknown/model"


def test_get_dummy_list(router):
    names = router.get_dummy_list()
    assert "best" in names
    assert "fast" in names
    assert "default" in names
    assert "large" in names
    assert "creative" in names


@pytest.mark.asyncio
async def test_best_with_blocklist():
    config = Settings(
        default_model="aphrodite/llama-3.1-8b",
        model_blocklist=["llama"],
    )
    router = ModelRouter(config)
    # Best after blocking llama should be mistral (count=3) > llama-70b (count=2)
    result = await router.resolve("best", MODELS)
    assert "llama" not in result


@pytest.mark.asyncio
async def test_best_falls_back_when_all_filtered():
    """If all models are filtered out, best/fast fall back to unfiltered list."""
    config = Settings(
        default_model="aphrodite/llama-3.1-8b",
        model_whitelist=["nonexistent-model"],  # eliminates everything
    )
    router = ModelRouter(config)
    # Should NOT raise — falls back to unfiltered MODELS
    result = await router.resolve("best", MODELS)
    # Falls back to unfiltered best = llama-3.1-8b (count=5)
    assert result == "aphrodite/llama-3.1-8b"


@pytest.mark.asyncio
async def test_fast_falls_back_when_all_filtered():
    """fast falls back to unfiltered list when filters eliminate everything."""
    config = Settings(
        default_model="aphrodite/llama-3.1-8b",
        model_blocklist=["aphrodite", "koboldcpp"],  # eliminates everything
    )
    router = ModelRouter(config)
    result = await router.resolve("fast", MODELS)
    # Falls back to unfiltered — mistral-nemo has lowest (queued=1, eta=5)
    assert result == "koboldcpp/mistral-nemo-12b"


@pytest.mark.asyncio
async def test_resolve_uses_passed_config_over_stored():
    """resolve(config=...) uses given config, ignoring self.config filters."""
    stored_config = Settings(
        default_model="aphrodite/llama-3.1-8b",
        model_whitelist=["nonexistent"],  # stored config blocks everything
    )
    router = ModelRouter(stored_config)

    # Pass a permissive runtime config
    runtime_config = Settings(default_model="aphrodite/llama-3.1-8b")
    result = await router.resolve("best", MODELS, config=runtime_config)
    # Runtime config has no filter → llama-3.1-8b (count=5)
    assert result == "aphrodite/llama-3.1-8b"


@pytest.mark.asyncio
async def test_best_empty_model_list_raises():
    """ModelNotFoundError raised only when Horde returns zero models at all."""
    config = Settings(default_model="model")
    router = ModelRouter(config)
    with pytest.raises(ModelNotFoundError):
        await router.resolve("best", [])
