from __future__ import annotations

from app.config import Settings
from app.horde.filters import filter_models
from app.schemas.horde import HordeModel


class ModelNotFoundError(Exception):
    pass


class ModelRouter:
    BUILTIN_ALIASES = {"best", "fast"}

    def __init__(self, config: Settings):
        self.config = config

    def _apply_filters(self, models: list[HordeModel], config: Settings) -> list[HordeModel]:
        return filter_models(
            models,
            whitelist=config.model_whitelist or None,
            blocklist=config.model_blocklist or None,
            min_context=config.model_min_context,
            min_max_length=config.model_min_max_length,
        )

    def _pick_best(self, models: list[HordeModel], config: Settings) -> str:
        """Pick model with most workers."""
        candidates = [m for m in self._apply_filters(models, config) if m.count > 0]
        if not candidates:
            raise ModelNotFoundError("No text models available from Horde after applying filters")
        return max(candidates, key=lambda m: m.count).name

    def _pick_fast(self, models: list[HordeModel], config: Settings) -> str:
        """Pick model with lowest ETA."""
        # Exclude models with no workers
        candidates = [m for m in self._apply_filters(models, config) if m.count > 0]
        if not candidates:
            raise ModelNotFoundError("No text models available from Horde after applying filters")

        return min(candidates, key=lambda m: (m.eta, m.queued)).name

    async def resolve(
        self, 
        alias: str, 
        models: list[HordeModel], 
        config: Settings | None = None,
        exclude_model: str | None = None,
    ) -> str:
        """Resolve a dummy alias to a real Horde model name.

        config: use this instead of self.config (allows per-request config).
        exclude_model: do not pick this specific model (fallback to another candidate).
        """
        cfg = config if config is not None else self.config
        
        # Filter out the model we're explicitly trying to avoid
        if exclude_model:
            models = [m for m in models if m.name != exclude_model]

        if alias == "best":
            return self._pick_best(models, cfg)
        if alias == "fast":
            return self._pick_fast(models, cfg)

        # Check user-defined aliases
        if alias in cfg.model_aliases:
            return cfg.model_aliases[alias]

        # Check "default" alias
        if alias == "default":
            if cfg.default_model:
                return cfg.default_model
            return self._pick_fast(models, cfg)

        # Direct model name check
        if any(m.name == alias for m in models):
            return alias
            
        # If the requested specific model is banned/unavailable, fallback to "fast" 
        # instead of failing, provided it's an online worker list.
        if models:
            try:
                fallback = self._pick_fast(models, cfg)
                return fallback
            except ModelNotFoundError:
                pass

        return alias

    def reverse(self, real_name: str) -> str:
        """Map a real Horde model name back to the alias clients see."""
        # Check aliases
        for alias, model in self.config.model_aliases.items():
            if model == real_name:
                return alias
        if self.config.default_model == real_name:
            return "default"
        return real_name

    def get_dummy_list(self) -> list[str]:
        """List of dummy model names to expose to clients."""
        names = list(self.BUILTIN_ALIASES) + ["default"]
        names += list(self.config.model_aliases.keys())
        return names
