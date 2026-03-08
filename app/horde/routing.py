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

    def _apply_filters(self, models: list[HordeModel]) -> list[HordeModel]:
        return filter_models(
            models,
            whitelist=self.config.model_whitelist or None,
            blocklist=self.config.model_blocklist or None,
            min_context=self.config.model_min_context,
            min_max_length=self.config.model_min_max_length,
        )

    def _pick_best(self, models: list[HordeModel]) -> str:
        """Pick model with most workers (highest availability)."""
        filtered = self._apply_filters(models)
        if not filtered:
            raise ModelNotFoundError("No models available after filtering for 'best'")
        return max(filtered, key=lambda m: m.count).name

    def _pick_fast(self, models: list[HordeModel]) -> str:
        """Pick model with lowest ETA / queue."""
        filtered = self._apply_filters(models)
        if not filtered:
            raise ModelNotFoundError("No models available after filtering for 'fast'")
        return min(filtered, key=lambda m: (m.queued, m.eta)).name

    async def resolve(self, alias: str, models: list[HordeModel]) -> str:
        """Resolve a dummy alias to a real Horde model name."""
        if alias == "best":
            return self._pick_best(models)
        if alias == "fast":
            return self._pick_fast(models)

        # Check user-defined aliases
        if alias in self.config.model_aliases:
            return self.config.model_aliases[alias]

        # Check "default" alias
        if alias == "default":
            if self.config.default_model:
                return self.config.default_model
            # Fall back to "best" if no default configured
            return self._pick_best(models)

        # Unknown alias — pass through as-is (may be a direct Horde model name)
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
