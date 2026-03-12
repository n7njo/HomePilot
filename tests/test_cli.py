"""Tests for the HomePilot CLI commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from homepilot.__main__ import cli
from homepilot.models import (
    AppConfig,
    DeployConfig,
    HomePilotConfig,
    ProxmoxHostConfig,
    SourceConfig,
    TrueNASHostConfig,
)
from homepilot.providers.base import Resource, ResourceStatus, ResourceType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def sample_config():
    return HomePilotConfig(
        hosts={
            "truenas": TrueNASHostConfig(host="truenas.lan", user="neil"),
            "proxmox": ProxmoxHostConfig(
                host="192.168.0.199",
                token_id="test@pve!tok",
                token_secret="secret",
                token_source="inline",
            ),
        },
        apps={
            "my-app": AppConfig(
                name="my-app",
                host="truenas",
                source=SourceConfig(path="/tmp/my-app"),
                deploy=DeployConfig(
                    image_name="my-app",
                    container_name="my-app-container",
                    host_port=30213,
                    container_port=5000,
                ),
            ),
        },
    )


SAMPLE_RESOURCES = [
    Resource(
        id="my-app",
        name="my-app",
        resource_type=ResourceType.DOCKER_CONTAINER,
        provider_name="truenas",
        status=ResourceStatus.RUNNING,
        host="truenas.lan",
        port=30213,
        image="my-app:latest",
    ),
    Resource(
        id="qemu/pve/100",
        name="ubuntu-vm",
        resource_type=ResourceType.VM,
        provider_name="proxmox",
        status=ResourceStatus.RUNNING,
        host="192.168.0.199",
        port=100,
        uptime="1d 2h",
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "HomePilot" in result.output


class TestStatusCommand:
    @patch("homepilot.__main__._build_registry")
    def test_status_shows_resources(self, mock_build, runner, sample_config):
        mock_registry = MagicMock()
        mock_registry.list_all_resources.return_value = SAMPLE_RESOURCES
        mock_build.return_value = (sample_config, mock_registry)

        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "my-app" in result.output
        assert "ubuntu-vm" in result.output
        assert "Resource Status" in result.output
        mock_registry.connect_all.assert_called_once()
        mock_registry.disconnect_all.assert_called_once()

    @patch("homepilot.__main__._build_registry")
    def test_status_filter_by_host(self, mock_build, runner, sample_config):
        mock_registry = MagicMock()
        mock_registry.list_all_resources.return_value = SAMPLE_RESOURCES
        mock_build.return_value = (sample_config, mock_registry)

        result = runner.invoke(cli, ["status", "--host", "proxmox"])
        assert result.exit_code == 0
        assert "ubuntu-vm" in result.output
        # my-app is from truenas, should be filtered out
        assert "my-app" not in result.output

    @patch("homepilot.__main__._build_registry")
    def test_status_no_resources(self, mock_build, runner, sample_config):
        mock_registry = MagicMock()
        mock_registry.list_all_resources.return_value = []
        mock_build.return_value = (sample_config, mock_registry)

        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "No resources found" in result.output


class TestHostsCommand:
    @patch("homepilot.__main__._build_registry")
    def test_hosts_lists_providers(self, mock_build, runner, sample_config):
        mock_truenas = MagicMock()
        mock_truenas.provider_type = "truenas"
        mock_truenas.host_display = "neil@truenas.lan"
        mock_truenas.is_connected.return_value = True

        mock_proxmox = MagicMock()
        mock_proxmox.provider_type = "proxmox"
        mock_proxmox.host_display = "192.168.0.199"
        mock_proxmox.is_connected.return_value = True

        mock_registry = MagicMock()
        mock_registry.providers = {"truenas": mock_truenas, "proxmox": mock_proxmox}
        mock_build.return_value = (sample_config, mock_registry)

        result = runner.invoke(cli, ["hosts"])
        assert result.exit_code == 0
        assert "truenas" in result.output
        assert "proxmox" in result.output
        assert "Connected" in result.output

    @patch("homepilot.__main__._build_registry")
    def test_hosts_connection_failure(self, mock_build, runner, sample_config):
        mock_provider = MagicMock()
        mock_provider.provider_type = "proxmox"
        mock_provider.host_display = "192.168.0.199"
        mock_provider.connect.side_effect = Exception("refused")

        mock_registry = MagicMock()
        mock_registry.providers = {"proxmox": mock_provider}
        mock_build.return_value = (sample_config, mock_registry)

        result = runner.invoke(cli, ["hosts"])
        assert result.exit_code == 0
        assert "refused" in result.output


class TestConfigCommand:
    @patch("homepilot.config.load_config")
    @patch("homepilot.config.validate_config")
    def test_config_shows_hosts_and_apps(self, mock_validate, mock_load, runner, sample_config):
        mock_load.return_value = sample_config
        mock_validate.return_value = []

        result = runner.invoke(cli, ["config"])
        assert result.exit_code == 0
        assert "Hosts:" in result.output
        assert "truenas" in result.output
        assert "proxmox" in result.output
        assert "my-app" in result.output


class TestDeployCommand:
    @patch("homepilot.config.load_config")
    @patch("homepilot.config.validate_config")
    def test_deploy_unknown_app(self, mock_validate, mock_load, runner, sample_config):
        mock_load.return_value = sample_config
        mock_validate.return_value = []

        result = runner.invoke(cli, ["deploy", "nonexistent"])
        assert result.exit_code != 0
        assert "Unknown app" in result.output
