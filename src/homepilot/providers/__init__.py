"""Infrastructure provider abstractions for HomePilot."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homepilot.providers.base import InfraProvider, Resource

if TYPE_CHECKING:
    from homepilot.models import HomePilotConfig

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Creates and manages provider instances for all configured hosts.

    Usage::

        registry = ProviderRegistry(config)
        registry.connect_all()  # call in background thread
        resources = registry.list_all_resources()
        registry.disconnect_all()
    """

    def __init__(self, config: HomePilotConfig) -> None:
        self._config = config
        self._providers: dict[str, InfraProvider] = {}
        self._build_providers()

    def _build_providers(self) -> None:
        """Instantiate a provider for each host entry."""
        from homepilot.models import ProxmoxHostConfig, TrueNASHostConfig

        for key, host_cfg in self._config.hosts.items():
            if isinstance(host_cfg, TrueNASHostConfig):
                from homepilot.providers.truenas import TrueNASProvider
                self._providers[key] = TrueNASProvider(key, host_cfg)
            elif isinstance(host_cfg, ProxmoxHostConfig):
                from homepilot.providers.proxmox import ProxmoxProvider
                self._providers[key] = ProxmoxProvider(key, host_cfg)
            else:
                logger.warning("Unknown host type for '%s': %s", key, host_cfg.type)

    # -- Connection lifecycle ------------------------------------------------

    def connect_all(self) -> None:
        """Connect all providers, logging errors but not raising."""
        for key, provider in self._providers.items():
            try:
                provider.connect()
                logger.info("Connected to %s (%s)", key, provider.host_display)
            except Exception as exc:
                logger.warning("Failed to connect to %s: %s", key, exc)

    def disconnect_all(self) -> None:
        """Disconnect all providers."""
        for provider in self._providers.values():
            try:
                provider.disconnect()
            except Exception:
                pass

    # -- Queries -------------------------------------------------------------

    @property
    def providers(self) -> dict[str, InfraProvider]:
        return self._providers

    def get_provider(self, host_key: str) -> InfraProvider | None:
        return self._providers.get(host_key)

    def list_all_resources(self) -> list[Resource]:
        """Collect resources from all connected providers."""
        resources: list[Resource] = []
        for provider in self._providers.values():
            try:
                resources.extend(provider.list_resources())
            except Exception as exc:
                logger.warning(
                    "Error listing resources from %s: %s", provider.name, exc
                )
        return resources

    def connected_hosts_display(self) -> str:
        """Human-readable summary of connected hosts."""
        parts: list[str] = []
        for key, provider in self._providers.items():
            status = "●" if provider.is_connected() else "○"
            parts.append(f"{status} {key} ({provider.host_display})")
        return " │ ".join(parts) if parts else "No hosts configured"
