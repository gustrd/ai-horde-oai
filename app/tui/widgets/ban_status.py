"""Ban / reputation status indicator widget."""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label


class BanStatusWidget(Widget):
    """Colour-coded indicator for the three live ban/reputation signals.

    Green  — all clear
    Yellow — suspicion > 0 or 429 cooldown active
    Red    — IP block active or suspicion >= 5
    """

    DEFAULT_CSS = """
    BanStatusWidget {
        height: 1;
        margin-bottom: 1;
    }
    BanStatusWidget Label {
        width: 100%;
        height: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("  Ban     : —", id="ban-label")

    def set_status(
        self,
        suspicion: int,
        ip_blocked_until: float,
        ip_block_reason: str,
        rate_limited_until: float,
    ) -> None:
        now = time.monotonic()
        label = self.query_one("#ban-label", Label)
        parts: list[str] = []

        # Suspicion score (threshold = 5)
        if suspicion == 0:
            parts.append("suspicion:0")
        elif suspicion < 5:
            parts.append(f"[yellow]suspicion:{suspicion}[/yellow]")
        else:
            parts.append(f"[red]suspicion:{suspicion}[/red]")

        # IP block (TimeoutIP = 1 h, UnsafeIP = 6 h)
        if now < ip_blocked_until:
            remaining = int(ip_blocked_until - now)
            reason = ip_block_reason or "blocked"
            parts.append(f"[red]IP:{reason}({remaining}s)[/red]")
        else:
            parts.append("IP:ok")

        # 429 rate-limit cooldown
        if now < rate_limited_until:
            remaining = int(rate_limited_until - now)
            parts.append(f"[yellow]429:cooldown({remaining}s)[/yellow]")
        else:
            parts.append("429:ok")

        label.update("  Ban     : " + "  ".join(parts))

    def clear(self) -> None:
        """Reset to the indeterminate state (used when no client is available)."""
        self.query_one("#ban-label", Label).update("  Ban     : —")
