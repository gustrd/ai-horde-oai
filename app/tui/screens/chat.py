"""Test chat screen."""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, TextArea

from app.tui.widgets.chat_view import ChatMessage, ChatView


class ChatScreen(Screen):
    """Interactive test chat interface."""

    TITLE = "Test Chat"
    BINDINGS = [
        ("d", "switch_screen('dashboard')", "Dash"),
        ("s", "switch_screen('config')", "Set"),
        ("m", "switch_screen('models')", "Mod"),
        ("c", "switch_screen('chat')", "Chat"),
        ("l", "switch_screen('logs')", "Log"),
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

        STALE_TIMEOUT = 30.0  # seconds without any SSE line before giving up

        start = time.monotonic()
        connect_host = "127.0.0.1" if cfg.host == "0.0.0.0" else cfg.host
        content_parts: list[str] = []
        actual_model: str = str(model)
        worker_name: str = ""
        worker_id: str = ""
        kudos_spent: float = 0.0
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client:
                async with client.stream(
                    "POST",
                    f"http://{connect_host}:{cfg.port}/v1/chat/completions",
                    json={"model": model, "messages": messages, "stream": True},
                ) as r:
                    if r.status_code != 200:
                        body = await r.aread()
                        self.query_one("#status", Label).update(
                            f"Error: HTTP {r.status_code} — {body.decode()[:120]}"
                        )
                        return

                    first_pos: int | None = None
                    first_pos_time: float = 0.0
                    aiter = r.aiter_lines().__aiter__()
                    while True:
                        try:
                            line = await asyncio.wait_for(aiter.__anext__(), timeout=STALE_TIMEOUT)
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            elapsed_s = time.monotonic() - start
                            raise TimeoutError(
                                f"No update from server for {STALE_TIMEOUT:.0f}s "
                                f"(total elapsed: {elapsed_s:.0f}s)"
                            )

                        if line.startswith(": x-horde-resolved"):
                            m = re.search(r"model=(\S+)", line)
                            if m:
                                actual_model = m.group(1)
                        elif line.startswith(": x-horde-worker"):
                            # Parse: x-horde-worker name=... id=... model=... kudos=...
                            m = re.search(r"name=(\S+)", line)
                            if m:
                                worker_name = m.group(1)
                            m = re.search(r"id=(\S+)", line)
                            if m:
                                worker_id = m.group(1)
                            m = re.search(r"kudos=([\d.]+)", line)
                            if m:
                                kudos_spent = float(m.group(1))
                        elif line.startswith(": queue_position="):
                            m = re.search(r"queue_position=(\d+).*eta=(\d+)", line)
                            if m:
                                pos = int(m.group(1))
                                horde_eta = int(m.group(2))
                                elapsed_s = time.monotonic() - start
                                if horde_eta > 0:
                                    eta_str = f"eta={horde_eta}s"
                                elif first_pos is not None and pos < first_pos:
                                    elapsed_since = time.monotonic() - first_pos_time
                                    rate = (first_pos - pos) / elapsed_since
                                    eta_est = int(pos / rate) if rate > 0 else 0
                                    eta_str = f"eta~{eta_est}s"
                                else:
                                    eta_str = "eta=?"
                                if first_pos is None:
                                    first_pos = pos
                                    first_pos_time = time.monotonic()
                                model_label = f"{actual_model} — " if actual_model != str(model) else ""
                                self.query_one("#status", Label).update(
                                    f"Queued — {model_label}pos={pos}, {eta_str}, elapsed={elapsed_s:.0f}s"
                                )
                            else:
                                self.query_one("#status", Label).update(f"Queued — {line[2:]}")
                        elif line.startswith("data: ") and line != "data: [DONE]":
                            chunk = line[6:]
                            try:
                                delta = json.loads(chunk)
                                # Capture actual model name (Horde resolves aliases like "best")
                                if delta.get("model"):
                                    actual_model = delta["model"]
                                text = (
                                    delta.get("choices", [{}])[0]
                                    .get("delta", {})
                                    .get("content") or ""
                                )
                                if text:
                                    content_parts.append(text)
                                    self.query_one("#status", Label).update("Receiving…")
                            except Exception:
                                pass

            elapsed = time.monotonic() - start
            content = "".join(content_parts)
            if content:
                reply = ChatMessage(
                    role="assistant",
                    content=content,
                    metadata={
                        "elapsed": elapsed,
                        "model": actual_model,
                        "worker_name": worker_name,
                        "worker_id": worker_id,
                        "kudos": kudos_spent,
                    },
                )
                self._history.append(reply)
                self.query_one(ChatView).add_message(reply)

                parts = [f"Done — {elapsed:.1f}s"]
                if actual_model and actual_model != str(model):
                    parts.append(f"model: {actual_model}")
                if worker_name:
                    parts.append(f"worker: {worker_name}")
                self.query_one("#status", Label).update(" · ".join(parts))
                self._save_history(actual_model, worker_name, kudos_spent)
            else:
                self.query_one("#status", Label).update("Error: empty response")

        except Exception as e:
            self.query_one("#status", Label).update(f"Error: {str(e) or type(e).__name__}")

    def _save_history(self, model: str, worker_name: str, kudos: float) -> None:
        history_dir = Path.home() / ".ai-horde-oai" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_{timestamp}.json"

        data = {
            "date": datetime.now().isoformat(),
            "model": model,
            "worker": worker_name,
            "kudos_spent": kudos,
            "message_count": len(self._history),
            "messages": [
                {"role": m.role, "content": m.content, "metadata": m.metadata}
                for m in self._history
            ],
        }

        (history_dir / filename).write_text(json.dumps(data, indent=2))

    def action_clear(self) -> None:
        self._history.clear()
        self.query_one(ChatView).clear()
        self.query_one("#status", Label).update("Chat cleared.")
