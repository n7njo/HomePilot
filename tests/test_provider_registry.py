"""Tests for ProviderRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from homepilot.models import (
    HomePilotConfig,
    ProxmoxHostConfig,
    TrueNASHostConfig,
)
from homepilot.providers import ProviderRegistry
from homepilot.providers.base import Resource, ResourceStatus, ResourceType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def multi_host_config():
    """Config with one TrueNAS and one Proxmox host."""
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
        apps={},
    )


@pytest.fixture
def empty_config():
    return HomePilotConfig(hosts={}, apps={})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProviderRegistryBuild:
    def test_builds_both_providers(self, multi_host_config):
        registry = ProviderRegistry(multi_host_config)
        assert "truenas" in registry.providers
        assert "proxmox" in registry.providers
        assert len(registry.providers) == 2

    def test_truenas_provider_type(self, multi_host_config):
        registry = ProviderRegistry(multi_host_config)
        p = registry.get_provider("truenas")
        assert p is not None
        assert p.provider_type == "truenas"

    def test_proxmox_provider_type(self, multi_host_config):
        registry = ProviderRegistry(multi_host_config)
        p = registry.get_provider("proxmox")
        assert p is not None
        assert p.provider_type == "proxmox"

    def test_empty_config(self, empty_config):
        registry = ProviderRegistry(empty_config)
        assert len(registry.providers) == 0

    def test_get_provider_missing(self, multi_host_config):
        registry = ProviderRegistry(multi_host_config)
        assert registry.get_provider("nonexistent") is None


class TestProviderRegistryConnect:
    def test_connect_all_logs_errors(self, multi_host_config):
        """connect_all should not raise even if providers fail."""
        registry = ProviderRegistry(multi_host_config)

        # Mock all providers to fail on connect
        for p in registry.providers.values():
            p.connect = MagicMock(side_effect=Exception("connection refused"))

        # Should not raise
        registry.connect_all()

    def test_disconnect_all(self, multi_host_config):
        registry = ProviderRegistry(multi_host_config)
        for p in registry.providers.values():
            p.disconnect = MagicMock()

        registry.disconnect_all()
        for p in registry.providers.values():
            p.disconnect.assert_called_once()


class TestProviderRegistryResources:
    def test_list_all_resources_aggregates(self, multi_host_config):
        registry = ProviderRegistry(multi_host_config)

        # Mock each provider's list_resources
        truenas_resources = [
            Resource(
                id="my-app",
                name="my-app",
                resource_type=ResourceType.DOCKER_CONTAINER,
                provider_name="truenas",
                status=ResourceStatus.RUNNING,
            ),
        ]
        proxmox_resources = [
            Resource(
                id="qemu/pve/100",
                name="ubuntu-vm",
                resource_type=ResourceType.VM,
                provider_name="proxmox",
                status=ResourceStatus.RUNNING,
            ),
        ]

        registry.providers["truenas"].list_resources = MagicMock(return_value=truenas_resources)
        registry.providers["proxmox"].list_resources = MagicMock(return_value=proxmox_resources)

        all_resources = registry.list_all_resources()
        assert len(all_resources) == 2
        names = {r.name for r in all_resources}
        assert names == {"my-app", "ubuntu-vm"}

    def test_list_all_resources_handles_error(self, multi_host_config):
        registry = ProviderRegistry(multi_host_config)

        registry.providers["truenas"].list_resources = MagicMock(
            side_effect=Exception("SSH failed")
        )
        registry.providers["proxmox"].list_resources = MagicMock(
            return_value=[
                Resource(
                    id="qemu/pve/100",
                    name="vm1",
                    resource_type=ResourceType.VM,
                    provider_name="proxmox",
                ),
            ]
        )

        # Should still return Proxmox resources despite TrueNAS failure
        resources = registry.list_all_resources()
        assert len(resources) == 1
        assert resources[0].name == "vm1"


class TestProviderRegistryDisplay:
    def test_connected_hosts_display_disconnected(self, multi_host_config):
        registry = ProviderRegistry(multi_host_config)
        display = registry.connected_hosts_display()
        # Both should show as disconnected (○)
        assert "○" in display
        assert "truenas" in display
        assert "proxmox" in display

    def test_connected_hosts_display_empty(self, empty_config):
        registry = ProviderRegistry(empty_config)
        assert registry.connected_hosts_display() == "No hosts configured"
