"""Proxmox Docker deployment pipeline — SSH-based pull-from-registry deployer."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Generator

from homepilot.models import (
    AppConfig,
    DeploymentState,
    DeployStep,
    DeployStepStatus,
)
from homepilot.services.ssh import SSHService

if TYPE_CHECKING:
    from homepilot.models import ProxmoxHostConfig

logger = logging.getLogger(__name__)

DeployEvent = tuple[str, str, str]
LineCallback = Callable[[str], None]


class _SkipStep(Exception):
    """Raised to mark a step as skipped rather than failed."""


class ProxmoxDeployer:
    """Deploy a Docker container to a Proxmox host via SSH.

    Unlike the TrueNAS deployer, this pipeline pulls the image directly
    on the remote host rather than building and transferring a tar.
    Suitable for pre-built images (Docker Hub, GHCR, etc.).

    Pipeline steps:
      connect_server → pull_image → backup_data → stop_app
      → start_app → verify_health
    """

    def __init__(
        self,
        host_config: ProxmoxHostConfig,
        app: AppConfig,
        *,
        line_callback: LineCallback | None = None,
    ) -> None:
        self._host = host_config
        self._app = app
        self._line_cb = line_callback
        self._aborted = False
        self._ssh: SSHService | None = None
        self._docker = "docker"  # resolved in connect step
        self.state: DeploymentState | None = None

    # ------------------------------------------------------------------
    # Public interface (matches Deployer)
    # ------------------------------------------------------------------

    def abort(self) -> None:
        self._aborted = True

    def run_sync(self) -> Generator[DeployEvent, None, None]:
        self.state = DeploymentState(
            app_name=self._app.name,
            started_at=datetime.now(timezone.utc),
        )
        steps = self._build_steps()
        self.state.steps = steps

        for step in steps:
            if self._aborted:
                step.status = DeployStepStatus.SKIPPED
                step.message = "Aborted"
                yield (step.name, "skipped", "Aborted by user")
                continue

            step.status = DeployStepStatus.RUNNING
            step.started_at = datetime.now(timezone.utc)
            yield (step.name, "running", step.description)

            try:
                message = self._execute_step(step.name)
                step.status = DeployStepStatus.SUCCESS
                step.message = message
                step.finished_at = datetime.now(timezone.utc)
                yield (step.name, "success", message)
            except _SkipStep as exc:
                step.status = DeployStepStatus.SKIPPED
                step.message = str(exc)
                step.finished_at = datetime.now(timezone.utc)
                yield (step.name, "skipped", str(exc))
            except Exception as exc:
                step.status = DeployStepStatus.FAILED
                step.message = str(exc)
                step.finished_at = datetime.now(timezone.utc)
                yield (step.name, "failed", str(exc))
                self._aborted = True

        self.state.finished_at = datetime.now(timezone.utc)
        self.state.aborted = self._aborted

        if self._ssh:
            self._ssh.close()

    # ------------------------------------------------------------------
    # Step definitions
    # ------------------------------------------------------------------

    def _build_steps(self) -> list[DeployStep]:
        image = self._app.deploy.image_name
        return [
            DeployStep("connect_server", f"Connect to {self._host.host} via SSH"),
            DeployStep("pull_image", f"Pull {image} from registry"),
            DeployStep("backup_data", "Backup existing data volumes"),
            DeployStep("stop_app", "Stop and remove existing container"),
            DeployStep("start_app", "Start container"),
            DeployStep("verify_health", "Verify container is running"),
            DeployStep("record_state", "Record deployment in remote state"),
        ]

    def _execute_step(self, name: str) -> str:
        handler = {
            "connect_server": self._step_connect,
            "pull_image": self._step_pull_image,
            "backup_data": self._step_backup_data,
            "stop_app": self._step_stop_app,
            "start_app": self._step_start_app,
            "verify_health": self._step_verify_health,
            "record_state": self._step_record_state,
        }.get(name)
        if handler is None:
            raise RuntimeError(f"Unknown step: {name}")
        return handler()

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    def _step_connect(self) -> str:
        server_cfg = self._host.to_server_config()
        self._ssh = SSHService(server_cfg)
        self._ssh.connect()

        # Resolve which docker binary to use
        _, _, code = self._run("docker --version")
        if code == 0:
            self._docker = "docker"
        else:
            _, _, code2 = self._run("sudo docker --version")
            if code2 == 0:
                self._docker = "sudo docker"
            else:
                raise RuntimeError(
                    "Docker not found on Proxmox host.\n"
                    "Install Docker on the host or deploy into an LXC that has Docker."
                )

        return f"Connected to {self._host.ssh_user}@{self._host.host} ({self._docker})"

    def _step_pull_image(self) -> str:
        image = self._app.deploy.image_name
        if ":" not in image:
            image = f"{image}:latest"

        def _cb(line: str) -> None:
            if self._line_cb:
                self._line_cb(line)

        assert self._ssh is not None
        out, err, code = self._ssh.run_command_stream(
            f"{self._docker} pull {image}",
            line_callback=_cb,
            timeout=300,
        )
        if code != 0:
            raise RuntimeError(f"docker pull failed: {err.strip()}")
        return f"Pulled {image}"

    def _step_backup_data(self) -> str:
        container = self._app.deploy.container_name
        _, _, code = self._run(f"{self._docker} inspect {container}")
        if code != 0:
            raise _SkipStep("No existing container — skipping backup")

        if not self._app.volumes:
            raise _SkipStep("No volumes configured — skipping backup")

        backup_dir = "/tmp/homepilot-backups"
        self._run(f"mkdir -p {backup_dir}")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backed_up: list[str] = []

        for vol in self._app.volumes:
            backup_file = f"{backup_dir}/{container}-{timestamp}.tar.gz"
            _, _, code = self._run(
                f"{self._docker} exec {container} tar czf - -C {vol.container} . "
                f"| tee {backup_file} > /dev/null",
                timeout=300,
            )
            if code == 0:
                backed_up.append(backup_file)

        return f"Backup saved: {', '.join(backed_up)}" if backed_up else "No data to backup"

    def _step_stop_app(self) -> str:
        container = self._app.deploy.container_name
        _, _, code = self._run(f"{self._docker} inspect {container}")
        if code != 0:
            raise _SkipStep(f"Container '{container}' does not exist — skipping")

        self._run(f"{self._docker} stop {container}", timeout=30)
        self._run(f"{self._docker} rm {container}")
        return f"Container '{container}' stopped and removed"

    def _step_start_app(self) -> str:
        app = self._app
        container = app.deploy.container_name
        image = app.deploy.image_name
        if ":" not in image:
            image = f"{image}:latest"

        parts = [
            self._docker, "run", "-d",
            "--name", container,
            "--restart", "unless-stopped",
        ]

        if app.deploy.host_port and app.deploy.container_port:
            parts += ["-p", f"{app.deploy.host_port}:{app.deploy.container_port}"]

        for vol in app.volumes:
            vol_str = f"{vol.host}:{vol.container}"
            if vol.mode:
                vol_str += f":{vol.mode}"
            parts += ["-v", vol_str]

        for key, val in app.env.items():
            parts += ["-e", f"{key}={val}"]

        parts.append(image)

        cmd = " ".join(parts)
        if self._line_cb:
            self._line_cb(f"$ {cmd}")

        out, err, code = self._run(cmd, timeout=60)
        if code != 0:
            raise RuntimeError(f"docker run failed: {err.strip()}")

        return f"Container started: {out.strip()[:12]}"

    def _step_record_state(self) -> str:
        from homepilot.services.remote_state import RemoteStateService
        state_svc = RemoteStateService(self._ssh, host_key=self._host.host)
        state_svc.record_deploy(self._app)
        return "Deployment recorded in /opt/homepilot/state.yaml"

    def _step_verify_health(self) -> str:
        container = self._app.deploy.container_name
        time.sleep(3)

        out, _, code = self._run(
            f"{self._docker} inspect {container} --format='{{{{.State.Running}}}}'"
        )
        if code != 0:
            raise RuntimeError(f"Container '{container}' not found after start")

        running = out.strip().strip("'")
        if running == "true":
            host = self._host.host
            port = self._app.deploy.host_port
            return f"Container running — {host}:{port}"

        # Container failed to stay up — grab logs for diagnosis
        logs, _, _ = self._run(f"{self._docker} logs --tail 30 {container}")
        raise RuntimeError(
            f"Container exited unexpectedly.\nLast logs:\n{logs}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: str, timeout: float = 120) -> tuple[str, str, int]:
        assert self._ssh is not None, "SSH not connected"
        return self._ssh.run_command(cmd, timeout=timeout)
