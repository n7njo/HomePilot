"""Health monitoring for deployed applications."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from homepilot.models import AppConfig, AppRuntimeInfo, HealthStatus, ServerConfig

logger = logging.getLogger(__name__)

# Callback type: (app_name, health_status, response_ms)
HealthEvent = tuple[str, HealthStatus, float]
HealthCallback = Callable[[HealthEvent], None]


# ---------------------------------------------------------------------------
# Synchronous (used by CLI)
# ---------------------------------------------------------------------------

def check_health_sync(host: str, port: int, endpoint: str, timeout: float = 5) -> str:
    """Perform a single health check. Returns 'Healthy' or 'Unhealthy'."""
    url = f"http://{host}:{port}{endpoint}"
    try:
        resp = httpx.get(url, timeout=timeout)
        if resp.status_code == 200:
            return "Healthy"
    except httpx.RequestError:
        pass
    return "Unhealthy"


# ---------------------------------------------------------------------------
# Async (used by TUI)
# ---------------------------------------------------------------------------

async def check_health_async(
    host: str, port: int, endpoint: str, expected_status: int = 200, timeout: float = 5
) -> tuple[HealthStatus, float]:
    """Perform a single async health check.

    Returns (HealthStatus, response_time_ms).
    """
    url = f"http://{host}:{port}{endpoint}"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=timeout)
            elapsed_ms = (time.monotonic() - start) * 1000
            if resp.status_code == expected_status:
                return HealthStatus.HEALTHY, elapsed_ms
            return HealthStatus.UNHEALTHY, elapsed_ms
    except httpx.RequestError:
        elapsed_ms = (time.monotonic() - start) * 1000
        return HealthStatus.UNHEALTHY, elapsed_ms


class HealthMonitor:
    """Periodically checks health of all registered apps.

    Used by the TUI dashboard to display live health status.
    """

    def __init__(
        self,
        server: ServerConfig,
        apps: dict[str, AppConfig],
        callback: HealthCallback | None = None,
    ) -> None:
        self._server = server
        self._apps = apps
        self._callback = callback
        self._running = False
        self._task: asyncio.Task | None = None

        # Cached results
        self.results: dict[str, tuple[HealthStatus, float, datetime]] = {}

    async def check_all(self) -> dict[str, tuple[HealthStatus, float]]:
        """Run health checks for all apps concurrently."""
        tasks = {}
        for name, app in self._apps.items():
            if app.deploy.host_port:
                tasks[name] = check_health_async(
                    self._server.host,
                    app.deploy.host_port,
                    app.health.endpoint,
                    app.health.expected_status,
                )

        results: dict[str, tuple[HealthStatus, float]] = {}
        for name, coro in tasks.items():
            status, ms = await coro
            results[name] = (status, ms)
            now = datetime.now(timezone.utc)
            self.results[name] = (status, ms, now)
            if self._callback:
                self._callback((name, status, ms))

        return results

    async def run_loop(self, interval: float = 30) -> None:
        """Run health checks in a loop until stopped."""
        self._running = True
        while self._running:
            try:
                await self.check_all()
            except Exception as exc:
                logger.warning("Health check loop error: %s", exc)
            await asyncio.sleep(interval)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    def start_background(self, interval: float = 30) -> None:
        """Start the monitoring loop as a background task."""
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self.run_loop(interval))

    def get_status(self, app_name: str) -> tuple[HealthStatus, float]:
        """Get cached health status for an app."""
        if app_name in self.results:
            status, ms, _ = self.results[app_name]
            return status, ms
        return HealthStatus.UNKNOWN, 0.0
