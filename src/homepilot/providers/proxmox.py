"""Proxmox VE provider — manages VMs and LXC containers via the PVE REST API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homepilot.providers.base import (
    HealthStatus,
    HostMetrics,
    Resource,
    ResourceStatus,
    ResourceType,
    detect_protocol,
)
from homepilot.services.proxmox_api import ProxmoxAPI, resolve_token

if TYPE_CHECKING:
    from homepilot.models import ProxmoxHostConfig, HomePilotConfig

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

    def __init__(self, host_key: str, config: ProxmoxHostConfig, hp_config: HomePilotConfig) -> None:
        self._host_key = host_key
        self._config = config
        self._hp_config = hp_config
        self._api: ProxmoxAPI | None = None
        self.bootstrap_status: str = "—"
        self.last_metrics: HostMetrics | None = None
        self._using_netdata: bool = False
        self._metrics_history: list[float] = []

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

    @property
    def using_netdata(self) -> bool:
        return self._using_netdata

    @property
    def metrics_history(self) -> list[float]:
        return self._metrics_history

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

    def check_bootstrap(self) -> str:
        """SSH to the host and check whether HomePilot has been bootstrapped.

        Sets and returns self.bootstrap_status:
          '✅ Bootstrapped'       — /opt/homepilot/state.yaml found
          '⚠️  Run Bootstrap (h→b)' — SSH works but state.yaml missing
          '❌ SSH failed'         — could not connect
        """
        try:
            from homepilot.services.ssh import SSHService
            server_cfg = self._config.to_server_config()
            ssh = SSHService(server_cfg)
            ssh.connect()
            try:
                _, _, code = ssh.run_command(
                    "test -f /opt/homepilot/state.yaml", timeout=10
                )
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

            # Determine if this is a HomePilot-managed app
            is_managed = False
            rid = self._make_resource_id(pve_type, node, vmid)
            if name in self._hp_config.apps or rid in self._hp_config.apps:
                is_managed = True

            resources.append(Resource(
                id=rid,
                name=name,
                resource_type=rt,
                provider_name=self._host_key,
                status=self._pve_status_to_resource(status_str),
                host=self._config.host,
                port=vmid,
                uptime=self._uptime_display(uptime),
                managed=is_managed,
                metadata={
                    "node": node,
                    "vmid": vmid,
                    "pve_type": pve_type,
                    "maxmem": item.get("maxmem", 0),
                    "maxcpu": item.get("maxcpu", 0),
                    "template": item.get("template", 0),
                },
            ))

        resources.extend(self._list_docker_resources())
        return resources

    def _list_docker_resources(self) -> list[Resource]:
        """List Docker containers running directly on the Proxmox host via SSH."""
        import re
        try:
            from homepilot.services.ssh import SSHService
            from homepilot.services.truenas import TrueNASService
            server_cfg = self._config.to_server_config()
            ssh = SSHService(server_cfg)
            ssh.connect()
            try:
                svc = TrueNASService(ssh, server_cfg)
                containers = svc.list_containers()
            finally:
                ssh.close()
        except Exception as exc:
            logger.debug("Docker SSH list failed for %s: %s", self._host_key, exc)
            return []

        resources: list[Resource] = []
        for c in containers:
            status_str = c.get("status", "").lower()
            if "up" in status_str:
                rs = ResourceStatus.RUNNING
            elif "exited" in status_str or "created" in status_str:
                rs = ResourceStatus.STOPPED
            else:
                rs = ResourceStatus.UNKNOWN

            port = 0
            m = re.search(r":(\d+)->", c.get("ports", ""))
            if m:
                port = int(m.group(1))

            uptime = ""
            up_match = re.match(r"Up\s+(.+)", c.get("status", ""), re.IGNORECASE)
            if up_match:
                uptime = up_match.group(1).strip()

            image = c.get("image", "")
            name = c.get("name", "")

            # Determine if this is a HomePilot-managed app
            is_managed = False
            if name in self._hp_config.apps:
                is_managed = True
            else:
                for app_cfg in self._hp_config.apps.values():
                    if app_cfg.deploy.container_name == name:
                        is_managed = True
                        break

            resources.append(Resource(
                id=name,
                name=name,
                resource_type=ResourceType.DOCKER_CONTAINER,
                provider_name=self._host_key,
                status=rs,
                host=self._config.host,
                port=port,
                protocol=detect_protocol(port, image),
                image=image,
                uptime=uptime,
                managed=is_managed,
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
        # Check if this is a Docker container (ID is just the name)
        if "/" not in resource_id:
            try:
                from homepilot.services.ssh import SSHService
                from homepilot.services.truenas import TrueNASService
                server_cfg = self._config.to_server_config()
                ssh = SSHService(server_cfg)
                ssh.connect()
                try:
                    svc = TrueNASService(ssh, server_cfg)
                    svc.stop_container(resource_id)
                    return svc.remove_container(resource_id)
                finally:
                    ssh.close()
            except Exception as exc:
                logger.error("Failed to remove Docker container %s: %s", resource_id, exc)
                return False

        # PVE native resource (VM/LXC) deletion remains disabled for safety
        logger.warning("Remove not implemented for Proxmox native resources (safety): %s", resource_id)
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

    def get_metrics(self) -> HostMetrics | None:
        """Fetch node metrics via Netdata (primary) or PVE API (fallback)."""
        # Periodic bootstrap re-check to keep the UI indicator fresh
        self.check_bootstrap()

        m: HostMetrics | None = None

        # 1. Try Netdata first if enabled
        if self._config.enable_netdata:
            from homepilot.services.netdata import NetdataService
            import asyncio
            
            nd = NetdataService(self._config.host, self._config.netdata_port)
            try:
                # We are likely in a background thread here
                m = asyncio.run(nd.fetch_metrics())
                if m:
                    self._using_netdata = True
            except Exception:
                pass

        if not m:
            self._using_netdata = False
            # 2. Fallback to PVE API
            try:
                api = self._ensure_api()
                
                # Try specific node status first (requires Sys.Audit)
                try:
                    nodes = api.get_nodes()
                    if nodes:
                        node_name = nodes[0].get("node")
                        status = api.get_node_status(node_name)
                        
                        cpu = status.get("cpu", 0.0) * 100
                        ram_used = status.get("memory", {}).get("used", 0)
                        ram_total = status.get("memory", {}).get("total", 0)
                        disk_used = status.get("rootfs", {}).get("used", 0)
                        disk_total = status.get("rootfs", {}).get("total", 0)
                        
                        m = HostMetrics(
                            cpu_pct=cpu,
                            ram_used_gb=ram_used / (1024**3),
                            ram_total_gb=ram_total / (1024**3),
                            disk_pct=(disk_used / disk_total * 100) if disk_total else 0.0,
                        )
                except Exception as e:
                    if "Permission check failed" not in str(e):
                        raise e

                if not m:
                    # Secondary fallback: Use cluster resources
                    cluster_resources = api.get_cluster_resources("node")
                    if cluster_resources:
                        node = cluster_resources[0]
                        logger.debug("Proxmox cluster resource data for %s: %s", self._host_key, node)
                        
                        cpu = node.get("cpu", 0.0) * 100
                        ram_used = node.get("mem", 0)
                        ram_total = node.get("maxmem", 0)
                        disk_used = node.get("disk", 0)
                        disk_total = node.get("maxdisk", 0)
                        
                        m = HostMetrics(
                            cpu_pct=cpu,
                            ram_used_gb=ram_used / (1024**3),
                            ram_total_gb=ram_total / (1024**3),
                            disk_pct=(disk_used / disk_total * 100) if disk_total else 0.0,
                        )
            except Exception as exc:
                logger.warning("Failed to fetch Proxmox metrics for %s: %s", self._host_key, exc)

        if m:
            self.last_metrics = m
            self._metrics_history.append(m.cpu_pct)
            if len(self._metrics_history) > 30:
                self._metrics_history.pop(0)
            return m
            
        return None
