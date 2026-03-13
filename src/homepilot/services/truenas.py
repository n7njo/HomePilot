"""TrueNAS-specific operations: Docker on the server, midclt app management."""

from __future__ import annotations

import json
import logging
import re
from typing import Callable

from homepilot.models import AppConfig, ServerConfig
from homepilot.services.ssh import SSHService

logger = logging.getLogger(__name__)


class TrueNASService:
    """Interact with Docker and TrueNAS Custom Apps via SSH."""

    def __init__(self, ssh: SSHService, server: ServerConfig) -> None:
        self._ssh = ssh
        self._server = server
        self._docker = server.docker_cmd
        self._midclt = server.midclt_cmd

    # ------------------------------------------------------------------
    # Docker image management (on TrueNAS)
    # ------------------------------------------------------------------

    def load_image(self, remote_tar_path: str) -> bool:
        """Load a Docker image from a tar file on the server."""
        out, err, code = self._ssh.run_command(
            f"{self._docker} load -i {remote_tar_path}", timeout=300
        )
        if code != 0:
            logger.error("docker load failed: %s", err)
            return False
        logger.info("Image loaded: %s", out.strip())
        return True

    def image_exists(self, image_name: str) -> bool:
        """Check if a Docker image exists on the server."""
        out, _, code = self._ssh.run_command(
            f"{self._docker} images -q {image_name}:latest"
        )
        return code == 0 and bool(out.strip())

    # ------------------------------------------------------------------
    # Container management
    # ------------------------------------------------------------------

    def container_status(self, container_name: str) -> str:
        """Return the status of a container: 'running', 'stopped', 'not found'."""
        out, _, code = self._ssh.run_command(
            f'{self._docker} inspect --format "{{{{.State.Status}}}}" {container_name}'
        )
        if code != 0:
            return "not found"
        return out.strip().strip('"')

    def container_exists(self, container_name: str) -> bool:
        """Check if a container exists (running or stopped)."""
        _, _, code = self._ssh.run_command(
            f"{self._docker} inspect {container_name}"
        )
        return code == 0

    def stop_container(self, container_name: str) -> bool:
        """Stop a running container."""
        _, _, code = self._ssh.run_command(f"{self._docker} stop {container_name}")
        return code == 0

    def remove_container(self, container_name: str) -> bool:
        """Remove a container."""
        _, _, code = self._ssh.run_command(f"{self._docker} rm {container_name}")
        return code == 0

    def container_logs(
        self,
        container_name: str,
        lines: int = 50,
        line_callback: Callable[[str], None] | None = None,
    ) -> str:
        """Fetch recent container logs."""
        if line_callback:
            out, _, _ = self._ssh.run_command_stream(
                f"{self._docker} logs --tail {lines} {container_name}",
                line_callback=line_callback,
            )
            return out
        out, _, _ = self._ssh.run_command(
            f"{self._docker} logs --tail {lines} {container_name}"
        )
        return out

    def container_inspect(self, container_name: str) -> dict:
        """Return parsed docker inspect output for a single container."""
        out, _, code = self._ssh.run_command(
            f"{self._docker} inspect {container_name}"
        )
        if code != 0:
            return {}
        try:
            data = json.loads(out)
            return data[0] if data else {}
        except (json.JSONDecodeError, IndexError):
            return {}

    def list_containers(self) -> list[dict[str, str]]:
        """List all containers as dicts with keys: name, status, image, ports."""
        fmt = '{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'
        out, _, code = self._ssh.run_command(
            f'{self._docker} ps -a --format "{fmt}"'
        )
        if code != 0:
            return []

        containers: list[dict[str, str]] = []
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                containers.append({
                    "name": parts[0],
                    "status": parts[1],
                    "image": parts[2],
                    "ports": parts[3],
                })
        return containers

    # ------------------------------------------------------------------
    # TrueNAS Custom App lifecycle (via midclt)
    # ------------------------------------------------------------------

    def app_start(self, app_name: str) -> bool:
        """Start a TrueNAS Custom App."""
        _, err, code = self._ssh.run_command(
            f"{self._midclt} app.start {app_name}", timeout=60
        )
        if code != 0:
            logger.error("midclt app.start failed: %s", err)
            return False
        return True

    def app_stop(self, app_name: str) -> bool:
        """Stop a TrueNAS Custom App."""
        _, err, code = self._ssh.run_command(
            f"{self._midclt} app.stop {app_name}", timeout=60
        )
        if code != 0:
            logger.error("midclt app.stop failed: %s", err)
            return False
        return True

    def app_status(self, app_name: str) -> str:
        """Query the TrueNAS app state: 'RUNNING', 'STOPPED', etc."""
        out, _, code = self._ssh.run_command(
            f"{self._midclt} app.query"
        )
        if code != 0:
            return "UNKNOWN"

        try:
            apps = json.loads(out)
            for app in apps:
                if app.get("name") == app_name:
                    return app.get("state", "UNKNOWN")
        except json.JSONDecodeError:
            pass

        return "NOT_FOUND"

    def app_exists(self, app_name: str) -> bool:
        """Check if a TrueNAS Custom App is registered."""
        return self.app_status(app_name) != "NOT_FOUND"

    def run_container(self, app: AppConfig) -> bool:
        """Create and start a container directly via docker run."""
        image = f"{app.deploy.image_name}:latest"
        name = app.deploy.container_name
        port_map = f"{app.deploy.host_port}:{app.deploy.container_port}"

        parts = [
            self._docker, "run", "-d",
            "--name", name,
            "-p", port_map,
            "--restart", "unless-stopped",
        ]

        for vol in app.volumes:
            vol_str = f"{vol.host}:{vol.container}"
            if vol.mode:
                vol_str += f":{vol.mode}"
            parts.extend(["-v", vol_str])

        for key, val in app.env.items():
            parts.extend(["-e", f"{key}={val}"])

        parts.append(image)

        cmd = " ".join(parts)
        logger.info("Running container: %s", cmd)
        out, err, code = self._ssh.run_command(cmd, timeout=60)
        if code != 0:
            logger.error("docker run failed: %s", err)
            return False
        logger.info("Container started: %s", out.strip()[:12])
        return True

    # ------------------------------------------------------------------
    # Data backup
    # ------------------------------------------------------------------

    def backup_container_data(
        self,
        container_name: str,
        data_path: str,
        backup_dir: str,
    ) -> str | None:
        """Create a tarball backup of container data. Returns backup path or None."""
        # Ensure backup dir exists
        self._ssh.run_command(f"sudo mkdir -p {backup_dir}")

        # Check if container exists and has data
        if not self.container_exists(container_name):
            logger.info("No container %s to backup", container_name)
            return None

        # Create timestamped backup
        out, _, code = self._ssh.run_command("date +%Y%m%d-%H%M%S")
        timestamp = out.strip()
        backup_file = f"{backup_dir}/{container_name}-{timestamp}.tar.gz"

        _, err, code = self._ssh.run_command(
            f"{self._docker} exec {container_name} tar czf - -C {data_path} . "
            f"| sudo tee {backup_file} > /dev/null",
            timeout=300,
        )
        if code != 0:
            logger.warning("Backup failed: %s", err)
            return None

        logger.info("Backup created: %s", backup_file)
        return backup_file

    # ------------------------------------------------------------------
    # Port management
    # ------------------------------------------------------------------

    def get_used_ports(self) -> set[int]:
        """Query all host ports currently in use by Docker containers."""
        out, _, code = self._ssh.run_command(
            f'{self._docker} ps --format "{{{{.Ports}}}}"'
        )
        if code != 0:
            return set()

        ports: set[int] = set()
        # Ports look like: 0.0.0.0:30213->5000/tcp
        for line in out.strip().splitlines():
            for match in re.finditer(r":(\d+)->", line):
                ports.add(int(match.group(1)))
        return ports

    def find_available_port(self, range_start: int, range_end: int) -> int | None:
        """Find the next available port in the given range."""
        used = self.get_used_ports()
        for port in range(range_start, range_end + 1):
            if port not in used:
                return port
        return None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def remove_remote_file(self, path: str) -> None:
        """Delete a file on the remote server."""
        self._ssh.run_command(f"rm -f {path}")
