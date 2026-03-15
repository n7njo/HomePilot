"""Deployment pipeline orchestrator."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generator

from homepilot.models import (
    AppConfig,
    DeploymentState,
    DeployStep,
    DeployStepStatus,
    PortMode,
    ServerConfig,
    SourceType,
)
from homepilot.services.docker import DockerService
from homepilot.services.ssh import SSHService
from homepilot.services.truenas import TrueNASService

logger = logging.getLogger(__name__)

# Type alias for the events yielded by the pipeline.
# (step_name, status_string, message)
DeployEvent = tuple[str, str, str]

LineCallback = Callable[[str], None]


class Deployer:
    """Orchestrates the full build→transfer→deploy pipeline.

    Usage (synchronous / headless)::

        deployer = Deployer(server_cfg, app_cfg)
        for step_name, status, msg in deployer.run_sync():
            print(f"{step_name}: {status} — {msg}")

    The TUI consumes the same events via ``run_sync()`` from a worker thread.
    """

    def __init__(
        self,
        server: ServerConfig,
        app: AppConfig,
        *,
        line_callback: LineCallback | None = None,
    ) -> None:
        self._server = server
        self._app = app
        self._line_cb = line_callback
        self._aborted = False

        self.state: DeploymentState | None = None

        # Services — created lazily in the pipeline.
        self._ssh: SSHService | None = None
        self._truenas: TrueNASService | None = None
        self._docker = DockerService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def abort(self) -> None:
        """Signal the pipeline to abort at the next opportunity."""
        self._aborted = True

    def run_sync(self) -> Generator[DeployEvent, None, None]:
        """Execute the full deploy pipeline, yielding events for each step."""
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
                # Mark remaining steps as skipped.
                self._aborted = True

        self.state.finished_at = datetime.now(timezone.utc)
        self.state.aborted = self._aborted

        # Cleanup SSH connection.
        if self._ssh:
            self._ssh.close()

    # ------------------------------------------------------------------
    # Step definitions
    # ------------------------------------------------------------------

    def _build_steps(self) -> list[DeployStep]:
        return [
            DeployStep("validate_source", "Validate source directory"),
            DeployStep("build_image", "Build Docker image"),
            DeployStep("export_image", "Export image to tar"),
            DeployStep("connect_server", "Connect to TrueNAS via SSH"),
            DeployStep("transfer_image", "Transfer image to TrueNAS"),
            DeployStep("load_image", "Load image on TrueNAS"),
            DeployStep("backup_data", "Backup existing data"),
            DeployStep("stop_app", "Stop existing app"),
            DeployStep("start_app", "Start updated app"),
            DeployStep("verify_health", "Verify health"),
            DeployStep("cleanup", "Cleanup temporary files"),
        ]

    def _execute_step(self, name: str) -> str:
        handler = {
            "validate_source": self._step_validate_source,
            "build_image": self._step_build_image,
            "export_image": self._step_export_image,
            "connect_server": self._step_connect_server,
            "transfer_image": self._step_transfer_image,
            "load_image": self._step_load_image,
            "backup_data": self._step_backup_data,
            "stop_app": self._step_stop_app,
            "start_app": self._step_start_app,
            "verify_health": self._step_verify_health,
            "cleanup": self._step_cleanup,
        }.get(name)

        if handler is None:
            raise RuntimeError(f"Unknown step: {name}")
        return handler()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_image_only(self) -> bool:
        """True when the app uses a pre-built registry image (no source to build)."""
        src = self._app.source
        return bool(self._app.deploy.image_name) and not src.path and not src.git_url

    # ------------------------------------------------------------------
    # Individual step implementations
    # ------------------------------------------------------------------

    def _step_validate_source(self) -> str:
        if self._is_image_only():
            raise _SkipStep(f"Image-only app — using {self._app.deploy.image_name}")

        if self._app.source.type == SourceType.GIT:
            return self._clone_or_pull_git()

        src = self._app.source_path()
        if not src.exists():
            raise RuntimeError(f"Source path does not exist: {src}")

        dockerfile = src / self._app.build.dockerfile
        if not dockerfile.exists():
            raise RuntimeError(f"Dockerfile not found: {dockerfile}")

        return f"Source validated: {src}"

    def _clone_or_pull_git(self) -> str:
        """Clone or pull the git repo for git-sourced apps."""
        url = self._app.source.git_url
        branch = self._app.source.git_branch

        # Use a temp directory if no local path specified.
        if not self._app.source.path:
            tmp = tempfile.mkdtemp(prefix="homepilot-")
            self._app.source.path = tmp

        dest = Path(self._app.source.path)

        if (dest / ".git").exists():
            # Pull latest
            result = subprocess.run(
                ["git", "-C", str(dest), "pull", "origin", branch],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git pull failed: {result.stderr}")
            return f"Git pulled: {branch}"
        else:
            dest.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", "-b", branch, url, str(dest)],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone failed: {result.stderr}")
            return f"Git cloned: {url} ({branch})"

    def _step_build_image(self) -> str:
        if self._is_image_only():
            raise _SkipStep("Image-only app — skipping build")
        src = self._app.source_path()
        tag = self._app.deploy.image_name
        ok = self._docker.build_image(src, self._app.build, tag, self._line_cb)
        if not ok:
            raise RuntimeError("Docker build failed")
        return f"Image built: {tag}:latest"

    def _step_export_image(self) -> str:
        if self._is_image_only():
            raise _SkipStep("Image-only app — skipping export")
        tag = self._app.deploy.image_name
        self._tar_path = Path(tempfile.gettempdir()) / f"{tag}-latest.tar"
        ok = self._docker.save_image(tag, self._tar_path)
        if not ok:
            raise RuntimeError("docker save failed")
        size_mb = self._tar_path.stat().st_size / (1024 * 1024)
        return f"Exported: {self._tar_path.name} ({size_mb:.1f} MB)"

    def _step_connect_server(self) -> str:
        self._ssh = SSHService(self._server)
        self._ssh.connect()
        self._truenas = TrueNASService(self._ssh, self._server)
        return f"Connected to {self._server.user}@{self._server.host}"

    def _step_transfer_image(self) -> str:
        if self._is_image_only():
            raise _SkipStep("Image-only app — skipping transfer")
        assert self._ssh is not None
        remote_path = f"/tmp/{self._tar_path.name}"
        self._remote_tar = remote_path
        self._ssh.upload_file(self._tar_path, remote_path, self._line_cb_progress)
        size_mb = self._tar_path.stat().st_size / (1024 * 1024)
        return f"Transferred {size_mb:.1f} MB"

    def _step_load_image(self) -> str:
        assert self._truenas is not None
        if self._is_image_only():
            image = self._app.deploy.image_name
            ok = self._truenas.pull_image(image, self._line_cb)
            if not ok:
                raise RuntimeError(f"docker pull failed for {image}")
            return f"Pulled from registry: {image}"
        ok = self._truenas.load_image(self._remote_tar)
        if not ok:
            raise RuntimeError("docker load failed on server")
        return f"Image loaded: {self._app.deploy.image_name}:latest"

    def _step_backup_data(self) -> str:
        assert self._truenas is not None
        container = self._app.deploy.container_name

        if not self._truenas.container_exists(container):
            raise _SkipStep("No existing container — skipping backup")

        if not self._app.volumes:
            raise _SkipStep("No volumes configured — skipping backup")

        data_path = self._app.volumes[0].container
        backup = self._truenas.backup_container_data(
            container, data_path, self._server.backup_dir
        )
        if backup:
            return f"Backup: {backup}"
        return "Backup attempted (no data found)"

    def _step_stop_app(self) -> str:
        assert self._truenas is not None
        app_name = self._app.name

        # Try TrueNAS Custom App stop only when midclt is working (not UNKNOWN).
        status = self._truenas.app_status(app_name)
        if status not in ("NOT_FOUND", "UNKNOWN"):
            if status == "RUNNING":
                self._truenas.app_stop(app_name)
                time.sleep(5)
                return f"TrueNAS app '{app_name}' stopped"
            return f"TrueNAS app was already {status}"

        # Fall back to direct container stop (also covers UNKNOWN / midclt unavailable).
        container = self._app.deploy.container_name
        if self._truenas.container_exists(container):
            self._truenas.stop_container(container)
            self._truenas.remove_container(container)
            return f"Container '{container}' stopped and removed"

        raise _SkipStep("No existing app/container found")

    def _step_start_app(self) -> str:
        assert self._truenas is not None
        app_name = self._app.name

        # Only use TrueNAS Custom App path when we have a definitive status.
        # "UNKNOWN" means midclt app.query itself failed — fall through to docker run.
        status = self._truenas.app_status(app_name)
        if status not in ("NOT_FOUND", "UNKNOWN"):
            ok, err = self._truenas.app_start(app_name)
            if not ok:
                raise RuntimeError(
                    f"Failed to start TrueNAS app '{app_name}': {err or 'unknown error'}"
                )
            time.sleep(5)
            return f"TrueNAS app '{app_name}' started"

        # Fall back to direct docker run for new apps or when midclt is unavailable.
        container = self._app.deploy.container_name
        if self._truenas.container_exists(container):
            self._truenas.stop_container(container)
            self._truenas.remove_container(container)

        ok, err = self._truenas.run_container(self._app, self._line_cb)
        if not ok:
            raise RuntimeError(f"Failed to start container '{container}': {err}" if err else f"Failed to start container '{container}'")
        time.sleep(2)

        # Discover the actual host port Docker assigned (for dynamic/0 ports).
        if self._app.deploy.port_mode == PortMode.DYNAMIC or self._app.deploy.host_port == 0:
            assigned = self._truenas.get_container_port(
                container, self._app.deploy.container_port
            )
            if assigned:
                self._app.deploy.host_port = assigned
                if self._line_cb:
                    self._line_cb(f"Host port assigned: {assigned}")

        return f"Container '{container}' started via docker run"

    def _step_verify_health(self) -> str:
        import httpx

        host = self._server.host
        port = self._app.deploy.host_port

        # If port is still 0, ask Docker what it actually assigned.
        if port == 0 and self._truenas is not None:
            assigned = self._truenas.get_container_port(
                self._app.deploy.container_name, self._app.deploy.container_port
            )
            if assigned:
                port = assigned
                self._app.deploy.host_port = assigned

        url = f"http://{host}:{port}{self._app.health.endpoint}"

        # Retry a few times (container may still be starting).
        for attempt in range(5):
            try:
                resp = httpx.get(url, timeout=10)
                if resp.status_code == self._app.health.expected_status:
                    return f"Health OK: {url} → {resp.status_code}"
            except httpx.RequestError:
                pass
            time.sleep(3)

        return f"Health check inconclusive — verify manually at {url}"

    def _step_cleanup(self) -> str:
        messages: list[str] = []

        # Remove local tar.
        if hasattr(self, "_tar_path") and self._tar_path.exists():
            self._tar_path.unlink()
            messages.append("local tar removed")

        # Remove remote tar.
        if hasattr(self, "_remote_tar") and self._truenas:
            self._truenas.remove_remote_file(self._remote_tar)
            messages.append("remote tar removed")

        return "; ".join(messages) if messages else "Nothing to clean up"

    def _line_cb_progress(self, transferred: int, total: int) -> None:
        """Convert SFTP progress to a line callback."""
        if self._line_cb and total > 0:
            pct = (transferred / total) * 100
            self._line_cb(f"Transfer: {pct:.0f}% ({transferred}/{total} bytes)")


class _SkipStep(Exception):
    """Raised to indicate a step should be marked as skipped."""
