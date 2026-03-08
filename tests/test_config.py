from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import Settings, load_config, save_config


def test_default_settings():
    s = Settings()
    assert s.horde_api_key == "0000000000"
    assert s.host == "0.0.0.0"
    assert s.port == 8000
    assert s.retry.max_retries == 2
    assert s.retry.timeout_seconds == 300


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
