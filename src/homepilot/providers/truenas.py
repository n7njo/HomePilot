"""TrueNAS provider — wraps SSHService + TrueNASService behind InfraProvider."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homepilot.providers.base import (
    HealthStatus,
    InfraProvider,
    Resource,
    ResourceStatus,
    ResourceType,
)
from homepilot.services.ssh import SSHService
from homepilot.services.truenas import TrueNASService

if TYPE_CHECKING:
    from homepilot.models import TrueNASHostConfig

logger = logging.getLogger(__name__)


class TrueNASProvider:
    """InfraProvider implementation for TrueNAS Docker hosts.

    Wraps the existing ``SSHService`` and ``TrueNASService`` so that
    the rest of HomePilot can interact with TrueNAS through the
    unified provider interface.
    """

    def __init__(self, host_key: str, config: TrueNASHostConfig) -> None:
        self._host_key = host_key
        self._config = config
        self._ssh: SSHService | None = None
        self._truenas: TrueNASService | None = None

    # -- Protocol properties -------------------------------------------------

    @property
    def name(self) -> str:
        return self._host_key

    @property
    def host_display(self) -> str:
        return f"{self._config.user}@{self._config.host}"

    @property
    def provider_type(self) -> str:
        return "truenas"

    # -- Connection lifecycle ------------------------------------------------

    def connect(self) -> None:
        from homepilot.models import ServerConfig

        # Build a legacy ServerConfig for the existing services.
        server = ServerConfig(
            host=self._config.host,
            user=self._config.user,
            ssh_key=self._config.ssh_key,
            docker_cmd=self._config.docker_cmd,
            midclt_cmd=self._config.midclt_cmd,
            data_root=self._config.data_root,
            backup_dir=self._config.backup_dir,
            dynamic_port_range_start=self._config.dynamic_port_range_start,
            dynamic_port_range_end=self._config.dynamic_port_range_end,
        )
        self._ssh = SSHService(server)
        self._ssh.connect()
        self._truenas = TrueNASService(self._ssh, server)

    def disconnect(self) -> None:
        if self._ssh:
            self._ssh.close()
            self._ssh = None
            self._truenas = None

    def is_connected(self) -> bool:
        return self._ssh is not None and self._ssh.is_connected

    # -- Helpers -------------------------------------------------------------

    def _ensure_connected(self) -> TrueNASService:
        if self._truenas is None or not self.is_connected():
            self.connect()
        assert self._truenas is not None
        return self._truenas

    @property
    def ssh(self) -> SSHService | None:
        """Expose SSH service for deploy pipeline and other direct use."""
        return self._ssh

    @property
    def truenas(self) -> TrueNASService | None:
        """Expose TrueNAS service for deploy pipeline and other direct use."""
        return self._truenas

    # -- Resource queries ----------------------------------------------------

    def list_resources(self) -> list[Resource]:
        truenas = self._ensure_connected()
        containers = truenas.list_containers()
        resources: list[Resource] = []

        for c in containers:
            status_str = c.get("status", "").lower()
            if "up" in status_str:
                rs = ResourceStatus.RUNNING
            elif "exited" in status_str or "created" in status_str:
                rs = ResourceStatus.STOPPED
            else:
                rs = ResourceStatus.UNKNOWN

            # Try to extract host port from ports string like "0.0.0.0:30213->5000/tcp"
            port = 0
            ports_str = c.get("ports", "")
            if ":" in ports_str and "->" in ports_str:
                try:
                    port = int(ports_str.split(":")[1].split("->")[0])
                except (IndexError, ValueError):
                    pass

            resources.append(Resource(
                id=c.get("name", ""),
                name=c.get("name", ""),
                resource_type=ResourceType.DOCKER_CONTAINER,
                provider_name=self._host_key,
                status=rs,
                host=self._config.host,
                port=port,
                image=c.get("image", ""),
            ))

        return resources

    def get_resource(self, resource_id: str) -> Resource | None:
        for r in self.list_resources():
            if r.id == resource_id:
                return r
        return None

    # -- Lifecycle actions ---------------------------------------------------

    def start(self, resource_id: str) -> bool:
        truenas = self._ensure_connected()
        # Try TrueNAS app first, fall back to docker start
        if truenas.app_exists(resource_id):
            return truenas.app_start(resource_id)
        _, _, code = self._ssh.run_command(  # type: ignore[union-attr]
            f"{self._config.docker_cmd} start {resource_id}"
        )
        return code == 0

    def stop(self, resource_id: str) -> bool:
        truenas = self._ensure_connected()
        if truenas.app_exists(resource_id):
            return truenas.app_stop(resource_id)
        return truenas.stop_container(resource_id)

    def restart(self, resource_id: str) -> bool:
        self.stop(resource_id)
        return self.start(resource_id)

    def remove(self, resource_id: str) -> bool:
        truenas = self._ensure_connected()
        truenas.stop_container(resource_id)
        return truenas.remove_container(resource_id)

    # -- Observability -------------------------------------------------------

    def logs(self, resource_id: str, lines: int = 50) -> str:
        truenas = self._ensure_connected()
        return truenas.container_logs(resource_id, lines=lines)

    def status(self, resource_id: str) -> ResourceStatus:
        truenas = self._ensure_connected()
        cs = truenas.container_status(resource_id)
        if cs == "running":
            return ResourceStatus.RUNNING
        elif cs == "not found":
            return ResourceStatus.UNKNOWN
        return ResourceStatus.STOPPED
