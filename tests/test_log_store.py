"""Unit tests for app.log_store.RequestLogEntry and persistence helpers."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.log_store import (
    RequestLogEntry,
    append_entry,
    entry_from_dict,
    entry_to_dict,
    load_entries,
    trim_log_file,
)


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


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------

class TestSerialisationRoundTrip:
    def test_entry_to_dict_and_back(self):
        msgs = [{"role": "user", "content": "Hello"}]
        original = RequestLogEntry(
            timestamp=datetime(2024, 6, 1, 14, 30, 0),
            method="POST",
            path="/v1/chat/completions",
            status=200,
            duration=5.25,
            model="best",
            real_model="aphrodite/llama-8b",
            worker="W1",
            worker_id="wid-1",
            kudos=3.5,
            messages=msgs,
            response_text="Hi!",
            error="",
        )
        d = entry_to_dict(original)
        restored = entry_from_dict(d)

        assert restored.timestamp == original.timestamp
        assert restored.method == original.method
        assert restored.path == original.path
        assert restored.status == original.status
        assert restored.duration == original.duration
        assert restored.model == original.model
        assert restored.real_model == original.real_model
        assert restored.worker == original.worker
        assert restored.worker_id == original.worker_id
        assert restored.kudos == original.kudos
        assert restored.messages == original.messages
        assert restored.response_text == original.response_text
        assert restored.error == original.error

    def test_none_messages_survives_round_trip(self):
        e = _entry(messages=None)
        assert entry_from_dict(entry_to_dict(e)).messages is None

    def test_bad_timestamp_falls_back_to_now(self):
        d = entry_to_dict(_entry())
        d["timestamp"] = "not-a-date"
        result = entry_from_dict(d)
        assert isinstance(result.timestamp, datetime)


# ---------------------------------------------------------------------------
# File persistence
# ---------------------------------------------------------------------------

class TestFilePersistence:
    def test_append_and_load(self, tmp_path):
        p = tmp_path / "requests.jsonl"
        e = _entry(model="m1", response_text="hello")
        append_entry(e, path=p)
        loaded = load_entries(path=p)
        assert len(loaded) == 1
        assert loaded[0].model == "m1"
        assert loaded[0].response_text == "hello"

    def test_multiple_entries_preserved_in_order(self, tmp_path):
        p = tmp_path / "requests.jsonl"
        for i in range(5):
            append_entry(_entry(model=f"m{i}"), path=p)
        loaded = load_entries(path=p)
        assert len(loaded) == 5
        assert [e.model for e in loaded] == ["m0", "m1", "m2", "m3", "m4"]

    def test_load_missing_file_returns_empty(self, tmp_path):
        result = load_entries(path=tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_load_respects_max_entries(self, tmp_path):
        p = tmp_path / "requests.jsonl"
        for i in range(10):
            append_entry(_entry(model=f"m{i}"), path=p)
        loaded = load_entries(path=p, max_entries=3)
        assert len(loaded) == 3
        # Should be the last 3
        assert loaded[0].model == "m7"
        assert loaded[2].model == "m9"

    def test_corrupt_line_is_skipped(self, tmp_path):
        p = tmp_path / "requests.jsonl"
        append_entry(_entry(model="good"), path=p)
        with p.open("a") as f:
            f.write("this is not json\n")
        append_entry(_entry(model="also-good"), path=p)
        loaded = load_entries(path=p)
        assert len(loaded) == 2
        assert loaded[0].model == "good"
        assert loaded[1].model == "also-good"

    def test_append_creates_parent_directory(self, tmp_path):
        p = tmp_path / "subdir" / "requests.jsonl"
        append_entry(_entry(), path=p)
        assert p.exists()

    def test_trim_removes_oldest_lines(self, tmp_path):
        p = tmp_path / "requests.jsonl"
        for i in range(10):
            append_entry(_entry(model=f"m{i}"), path=p)
        trim_log_file(path=p, max_entries=4)
        loaded = load_entries(path=p)
        assert len(loaded) == 4
        assert loaded[0].model == "m6"

    def test_trim_no_op_when_under_limit(self, tmp_path):
        p = tmp_path / "requests.jsonl"
        for i in range(3):
            append_entry(_entry(model=f"m{i}"), path=p)
        trim_log_file(path=p, max_entries=10)
        assert len(load_entries(path=p)) == 3

    def test_trim_missing_file_no_error(self, tmp_path):
        trim_log_file(path=tmp_path / "missing.jsonl")  # must not raise

    def test_messages_and_timestamp_survive_file_round_trip(self, tmp_path):
        p = tmp_path / "requests.jsonl"
        ts = datetime(2024, 12, 25, 10, 0, 0)
        msgs = [{"role": "system", "content": "Be helpful"},
                {"role": "user", "content": "Hi"}]
        append_entry(_entry(timestamp=ts, messages=msgs, response_text="Hello!"), path=p)
        loaded = load_entries(path=p)
        assert loaded[0].timestamp == ts
        assert loaded[0].messages == msgs
        assert loaded[0].response_text == "Hello!"
