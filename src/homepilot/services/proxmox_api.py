"""Low-level Proxmox VE REST API client.

Uses httpx to communicate with the PVE API at ``https://{host}:8006/api2/json/``.
Token auth is handled via the ``Authorization: PVEAPIToken=...`` header.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


@dataclass
class PVEToken:
    """Resolved Proxmox API token credentials."""

    token_id: str  # e.g. "user@pve!token-name"
    token_secret: str

    @property
    def header_value(self) -> str:
        """Value for the Authorization header."""
        return f"PVEAPIToken={self.token_id}={self.token_secret}"


def resolve_token(
    token_id: str,
    token_secret: str = "",
    token_source: str = "env",
) -> PVEToken:
    """Resolve a PVE API token from the configured source.

    Sources (checked in order for "keychain" / "env"):
      1. macOS Keychain (``security`` CLI)
      2. Environment variables ``PROXMOX_API_TOKEN_ID`` / ``PROXMOX_API_TOKEN_SECRET``
      3. ``~/.homelab-tokens`` file
      4. Inline values from config

    Args:
        token_id: The token ID (e.g. ``user@pve!token-name``).
        token_secret: Inline secret (used when token_source is "inline").
        token_source: One of ``"keychain"``, ``"env"``, or ``"inline"``.

    Returns:
        A resolved PVEToken.

    Raises:
        RuntimeError: If no valid token could be resolved.
    """
    if token_source == "inline" and token_secret:
        return PVEToken(token_id=token_id, token_secret=token_secret)

    # Try keychain first (macOS only)
    if token_source in ("keychain", "env") and platform.system() == "Darwin":
        kc_token = _load_from_keychain()
        if kc_token:
            logger.info("Loaded Proxmox API token from macOS Keychain")
            return kc_token

    # Try environment variables
    env_id = os.environ.get("PROXMOX_API_TOKEN_ID", "")
    env_secret = os.environ.get("PROXMOX_API_TOKEN_SECRET", "")
    if env_id and env_secret:
        logger.info("Loaded Proxmox API token from environment variables")
        return PVEToken(token_id=env_id, token_secret=env_secret)

    # Try ~/.homelab-tokens file
    tokens_file = os.path.expanduser("~/.homelab-tokens")
    if os.path.isfile(tokens_file):
        file_token = _load_from_tokens_file(tokens_file)
        if file_token:
            logger.info("Loaded Proxmox API token from %s", tokens_file)
            return file_token

    # Fall back to inline values
    if token_id and token_secret:
        return PVEToken(token_id=token_id, token_secret=token_secret)

    raise RuntimeError(
        "No Proxmox API token could be resolved. "
        "Configure via keychain, PROXMOX_API_TOKEN_ID/SECRET env vars, "
        "~/.homelab-tokens, or inline config."
    )


def _load_from_keychain() -> PVEToken | None:
    """Try to load token from macOS Keychain."""
    try:
        tid = subprocess.run(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", "homelab-proxmox-token-id", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        tsecret = subprocess.run(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-s", "homelab-proxmox-token-secret", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if tid.returncode == 0 and tsecret.returncode == 0:
            token_id = tid.stdout.strip()
            token_secret = tsecret.stdout.strip()
            if token_id and token_secret:
                return PVEToken(token_id=token_id, token_secret=token_secret)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _load_from_tokens_file(path: str) -> PVEToken | None:
    """Parse a bash-style tokens file for PROXMOX_API_TOKEN_* exports."""
    token_id = ""
    token_secret = ""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export PROXMOX_API_TOKEN_ID="):
                    token_id = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("export PROXMOX_API_TOKEN_SECRET="):
                    token_secret = line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None

    if token_id and token_secret:
        return PVEToken(token_id=token_id, token_secret=token_secret)
    return None


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------


class ProxmoxAPI:
    """HTTP client for the Proxmox VE REST API.

    Usage::

        api = ProxmoxAPI("192.168.0.199", token)
        api.connect()
        nodes = api.get_nodes()
        vms = api.get_vms(nodes[0]["node"])
        api.disconnect()
    """

    def __init__(
        self,
        host: str,
        token: PVEToken,
        *,
        port: int = 8006,
        verify_ssl: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._verify_ssl = verify_ssl
        self._client: httpx.Client | None = None

    @property
    def base_url(self) -> str:
        return f"https://{self._host}:{self._port}/api2/json"

    # -- Connection ----------------------------------------------------------

    def connect(self) -> None:
        """Create the HTTP client."""
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": self._token.header_value},
            verify=self._verify_ssl,
            timeout=30.0,
        )

    def disconnect(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def is_connected(self) -> bool:
        return self._client is not None

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self.connect()
        assert self._client is not None
        return self._client

    # -- Generic request -----------------------------------------------------

    def _get(self, path: str) -> dict[str, Any]:
        """GET request, return parsed JSON data field."""
        client = self._ensure_client()
        resp = client.get(path)
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", body)

    def _post(self, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST request, return parsed JSON data field."""
        client = self._ensure_client()
        resp = client.post(path, data=data)
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", body)

    # -- Version / connectivity test -----------------------------------------

    def get_version(self) -> dict[str, Any]:
        """GET /version — returns PVE version info."""
        return self._get("/version")

    def test_connection(self) -> bool:
        """Test API connectivity. Returns True on success."""
        try:
            info = self.get_version()
            version = info.get("version", "unknown")
            logger.info("Connected to Proxmox VE %s", version)
            return True
        except Exception as exc:
            logger.error("Proxmox API connection failed: %s", exc)
            return False

    # -- Nodes ---------------------------------------------------------------

    def get_nodes(self) -> list[dict[str, Any]]:
        """GET /nodes — list cluster nodes."""
        result = self._get("/nodes")
        return result if isinstance(result, list) else []

    def get_node_status(self, node: str) -> dict[str, Any]:
        """GET /nodes/{node}/status — node resource usage."""
        return self._get(f"/nodes/{node}/status")

    # -- VMs (QEMU) ----------------------------------------------------------

    def get_vms(self, node: str) -> list[dict[str, Any]]:
        """GET /nodes/{node}/qemu — list VMs on a node."""
        result = self._get(f"/nodes/{node}/qemu")
        return result if isinstance(result, list) else []

    def get_vm_status(self, node: str, vmid: int) -> dict[str, Any]:
        """GET /nodes/{node}/qemu/{vmid}/status/current — VM status."""
        return self._get(f"/nodes/{node}/qemu/{vmid}/status/current")

    def start_vm(self, node: str, vmid: int) -> dict[str, Any]:
        """POST /nodes/{node}/qemu/{vmid}/status/start"""
        return self._post(f"/nodes/{node}/qemu/{vmid}/status/start")

    def stop_vm(self, node: str, vmid: int) -> dict[str, Any]:
        """POST /nodes/{node}/qemu/{vmid}/status/stop"""
        return self._post(f"/nodes/{node}/qemu/{vmid}/status/stop")

    def shutdown_vm(self, node: str, vmid: int) -> dict[str, Any]:
        """POST /nodes/{node}/qemu/{vmid}/status/shutdown"""
        return self._post(f"/nodes/{node}/qemu/{vmid}/status/shutdown")

    def reboot_vm(self, node: str, vmid: int) -> dict[str, Any]:
        """POST /nodes/{node}/qemu/{vmid}/status/reboot"""
        return self._post(f"/nodes/{node}/qemu/{vmid}/status/reboot")

    # -- LXC containers ------------------------------------------------------

    def get_containers(self, node: str) -> list[dict[str, Any]]:
        """GET /nodes/{node}/lxc — list LXC containers on a node."""
        result = self._get(f"/nodes/{node}/lxc")
        return result if isinstance(result, list) else []

    def get_container_status(self, node: str, vmid: int) -> dict[str, Any]:
        """GET /nodes/{node}/lxc/{vmid}/status/current — LXC status."""
        return self._get(f"/nodes/{node}/lxc/{vmid}/status/current")

    def start_container(self, node: str, vmid: int) -> dict[str, Any]:
        """POST /nodes/{node}/lxc/{vmid}/status/start"""
        return self._post(f"/nodes/{node}/lxc/{vmid}/status/start")

    def stop_container(self, node: str, vmid: int) -> dict[str, Any]:
        """POST /nodes/{node}/lxc/{vmid}/status/stop"""
        return self._post(f"/nodes/{node}/lxc/{vmid}/status/stop")

    def shutdown_container(self, node: str, vmid: int) -> dict[str, Any]:
        """POST /nodes/{node}/lxc/{vmid}/status/shutdown"""
        return self._post(f"/nodes/{node}/lxc/{vmid}/status/shutdown")

    def reboot_container(self, node: str, vmid: int) -> dict[str, Any]:
        """POST /nodes/{node}/lxc/{vmid}/status/reboot"""
        return self._post(f"/nodes/{node}/lxc/{vmid}/status/reboot")

    # -- Cluster resources ---------------------------------------------------

    def get_cluster_resources(self, resource_type: str = "") -> list[dict[str, Any]]:
        """GET /cluster/resources — unified resource list.

        Args:
            resource_type: Optional filter: "vm", "storage", "node", or "".
        """
        path = "/cluster/resources"
        if resource_type:
            path += f"?type={resource_type}"
        result = self._get(path)
        return result if isinstance(result, list) else []
