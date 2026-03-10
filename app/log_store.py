"""Canonical RequestLogEntry definition shared by the FastAPI server and TUI."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / ".ai-horde-oai" / "requests.jsonl"


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return max(0, len(text) // 4)


MAX_PERSISTED_ENTRIES = 2000   # lines kept in the JSONL file
MAX_LOADED_ENTRIES = 500       # entries loaded into memory on startup


@dataclass
class RequestLogEntry:
    timestamp: datetime
    method: str
    path: str
    status: int
    duration: float
    model: str = ""
    real_model: str = ""
    worker: str = ""
    worker_id: str = ""
    kudos: float = 0.0
    messages: list | None = None   # list[dict] for chat, None for others
    prompt: str = ""               # for /v1/completions and /v1/images
    response_text: str = ""
    error: str = ""
    source: str = "api"            # always "api"; reserved for future use
    input_tokens: int = 0          # estimated input token count
    output_tokens: int = 0         # estimated output token count
    reasoning_content: str = ""    # chain-of-thought from reasoning models
    reasoning_tokens: int = 0      # estimated token count of reasoning block
    checked: bool = False          # user-toggled read/checked flag


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def entry_to_dict(entry: RequestLogEntry) -> dict:
    d = {
        "timestamp": entry.timestamp.isoformat(),
        "method": entry.method,
        "path": entry.path,
        "status": entry.status,
        "duration": entry.duration,
        "model": entry.model,
        "real_model": entry.real_model,
        "worker": entry.worker,
        "worker_id": entry.worker_id,
        "kudos": entry.kudos,
        "messages": entry.messages,
        "prompt": entry.prompt,
        "response_text": entry.response_text,
        "error": entry.error,
        "source": entry.source,
        "input_tokens": entry.input_tokens,
        "output_tokens": entry.output_tokens,
        "reasoning_content": entry.reasoning_content,
        "reasoning_tokens": entry.reasoning_tokens,
        "checked": entry.checked,
    }
    return d


def entry_from_dict(d: dict) -> RequestLogEntry:
    ts = d.get("timestamp", "")
    try:
        timestamp = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        timestamp = datetime.now()
    return RequestLogEntry(
        timestamp=timestamp,
        method=d.get("method", ""),
        path=d.get("path", ""),
        status=int(d.get("status", 0)),
        duration=float(d.get("duration", 0.0)),
        model=d.get("model", ""),
        real_model=d.get("real_model", ""),
        worker=d.get("worker", ""),
        worker_id=d.get("worker_id", ""),
        kudos=float(d.get("kudos", 0.0)),
        messages=d.get("messages"),
        prompt=d.get("prompt", ""),
        response_text=d.get("response_text", ""),
        error=d.get("error", ""),
        source=d.get("source", "api"),
        input_tokens=int(d.get("input_tokens", 0)),
        output_tokens=int(d.get("output_tokens", 0)),
        reasoning_content=d.get("reasoning_content", ""),
        reasoning_tokens=int(d.get("reasoning_tokens", 0)),
        checked=bool(d.get("checked", False)),
    )


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def append_entry(entry: RequestLogEntry, path: Path | None = None) -> None:
    """Append one entry as a JSON line. Silently ignores I/O errors."""
    if path is None:
        path = LOG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry_to_dict(entry)) + "\n")
    except Exception:
        pass


def load_entries(path: Path | None = None, max_entries: int = MAX_LOADED_ENTRIES) -> list[RequestLogEntry]:
    """Load the last *max_entries* entries from the JSONL file."""
    if path is None:
        path = LOG_PATH
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        recent = lines[-max_entries:] if len(lines) > max_entries else lines
        entries = []
        for line in recent:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(entry_from_dict(json.loads(line)))
            except Exception:
                continue
        return entries
    except Exception:
        return []


def save_entries(entries: list[RequestLogEntry], path: Path | None = None) -> None:
    """Rewrite the log file from the in-memory entry list (e.g. after toggling checked)."""
    if path is None:
        path = LOG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry_to_dict(entry)) + "\n")
    except Exception:
        pass


def trim_log_file(path: Path | None = None, max_entries: int = MAX_PERSISTED_ENTRIES) -> None:
    """Keep only the last *max_entries* lines in the file to prevent unbounded growth."""
    if path is None:
        path = LOG_PATH
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_entries:
            path.write_text("\n".join(lines[-max_entries:]) + "\n", encoding="utf-8")
    except Exception:
        pass
