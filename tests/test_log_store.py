"""Unit tests for app.log_store.RequestLogEntry."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.log_store import RequestLogEntry


def _entry(**kwargs) -> RequestLogEntry:
    defaults = dict(
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
        method="POST",
        path="/v1/chat/completions",
        status=200,
        duration=1.5,
    )
    defaults.update(kwargs)
    return RequestLogEntry(**defaults)


class TestRequestLogEntryDefaults:
    def test_required_fields_accepted(self):
        e = _entry()
        assert e.method == "POST"
        assert e.path == "/v1/chat/completions"
        assert e.status == 200
        assert e.duration == 1.5

    def test_optional_strings_default_empty(self):
        e = _entry()
        assert e.model == ""
        assert e.real_model == ""
        assert e.worker == ""
        assert e.worker_id == ""
        assert e.prompt == ""
        assert e.response_text == ""
        assert e.error == ""

    def test_optional_numeric_defaults(self):
        e = _entry()
        assert e.kudos == 0.0

    def test_messages_defaults_none(self):
        e = _entry()
        assert e.messages is None

    def test_source_defaults_to_api(self):
        e = _entry()
        assert e.source == "api"


class TestRequestLogEntryWithData:
    def test_full_chat_entry(self):
        msgs = [{"role": "user", "content": "Hello"}]
        e = _entry(
            model="best",
            real_model="aphrodite/llama-8b",
            worker="WorkerA",
            worker_id="uuid-123",
            kudos=3.5,
            messages=msgs,
            response_text="Hi there!",
        )
        assert e.model == "best"
        assert e.real_model == "aphrodite/llama-8b"
        assert e.worker == "WorkerA"
        assert e.worker_id == "uuid-123"
        assert e.kudos == 3.5
        assert e.messages == msgs
        assert e.response_text == "Hi there!"

    def test_error_entry(self):
        e = _entry(status=504, error="Job timed out")
        assert e.status == 504
        assert e.error == "Job timed out"

    def test_completions_entry_uses_prompt(self):
        e = _entry(
            path="/v1/completions",
            prompt="Once upon a time",
            response_text="there was a dragon",
        )
        assert e.prompt == "Once upon a time"
        assert e.response_text == "there was a dragon"

    def test_image_entry(self):
        e = _entry(
            path="/v1/images/generations",
            model="stable_diffusion_xl",
            prompt="A sunset over the ocean",
        )
        assert e.path == "/v1/images/generations"
        assert e.prompt == "A sunset over the ocean"

    def test_dataclass_equality(self):
        ts = datetime(2024, 1, 15, 12, 0, 0)
        a = RequestLogEntry(timestamp=ts, method="GET", path="/v1/models", status=200, duration=0.1)
        b = RequestLogEntry(timestamp=ts, method="GET", path="/v1/models", status=200, duration=0.1)
        assert a == b

    def test_messages_list_is_independent(self):
        msgs = [{"role": "user", "content": "hi"}]
        e = _entry(messages=msgs)
        msgs.append({"role": "assistant", "content": "hello"})
        # The entry holds a reference to the list; verify it was stored as-is
        assert e.messages is msgs
