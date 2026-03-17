"""Netdata API client for high-resolution host metrics."""

from __future__ import annotations

import logging
import httpx
from homepilot.providers.base import HostMetrics

logger = logging.getLogger(__name__)


class NetdataService:
    """Fetches real-time metrics from a host running Netdata."""

    def __init__(self, host: str, port: int = 19999) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}/api/v1"

    async def fetch_metrics(self) -> HostMetrics | None:
        """Fetch CPU, RAM, and Disk metrics via the Netdata JSON API."""
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                # Attempt to get all metrics in one go as a fallback/primary
                all_resp = await client.get(f"{self.base_url}/allmetrics?format=json")
                if all_resp.status_code == 200:
                    data = all_resp.json()
                    
                    # CPU: system.cpu
                    cpu_data = data.get("system.cpu", {})
                    cpu_pct = 0.0
                    if cpu_data:
                        # dimensions are percentages of total cpu
                        dims = cpu_data.get("dimensions", {})
                        cpu_pct = sum(v.get("value", 0.0) for k, v in dims.items() if k != "idle")

                    # RAM: system.ram
                    ram_data = data.get("system.ram", {})
                    ram_used_gb = 0.0
                    ram_total_gb = 0.0
                    if ram_data:
                        dims = ram_data.get("dimensions", {})
                        used = dims.get("used", {}).get("value", 0)
                        cached = dims.get("cached", {}).get("value", 0)
                        buffers = dims.get("buffers", {}).get("value", 0)
                        free = dims.get("free", {}).get("value", 0)
                        ram_used_gb = used / 1024 # MB to GB? Netdata base unit is often KB or MB
                        # actually system.ram dims are usually MB in allmetrics
                        ram_total_gb = (used + cached + buffers + free) / 1024

                    # Disk: disk_space._ or disk_space./
                    disk_pct = 0.0
                    disk_data = data.get("disk_space./", data.get("disk_space._", {}))
                    if disk_data:
                        dims = disk_data.get("dimensions", {})
                        u = dims.get("used", {}).get("value", 0)
                        a = dims.get("avail", {}).get("value", 0)
                        if (u + a) > 0:
                            disk_pct = (u / (u + a)) * 100

                    return HostMetrics(
                        cpu_pct=cpu_pct,
                        ram_used_gb=ram_used_gb,
                        ram_total_gb=ram_total_gb,
                        disk_pct=disk_pct
                    )

                # Original individual chart fallback...
                # 1. CPU usage (last 1s)
                cpu_resp = await client.get(
                    f"{self.base_url}/data",
                    params={
                        "chart": "system.cpu",
                        "after": -1,
                        "points": 1,
                        "group": "average",
                        "format": "json",
                    }
                )
                
                # 2. RAM usage
                ram_resp = await client.get(
                    f"{self.base_url}/data",
                    params={
                        "chart": "system.ram",
                        "after": -1,
                        "points": 1,
                        "format": "json",
                    }
                )

                # 3. Disk usage (attempt multiple common mount points)
                disk_resp = await client.get(
                    f"{self.base_url}/data",
                    params={
                        "chart": "disk_space./", # Standard for PVE/Debian
                        "after": -1,
                        "points": 1,
                        "format": "json",
                    }
                )
                if disk_resp.status_code != 200:
                    # Fallback for systems that use different names (like TrueNAS)
                    disk_resp = await client.get(
                        f"{self.base_url}/data",
                        params={
                            "chart": "disk_space._",
                            "after": -1,
                            "points": 1,
                            "format": "json",
                        }
                    )

                cpu_pct = 0.0
                if cpu_resp.status_code == 200:
                    data = cpu_resp.json()
                    # system.cpu dimensions: guest_nice, guest, steal, softirq, irq, user, system, nice, iowait
                    # We want sum of all EXCEPT 'idle' (which isn't returned, the chart total is usage)
                    # For system.cpu, Netdata returns components of usage.
                    cpu_pct = sum(data.get("latest_values", [0.0]))

                ram_used = 0.0
                ram_total = 0.0
                if ram_resp.status_code == 200:
                    data = ram_resp.json()
                    # system.ram dimensions: free, used, cached, buffers
                    dims = data.get("labels", [])
                    vals = data.get("latest_values", [])
                    stats = dict(zip(dims, vals))
                    
                    used = stats.get("used", 0)
                    cached = stats.get("cached", 0)
                    buffers = stats.get("buffers", 0)
                    free = stats.get("free", 0)
                    
                    ram_used = used / 1024 # MB
                    ram_total = (used + cached + buffers + free) / 1024 # MB

                disk_pct = 0.0
                if disk_resp.status_code == 200:
                    data = disk_resp.json()
                    # disk_space._ dimensions: used, avail, reserved
                    dims = data.get("labels", [])
                    vals = data.get("latest_values", [])
                    stats = dict(zip(dims, vals))
                    u = stats.get("used", 0)
                    a = stats.get("avail", 0)
                    if (u + a) > 0:
                        disk_pct = (u / (u + a)) * 100

                return HostMetrics(
                    cpu_pct=cpu_pct,
                    ram_used_gb=ram_used / 1024,
                    ram_total_gb=ram_total / 1024,
                    disk_pct=disk_pct
                )

        except Exception as exc:
            logger.debug("Netdata fetch failed for %s: %s", self.host, exc)
            return None
