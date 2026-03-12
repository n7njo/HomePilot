"""Tests for configuration loading, saving, and validation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from homepilot.config import (
    config_to_dict,
    dict_to_config,
    load_config,
    save_config,
    validate_config,
    _default_config,
)
from homepilot.models import HomePilotConfig, PortMode, SourceType, TrueNASHostConfig


class TestDefaultConfig:
    def test_default_has_house_tracker(self):
        config = _default_config()
        assert "house-tracker" in config.apps

    def test_house_tracker_seed_values(self):
        config = _default_config()
        ht = config.apps["house-tracker"]
        assert ht.deploy.host_port == 30213
        assert ht.deploy.container_port == 5000
        assert ht.deploy.image_name == "house-tracker"
        assert ht.health.endpoint == "/api/health"
        assert ht.source.type == SourceType.LOCAL
        assert len(ht.volumes) == 1

    def test_server_defaults(self):
        config = _default_config()
        # server is a legacy property derived from the first TrueNAS host
        assert config.server.host == "truenas.local"
        assert config.server.user == "neil"
        assert config.server.docker_cmd == "sudo docker"
        # Also check the new hosts dict
        assert "truenas" in config.hosts
        assert isinstance(config.hosts["truenas"], TrueNASHostConfig)


class TestRoundTrip:
    def test_serialise_deserialise(self):
        """Config should survive a dict round-trip."""
        original = _default_config()
        data = config_to_dict(original)
        restored = dict_to_config(data)

        assert len(restored.apps) == len(original.apps)
        for name in original.apps:
            assert name in restored.apps
            orig_app = original.apps[name]
            rest_app = restored.apps[name]
            assert rest_app.deploy.host_port == orig_app.deploy.host_port
            assert rest_app.source.type == orig_app.source.type
            assert rest_app.health.endpoint == orig_app.health.endpoint

    def test_yaml_round_trip(self):
        """Config should survive a YAML serialisation round-trip."""
        original = _default_config()
        data = config_to_dict(original)
        yaml_str = yaml.dump(data, default_flow_style=False)
        loaded = yaml.safe_load(yaml_str)
        restored = dict_to_config(loaded)

        assert "house-tracker" in restored.apps
        assert restored.server.host == "truenas.local"
        assert "truenas" in restored.hosts


class TestValidation:
    def test_valid_config(self):
        config = _default_config()
        errors = validate_config(config)
        assert errors == []

    def test_missing_host(self):
        config = _default_config()
        # Clear the host address on the TrueNAS host config
        config.hosts["truenas"].host = ""
        errors = validate_config(config)
        assert any("host" in e for e in errors)

    def test_missing_source_path(self):
        config = _default_config()
        config.apps["house-tracker"].source.path = ""
        errors = validate_config(config)
        assert any("source.path" in e for e in errors)

    def test_missing_image_name(self):
        config = _default_config()
        config.apps["house-tracker"].deploy.image_name = ""
        errors = validate_config(config)
        assert any("image_name" in e for e in errors)

    def test_fixed_port_zero(self):
        config = _default_config()
        config.apps["house-tracker"].deploy.host_port = 0
        config.apps["house-tracker"].deploy.port_mode = PortMode.FIXED
        errors = validate_config(config)
        assert any("host_port" in e for e in errors)

    def test_dynamic_port_zero_is_ok(self):
        config = _default_config()
        config.apps["house-tracker"].deploy.host_port = 0
        config.apps["house-tracker"].deploy.port_mode = PortMode.DYNAMIC
        errors = validate_config(config)
        assert errors == []

    def test_git_source_needs_url(self):
        config = _default_config()
        config.apps["house-tracker"].source.type = SourceType.GIT
        config.apps["house-tracker"].source.git_url = ""
        errors = validate_config(config)
        assert any("git_url" in e for e in errors)


class TestSaveLoad:
    def test_save_and_load(self, tmp_path: Path):
        config = _default_config()
        config_file = tmp_path / "config.yaml"

        with patch("homepilot.config.CONFIG_FILE", config_file), \
             patch("homepilot.config.CONFIG_DIR", tmp_path):
            save_config(config)
            assert config_file.exists()

            loaded = load_config()
            assert "house-tracker" in loaded.apps
            assert loaded.server.host == "truenas.local"
