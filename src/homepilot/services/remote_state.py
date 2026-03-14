"""Remote state — read/write /opt/homepilot/state.yaml on managed hosts.

The state file is the authoritative record of what HomePilot has deployed
to a host.  It lives on the host itself so it remains readable even when
the HomePilot client is unavailable, and survives a re-install of HomePilot.

Schema (YAML):
    version: 1
    host_key: ProxMox
    managed_apps:
      dolt:
        container_name: dolt
        image: dolthub/dolt-sql-server:latest
        deployed_at: 2026-03-13T10:00:00Z
        ports:
          - "3306:3306"
        volumes:
          - host: /opt/homepilot/dolt/data
            container: /var/lib/dolt
        env_keys:
          - DOLT_ROOT_HOST
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from homepilot.models import AppConfig
    from homepilot.services.ssh import SSHService

logger = logging.getLogger(__name__)

STATE_PATH = "/opt/homepilot/state.yaml"
STATE_VERSION = 1


class RemoteStateService:
    """Read and write HomePilot's managed-app state on a remote host."""

    def __init__(self, ssh: SSHService, host_key: str) -> None:
        self._ssh = ssh
        self._host_key = host_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """Return the parsed state dict, or an empty initial state."""
        out, _, code = self._ssh.run_command(f"cat {STATE_PATH} 2>/dev/null")
        if code != 0 or not out.strip():
            return self._empty_state()
        try:
            data = yaml.safe_load(out) or {}
            if not isinstance(data, dict):
                return self._empty_state()
            # Ensure managed_apps key exists
            data.setdefault("managed_apps", {})
            return data
        except Exception as exc:
            logger.warning("Failed to parse remote state: %s", exc)
            return self._empty_state()

    def write(self, state: dict[str, Any]) -> None:
        """Serialise state and write it to the remote host."""
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        raw = yaml.dump(state, default_flow_style=False, allow_unicode=True)
        # Write via heredoc to avoid shell quoting issues with special chars
        escaped = raw.replace("'", "'\"'\"'")
        self._ssh.run_command(
            f"mkdir -p /opt/homepilot && printf '%s' '{escaped}' > {STATE_PATH}"
        )

    def record_deploy(self, app: AppConfig) -> None:
        """Add or update an app's entry in the remote state after deployment."""
        state = self.read()
        state["managed_apps"][app.name] = {
            "container_name": app.deploy.container_name,
            "image": (
                app.deploy.image_name
                if ":" in app.deploy.image_name
                else f"{app.deploy.image_name}:latest"
            ),
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "ports": [f"{app.deploy.host_port}:{app.deploy.container_port}"]
            if app.deploy.host_port
            else [],
            "volumes": [
                {"host": v.host, "container": v.container}
                for v in app.volumes
            ],
            "env_keys": list(app.env.keys()),
        }
        self.write(state)

    def remove_app(self, app_name: str) -> dict[str, Any] | None:
        """Remove an app from the state and return its last recorded entry."""
        state = self.read()
        entry = state["managed_apps"].pop(app_name, None)
        if entry is not None:
            self.write(state)
        return entry

    def get_app(self, app_name: str) -> dict[str, Any] | None:
        """Return the state entry for a single app, or None."""
        return self.read()["managed_apps"].get(app_name)

    def list_apps(self) -> dict[str, Any]:
        """Return all managed app entries."""
        return self.read().get("managed_apps", {})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _empty_state(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "host_key": self._host_key,
            "managed_apps": {},
        }
