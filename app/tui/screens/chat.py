"""Test chat screen."""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, TextArea

from app.tui.widgets.chat_view import ChatMessage, ChatView


class ChatScreen(Screen):
    """Interactive test chat interface."""

    TITLE = "Test Chat"
    BINDINGS = [
        ("escape", "pop_screen", "Back"),
        ("ctrl+l", "clear", "Clear"),
    ]

    DEFAULT_CSS = """
    ChatScreen #controls {
        height: 3;
        padding: 0 1;
    }
    ChatScreen #model-select {
        width: 20;
    }
    ChatScreen #system-prompt {
        height: 4;
        margin: 0 1;
    }
    ChatScreen ChatView {
        height: 1fr;
        border: round $accent;
        margin: 0 1;
    }
    ChatScreen #input-row {
        height: 3;
        padding: 0 1;
    }
    ChatScreen #message-input {
        width: 1fr;
    }
    ChatScreen #status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._history: list[ChatMessage] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="controls"):
            yield Label("Model: ")
            yield Select([], id="model-select", allow_blank=False)
        yield TextArea("You are a helpful assistant.", id="system-prompt")
        yield ChatView(id="chat-view")
        with Horizontal(id="input-row"):
            yield Input(placeholder="Type a message... (Enter to send)", id="message-input")
            yield Button("Send", id="send-btn", variant="primary")
            yield Button("Clear", id="clear-btn")
        yield Label("Ready.", id="status")
        yield Footer()

    def on_mount(self) -> None:
        cfg = self.app.config
        options = [("default", "default"), ("best", "best"), ("fast", "fast")]
        for alias in cfg.model_aliases:
            options.append((alias, alias))
        select = self.query_one("#model-select", Select)
        select.set_options(options)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self._send_message()
        elif event.button.id == "clear-btn":
            self.action_clear()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "message-input":
            self._send_message()

    def _send_message(self) -> None:
        inp = self.query_one("#message-input", Input)
        text = inp.value.strip()
        if not text:
            return
        inp.value = ""

        user_msg = ChatMessage(role="user", content=text)
        self._history.append(user_msg)
        self.query_one(ChatView).add_message(user_msg)
        self.query_one("#status", Label).update("Sending...")
        self.run_worker(self._do_request(text), exclusive=True)

    async def _do_request(self, text: str) -> None:
        import httpx

        cfg = self.app.config
        model = self.query_one("#model-select", Select).value
        system_text = self.query_one("#system-prompt", TextArea).text

        messages = []
        if system_text.strip():
            messages.append({"role": "system", "content": system_text})
        for msg in self._history:
            messages.append({"role": msg.role, "content": msg.content})

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"http://{cfg.host}:{cfg.port}/v1/chat/completions",
                    json={"model": model, "messages": messages},
                )
            elapsed = time.monotonic() - start

            if r.status_code != 200:
                self.query_one("#status", Label).update(f"Error: HTTP {r.status_code}")
                return

            data = r.json()
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)

            reply = ChatMessage(
                role="assistant",
                content=content,
                metadata={"elapsed": elapsed, "tokens": tokens},
            )
            self._history.append(reply)
            self.query_one(ChatView).add_message(reply)
            self.query_one("#status", Label).update(
                f"Done — {elapsed:.1f}s · {tokens} tokens"
            )
        except Exception as e:
            self.query_one("#status", Label).update(f"Error: {e}")

    def action_clear(self) -> None:
        self._history.clear()
        self.query_one(ChatView).clear()
        self.query_one("#status", Label).update("Chat cleared.")
