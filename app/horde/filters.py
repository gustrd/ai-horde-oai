from __future__ import annotations

from app.schemas.horde import HordeModel


def filter_models(
    models: list[HordeModel],
    whitelist: list[str] | None = None,
    blocklist: list[str] | None = None,
    min_context: int = 0,
    min_max_length: int = 0,
) -> list[HordeModel]:
    """Filter Horde models based on config criteria.

    Evaluation order: whitelist → blocklist → min_context → min_max_length
    """
    result = models

    # 1. Whitelist: if non-empty, keep only models matching any substring
    if whitelist:
        result = [
            m for m in result
            if any(w.lower() in m.name.lower() for w in whitelist)
        ]

    # 2. Blocklist: remove models matching any substring
    if blocklist:
        result = [
            m for m in result
            if not any(b.lower() in m.name.lower() for b in blocklist)
        ]

    # 3. Min context length
    if min_context > 0:
        result = [m for m in result if m.max_context_length >= min_context]

    # 4. Min max output length
    if min_max_length > 0:
        result = [m for m in result if m.max_length >= min_max_length]

    return result
