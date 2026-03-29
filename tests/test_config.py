from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import pytest
from pydantic import ValidationError

from app.config import RetrySettings, Settings, load_config, save_config


def test_default_settings():
    s = Settings()
    assert s.horde_api_key == "0000000000"
    assert s.host == "127.0.0.1"
    assert s.port == 8002
    assert s.retry.max_retries == 2
    assert s.retry.timeout_seconds == 300
    assert s.retry.rate_limit_backoff == 5.0
    assert s.retry.streaming_retry_delay == 2.0
    assert s.retry.poll_interval == 2.0
    assert s.global_min_request_delay == 2.0


# ── Client agent validation (P3) ─────────────────────────────────────────────

def test_client_agent_default_is_valid():
    """Default client_agent passes validation."""
    s = Settings()
    assert s.client_agent == "ai-horde-oai:0.1:github"


def test_client_agent_valid_custom():
    """Well-formed client_agent is accepted."""
    s = Settings(client_agent="my-app:1.2.3:https://example.com")
    assert s.client_agent == "my-app:1.2.3:https://example.com"


def test_client_agent_banned_placeholder_rejected():
    """The hardcoded Horde banned placeholder raises ValidationError."""
    with pytest.raises(ValidationError, match="banned Horde placeholder"):
        Settings(client_agent="My-Project:v0.0.1:My-Contact")


def test_client_agent_bad_format_rejected():
    """client_agent without exactly 3 colon-separated parts raises ValidationError."""
    with pytest.raises(ValidationError):
        Settings(client_agent="no-colons")


def test_client_agent_empty_part_rejected():
    """client_agent with an empty part raises ValidationError."""
    with pytest.raises(ValidationError):
        Settings(client_agent="name::contact")


# ── RetrySettings new fields ──────────────────────────────────────────────────

def test_retry_settings_new_fields():
    """New RetrySettings fields have correct defaults."""
    r = RetrySettings()
    assert r.rate_limit_backoff == 5.0
    assert r.streaming_retry_delay == 2.0
    assert r.poll_interval == 2.0


def test_retry_settings_custom_values():
    """New RetrySettings fields can be overridden."""
    r = RetrySettings(rate_limit_backoff=10.0, streaming_retry_delay=0.5)
    assert r.rate_limit_backoff == 10.0
    assert r.streaming_retry_delay == 0.5


# ── HordeUser suspicion field (P2-B) ─────────────────────────────────────────

def test_horde_user_suspicion_default():
    """HordeUser has suspicion=0 by default."""
    from app.schemas.horde import HordeUser
    u = HordeUser(username="test#1", kudos=100.0)
    assert u.suspicion == 0


def test_horde_user_suspicion_parses():
    """HordeUser parses suspicion from API response."""
    from app.schemas.horde import HordeUser
    u = HordeUser(username="test#1", kudos=100.0, suspicion=4)
    assert u.suspicion == 4


def test_load_config_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({
        "horde_api_key": "my-secret-key",
        "port": 9000,
        "model_blocklist": ["yi", "phi"],
    }))
    s = load_config(config_file)
    assert s.horde_api_key == "my-secret-key"
    assert s.port == 9000
    assert "yi" in s.model_blocklist


def test_load_config_missing_file(tmp_path):
    # Should return defaults if file doesn't exist
    s = load_config(tmp_path / "nonexistent.yaml")
    assert s.horde_api_key == "0000000000"


def test_env_var_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HORDE_API_KEY", "env-key")
    monkeypatch.setenv("PORT", "7777")
    s = load_config(tmp_path / "nonexistent.yaml")
    assert s.horde_api_key == "env-key"
    assert s.port == 7777


def test_save_and_reload(tmp_path):
    config_file = tmp_path / "config.yaml"
    s = Settings(horde_api_key="saved-key", port=8080)
    save_config(s, config_file)
    assert config_file.exists()
    s2 = load_config(config_file)
    assert s2.horde_api_key == "saved-key"
    assert s2.port == 8080
