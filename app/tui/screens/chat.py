"""Test chat screen."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, TextArea

from app.tui.widgets.chat_view import ChatMessage, ChatView
from app.tui.screens.logs import RequestLogEntry


class ChatScreen(Screen):
    """Interactive test chat interface."""

    TITLE = "Test Chat"
    BINDINGS = [
        ("d", "switch_screen('dashboard')", "Dash"),
        ("s", "switch_screen('config')", "Set"),
        ("m", "switch_screen('models')", "Mod"),
        ("c", "switch_screen('chat')", "Chat"),
        ("l", "switch_screen('logs')", "Log"),
        ("h", "switch_screen('history')", "Hist"),
        ("q", "quit", "Quit"),
        ("ctrl+l", "clear", "Clr"),
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
            yield Select([], id="model-select", allow_blank=True)
        yield TextArea("You are a helpful assistant.", id="system-prompt")
        yield ChatView(id="chat-view")
        with Horizontal(id="input-row"):
            yield Input(placeholder="Type a message... (Enter to send)", id="message-input")
            yield Button("Send", id="send-btn", variant="primary")
            yield Button("Clear", id="clear-btn")
        yield Label("Ready.", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        cfg = self.app.config
        options = [("default", "default"), ("best", "best"), ("fast", "fast")]
        for alias in cfg.model_aliases:
            options.append((alias, alias))
        
        select = self.query_one("#model-select", Select)
        
        # Priority: 1. Current value, 2. app.selected_model, 3. cfg.default_model
        current_val = select.value
        
        # Check for empty selection
        is_empty = (
            current_val is None or 
            current_val == Select.BLANK or 
            (hasattr(Select, "NULL") and current_val == getattr(Select, "NULL"))
        )
        
        if is_empty:
            if self.app.selected_model:
                current_val = self.app.selected_model
            elif cfg.default_model:
                current_val = cfg.default_model
        
        # Ensure the selected model is in the options
        option_values = [opt[1] for opt in options]
        if current_val and not isinstance(current_val, (bool, type(Select.BLANK))):
            val_str = str(current_val)
            if val_str not in option_values:
                options.append((val_str, val_str))
            
        select.set_options(options)
        if current_val and not isinstance(current_val, (bool, type(Select.BLANK))):
            select.value = current_val
        elif not is_empty:
            # If we had a value but it's now invalid, try to clear it
            select.value = Select.BLANK

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
        select = self.query_one("#model-select", Select)
        model = select.value
        
        # Handle empty selection robustly
        is_empty = (
            model is None or 
            model == Select.BLANK or 
            (hasattr(Select, "NULL") and model == getattr(Select, "NULL"))
        )

        if is_empty:
            self.query_one("#status", Label).update("Error: No model selected.")
            return

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
            
            # Log the request
            entry = RequestLogEntry(
                timestamp=datetime.now(),
                method="POST",
                path="/v1/chat/completions",
                status=r.status_code,
                duration=elapsed
            )
            self.app.request_log.append(entry)
            # If we're on the logs screen, it'll pick it up on next mount,
            # but we should ideally notify it if it's active.
            try:
                logs_screen = self.app.get_screen("logs")
                from app.tui.screens.logs import LogsScreen
                if isinstance(logs_screen, LogsScreen):
                    logs_screen.add_entry(entry)
            except Exception:
                pass

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
            
            # Save to history
            self._save_history(model, tokens)
            
        except Exception as e:
            self.query_one("#status", Label).update(f"Error: {e}")

    def _save_history(self, model: str, tokens: int) -> None:
        history_dir = Path.home() / ".ai-horde-oai" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_{timestamp}.json"
        
        data = {
            "date": datetime.now().isoformat(),
            "model": model,
            "message_count": len(self._history),
            "kudos_spent": 0, # We don't have this info easily here
            "messages": [
                {"role": m.role, "content": m.content, "metadata": m.metadata}
                for m in self._history
            ]
        }
        
        (history_dir / filename).write_text(json.dumps(data, indent=2))

    def action_clear(self) -> None:
        self._history.clear()
        self.query_one(ChatView).clear()
        self.query_one("#status", Label).update("Chat cleared.")
