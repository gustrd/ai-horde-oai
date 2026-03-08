"""Chat message display widget."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from textual.containers import ScrollableContainer
from textual.widgets import Markdown


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str
    metadata: dict = field(default_factory=dict)


class ChatView(ScrollableContainer):
    """Scrollable chat history display."""

    DEFAULT_CSS = """
    ChatView {
        padding: 1;
    }
    ChatView .user-msg {
        color: $accent;
        margin-bottom: 1;
    }
    ChatView .assistant-msg {
        color: $text;
        margin-bottom: 1;
    }
    ChatView .system-msg {
        color: $text-muted;
        margin-bottom: 1;
    }
    ChatView .meta {
        color: $text-disabled;
        text-style: italic;
    }
    """

    def clear(self) -> None:
        self.remove_children()

    def add_message(self, msg: ChatMessage) -> None:
        role_label = {"user": "**You**", "assistant": "**Assistant**", "system": "*System*"}
        header = role_label.get(msg.role, msg.role)
        text = f"{header}\n\n{msg.content}"
        widget = Markdown(text, classes=f"{msg.role}-msg")
        self.mount(widget)
        if msg.metadata:
            parts = []
            if "elapsed" in msg.metadata:
                parts.append(f"{msg.metadata['elapsed']:.1f}s")
            if "tokens" in msg.metadata:
                parts.append(f"{msg.metadata['tokens']} tokens")
            if "worker" in msg.metadata:
                parts.append(f"worker: {msg.metadata['worker']}")
            if parts:
                meta = Markdown(f"*── {' · '.join(parts)} ──*", classes="meta")
                self.mount(meta)
        self.scroll_end(animate=False)
