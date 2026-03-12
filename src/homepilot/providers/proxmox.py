"""Proxmox VE provider — manages VMs and LXC containers via the PVE REST API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homepilot.providers.base import (
    HealthStatus,
    Resource,
    ResourceStatus,
    ResourceType,
)
from homepilot.services.proxmox_api import ProxmoxAPI, resolve_token

if TYPE_CHECKING:
    from homepilot.models import ProxmoxHostConfig

logger = logging.getLogger(__name__)

# PVE status string → ResourceStatus mapping
_STATUS_MAP: dict[str, ResourceStatus] = {
    "running": ResourceStatus.RUNNING,
    "stopped": ResourceStatus.STOPPED,
    "paused": ResourceStatus.STOPPED,
    "unknown": ResourceStatus.UNKNOWN,
}


class ProxmoxProvider:
    """InfraProvider implementation for Proxmox VE hosts.

    Lists VMs and LXC containers across all nodes and exposes
    start/stop/restart/remove/logs/status through the unified interface.
    """

    def __init__(self, host_key: str, config: ProxmoxHostConfig) -> None:
        self._host_key = host_key
        self._config = config
        self._api: ProxmoxAPI | None = None

    # -- Protocol properties -------------------------------------------------

    @property
    def name(self) -> str:
        return self._host_key

    @property
    def host_display(self) -> str:
        return self._config.host

    @property
    def provider_type(self) -> str:
        return "proxmox"

    # -- Connection lifecycle ------------------------------------------------

    def connect(self) -> None:
        token = resolve_token(
            token_id=self._config.token_id,
            token_secret=self._config.token_secret,
            token_source=self._config.token_source,
        )
        self._api = ProxmoxAPI(
            host=self._config.host,
            token=token,
            verify_ssl=self._config.verify_ssl,
        )
        self._api.connect()

    def disconnect(self) -> None:
        if self._api:
            self._api.disconnect()
            self._api = None

    def is_connected(self) -> bool:
        return self._api is not None and self._api.is_connected()

    # -- Helpers -------------------------------------------------------------

    def _ensure_api(self) -> ProxmoxAPI:
        if self._api is None or not self._api.is_connected():
            self.connect()
        assert self._api is not None
        return self._api

    @staticmethod
    def _pve_status_to_resource(status_str: str) -> ResourceStatus:
        return _STATUS_MAP.get(status_str.lower(), ResourceStatus.UNKNOWN)

    @staticmethod
    def _make_resource_id(resource_type: str, node: str, vmid: int) -> str:
        """Encode node + vmid into a string ID like 'qemu/pve/100'."""
        return f"{resource_type}/{node}/{vmid}"

    @staticmethod
    def _parse_resource_id(resource_id: str) -> tuple[str, str, int]:
        """Decode a resource ID into (type, node, vmid)."""
        parts = resource_id.split("/")
        if len(parts) != 3:
            raise ValueError(f"Invalid Proxmox resource ID: {resource_id}")
        return parts[0], parts[1], int(parts[2])

    def _uptime_display(self, seconds: int) -> str:
        if seconds <= 0:
            return ""
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        mins, _ = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        parts.append(f"{mins}m")
        return " ".join(parts)

    # -- Resource queries ----------------------------------------------------

    def list_resources(self) -> list[Resource]:
        api = self._ensure_api()
        resources: list[Resource] = []

        try:
            cluster_resources = api.get_cluster_resources("vm")
        except Exception as exc:
            logger.warning("Failed to list Proxmox resources: %s", exc)
            return []

        for item in cluster_resources:
            pve_type = item.get("type", "")  # "qemu" or "lxc"
            node = item.get("node", "")
            vmid = item.get("vmid", 0)
            name = item.get("name", f"{pve_type}-{vmid}")
            status_str = item.get("status", "unknown")
            uptime = item.get("uptime", 0)

            if pve_type == "qemu":
                rt = ResourceType.VM
            elif pve_type == "lxc":
                rt = ResourceType.LXC_CONTAINER
            else:
                continue

            resources.append(Resource(
                id=self._make_resource_id(pve_type, node, vmid),
                name=name,
                resource_type=rt,
                provider_name=self._host_key,
                status=self._pve_status_to_resource(status_str),
                host=self._config.host,
                port=vmid,  # use VMID in the port column for display
                uptime=self._uptime_display(uptime),
                metadata={
                    "node": node,
                    "vmid": vmid,
                    "pve_type": pve_type,
                    "maxmem": item.get("maxmem", 0),
                    "maxcpu": item.get("maxcpu", 0),
                    "template": item.get("template", 0),
                },
            ))

        return resources

    def get_resource(self, resource_id: str) -> Resource | None:
        for r in self.list_resources():
            if r.id == resource_id:
                return r
        return None

    # -- Lifecycle actions ---------------------------------------------------

    def start(self, resource_id: str) -> bool:
        api = self._ensure_api()
        try:
            rtype, node, vmid = self._parse_resource_id(resource_id)
            if rtype == "qemu":
                api.start_vm(node, vmid)
            elif rtype == "lxc":
                api.start_container(node, vmid)
            else:
                return False
            return True
        except Exception as exc:
            logger.error("Failed to start %s: %s", resource_id, exc)
            return False

    def stop(self, resource_id: str) -> bool:
        api = self._ensure_api()
        try:
            rtype, node, vmid = self._parse_resource_id(resource_id)
            if rtype == "qemu":
                api.shutdown_vm(node, vmid)
            elif rtype == "lxc":
                api.shutdown_container(node, vmid)
            else:
                return False
            return True
        except Exception as exc:
            logger.error("Failed to stop %s: %s", resource_id, exc)
            return False

    def restart(self, resource_id: str) -> bool:
        api = self._ensure_api()
        try:
            rtype, node, vmid = self._parse_resource_id(resource_id)
            if rtype == "qemu":
                api.reboot_vm(node, vmid)
            elif rtype == "lxc":
                api.reboot_container(node, vmid)
            else:
                return False
            return True
        except Exception as exc:
            logger.error("Failed to restart %s: %s", resource_id, exc)
            return False

    def remove(self, resource_id: str) -> bool:
        # PVE resource deletion is destructive; require explicit stop first.
        logger.warning("Remove not implemented for Proxmox resources (safety)")
        return False

    # -- Observability -------------------------------------------------------

    def logs(self, resource_id: str, lines: int = 50) -> str:
        """Fetch syslog-style output for a resource.

        PVE doesn't have a direct container-log API like Docker.
        For LXC we can query the task log; for VMs it's limited.
        """
        api = self._ensure_api()
        try:
            rtype, node, vmid = self._parse_resource_id(resource_id)
            if rtype == "lxc":
                # Read /nodes/{node}/lxc/{vmid}/status/current for basic info
                status = api.get_container_status(node, vmid)
                info_lines = [
                    f"LXC {vmid} on {node}",
                    f"Status: {status.get('status', 'unknown')}",
                    f"Uptime: {self._uptime_display(status.get('uptime', 0))}",
                    f"Memory: {status.get('mem', 0) // (1024*1024)}MB / {status.get('maxmem', 0) // (1024*1024)}MB",
                    f"CPU: {status.get('cpus', 0)} cores",
                ]
                return "\n".join(info_lines)
            elif rtype == "qemu":
                status = api.get_vm_status(node, vmid)
                info_lines = [
                    f"VM {vmid} on {node}",
                    f"Status: {status.get('status', 'unknown')}",
                    f"Uptime: {self._uptime_display(status.get('uptime', 0))}",
                    f"Memory: {status.get('mem', 0) // (1024*1024)}MB / {status.get('maxmem', 0) // (1024*1024)}MB",
                    f"CPU: {status.get('cpus', 0)} cores",
                ]
                return "\n".join(info_lines)
        except Exception as exc:
            return f"Error fetching logs: {exc}"

        return ""

    def status(self, resource_id: str) -> ResourceStatus:
        api = self._ensure_api()
        try:
            rtype, node, vmid = self._parse_resource_id(resource_id)
            if rtype == "qemu":
                data = api.get_vm_status(node, vmid)
            elif rtype == "lxc":
                data = api.get_container_status(node, vmid)
            else:
                return ResourceStatus.UNKNOWN
            return self._pve_status_to_resource(data.get("status", "unknown"))
        except Exception:
            return ResourceStatus.UNKNOWN
