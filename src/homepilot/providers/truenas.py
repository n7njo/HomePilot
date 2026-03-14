"""TrueNAS provider — wraps SSHService + TrueNASService behind InfraProvider."""

from __future__ import annotations

import logging
import re
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
        self.bootstrap_status: str = "—"

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

    def check_bootstrap(self) -> str:
        """SSH to the host and check whether HomePilot has been bootstrapped.

        Sets and returns self.bootstrap_status:
          '✅ Bootstrapped'       — /mnt/tank/homepilot/state.yaml found
          '⚠️  Run Bootstrap (h→b)' — SSH works but state.yaml missing
          '❌ SSH failed'         — could not connect
        """
        from homepilot.services.bootstrap import TRUENAS_HOMEPILOT_DIR
        state_path = f"{TRUENAS_HOMEPILOT_DIR}/state.yaml"
        try:
            server_cfg = self._config.to_server_config()
            ssh = SSHService(server_cfg)
            ssh.connect()
            try:
                _, _, code = ssh.run_command(f"test -f {state_path}", timeout=10)
                if code == 0:
                    self.bootstrap_status = "✅ Bootstrapped"
                else:
                    self.bootstrap_status = "⚠️  Run Bootstrap (h→b)"
            finally:
                ssh.close()
        except Exception as exc:
            logger.debug("Bootstrap check SSH failed for %s: %s", self._host_key, exc)
            self.bootstrap_status = "❌ SSH failed"
        return self.bootstrap_status

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

            # Extract first host port from ports string like "0.0.0.0:30213->5000/tcp"
            port = 0
            ports_str = c.get("ports", "")
            m = re.search(r":(\d+)->", ports_str)
            if m:
                port = int(m.group(1))

            # Extract uptime from status string like "Up 2 hours" or "Up 3 days"
            uptime = ""
            status_str_raw = c.get("status", "")
            up_match = re.match(r"Up\s+(.+)", status_str_raw, re.IGNORECASE)
            if up_match:
                uptime = up_match.group(1).strip()

            resources.append(Resource(
                id=c.get("name", ""),
                name=c.get("name", ""),
                resource_type=ResourceType.DOCKER_CONTAINER,
                provider_name=self._host_key,
                status=rs,
                host=self._config.host,
                port=port,
                image=c.get("image", ""),
                uptime=uptime,
            ))

        return resources

    def extract_app_config(self, container_name: str) -> dict:
        """Extract an AppConfig-compatible dict from a running container via docker inspect."""
        truenas = self._ensure_connected()
        data = truenas.container_inspect(container_name)
        if not data:
            return {}

        host_config = data.get("HostConfig", {})
        config = data.get("Config", {})

        # Image
        image_raw = config.get("Image", container_name)
        image_name = image_raw.split(":")[0].split("/")[-1]

        # Port bindings — pick first host port
        host_port = 0
        container_port = 5000
        port_bindings = host_config.get("PortBindings") or {}
        for proto_port, bindings in port_bindings.items():
            if bindings:
                try:
                    container_port = int(proto_port.split("/")[0])
                    host_port = int(bindings[0].get("HostPort", 0))
                except (ValueError, IndexError):
                    pass
                break

        # Volume mounts
        volumes = []
        for bind in (host_config.get("Binds") or []):
            parts = bind.split(":")
            if len(parts) >= 2:
                volumes.append({"host": parts[0], "container": parts[1]})

        # Environment variables (filter out internal Docker vars)
        env: dict[str, str] = {}
        _skip_prefixes = ("PATH=", "HOME=", "HOSTNAME=", "TERM=")
        for entry in (config.get("Env") or []):
            if any(entry.startswith(p) for p in _skip_prefixes):
                continue
            if "=" in entry:
                k, v = entry.split("=", 1)
                env[k] = v

        return {
            "container_name": container_name,
            "image_name": image_name,
            "image_tag": image_raw,
            "host_port": host_port,
            "container_port": container_port,
            "volumes": volumes,
            "env": env,
        }

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
