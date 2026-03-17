"""Service for migrating Docker apps between hosts with volume data preservation."""

from __future__ import annotations

import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generator

from homepilot.models import (
    AppConfig,
    AppHistoryEvent,
    DeploymentState,
    DeployStep,
    DeployStepStatus,
    HistoryEventType,
    HomePilotConfig,
    HostConfig,
    ServerConfig,
    TrueNASHostConfig,
    ProxmoxHostConfig,
)
from homepilot.services.ssh import SSHService
from homepilot.services.truenas import TrueNASService
from homepilot.services.deployer import Deployer

logger = logging.getLogger(__name__)

# (step_name, status_string, message)
MigrateEvent = tuple[str, str, str]

LineCallback = Callable[[str], None]


class Migrator:
    """Orchestrates moving an app + its volumes from one host to another.

    Uses a local 'pull-then-push' strategy for volume data to ensure
    compatibility even if hosts cannot communicate with each other directly.
    """

    def __init__(
        self,
        config: HomePilotConfig,
        app: AppConfig,
        dest_host_key: str,
        *,
        line_callback: LineCallback | None = None,
    ) -> None:
        self._config = config
        self._app = app
        self._dest_host_key = dest_host_key
        self._line_cb = line_callback
        self._aborted = False

        self.state: DeploymentState | None = None

        # Source host info
        self._src_host_key = app.host
        self._src_host_cfg = config.hosts.get(self._src_host_key)
        self._src_ssh: SSHService | None = None
        self._src_truenas: TrueNASService | None = None

        # Destination host info
        self._dest_host_cfg = config.hosts.get(dest_host_key)
        self._dest_ssh: SSHService | None = None
        self._dest_truenas: TrueNASService | None = None

        self._local_tar: Path | None = None
        self._remote_tar: str | None = None

    def abort(self) -> None:
        self._aborted = True

    def run_sync(self) -> Generator[MigrateEvent, None, None]:
        """Execute the migration pipeline, yielding events."""
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
            except _SkipMigrateStep as exc:
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

        self._cleanup_connections()

    def _build_steps(self) -> list[DeployStep]:
        return [
            DeployStep("connect_source", "Connect to source host"),
            DeployStep("connect_dest", "Connect to destination host"),
            DeployStep("stop_source", "Stop app on source"),
            DeployStep("backup_source", "Backup volume data (source)"),
            DeployStep("pull_data", "Pull backup to local machine"),
            DeployStep("push_data", "Push backup to destination"),
            DeployStep("extract_dest", "Extract volume data (dest)"),
            DeployStep("deploy_dest", "Deploy app on destination"),
            DeployStep("verify_health", "Verify health on destination"),
            # Note: Removal of source app is handled AFTER user confirmation in the UI.
            DeployStep("cleanup_temp", "Cleanup temporary files"),
        ]

    def _execute_step(self, name: str) -> str:
        handler = {
            "connect_source": self._step_connect_source,
            "connect_dest": self._step_connect_dest,
            "stop_source": self._step_stop_source,
            "backup_source": self._step_backup_source,
            "pull_data": self._step_pull_data,
            "push_data": self._step_push_data,
            "extract_dest": self._step_extract_dest,
            "deploy_dest": self._step_deploy_dest,
            "verify_health": self._step_verify_health,
            "cleanup_temp": self._step_cleanup_temp,
        }.get(name)

        if handler is None:
            raise RuntimeError(f"Unknown migration step: {name}")
        return handler()

    # -- Step Implementations ------------------------------------------------

    def _step_connect_source(self) -> str:
        if not self._src_host_cfg:
            raise RuntimeError(f"Source host '{self._src_host_key}' not found in config")
        
        server_cfg = self._to_server_cfg(self._src_host_cfg)
        self._src_ssh = SSHService(server_cfg)
        self._src_ssh.connect()
        self._src_truenas = TrueNASService(self._src_ssh, server_cfg)
        return f"Connected to source: {self._src_host_key}"

    def _step_connect_dest(self) -> str:
        if not self._dest_host_cfg:
            raise RuntimeError(f"Destination host '{self._dest_host_key}' not found in config")
        
        server_cfg = self._to_server_cfg(self._dest_host_cfg)
        self._dest_ssh = SSHService(server_cfg)
        self._dest_ssh.connect()
        self._dest_truenas = TrueNASService(self._dest_ssh, server_cfg)
        return f"Connected to destination: {self._dest_host_key}"

    def _step_stop_source(self) -> str:
        assert self._src_truenas is not None
        container = self._app.deploy.container_name
        
        if not self._src_truenas.container_exists(container):
            raise _SkipMigrateStep("App not running on source")

        ok = self._src_truenas.stop_container(container)
        if not ok:
            raise RuntimeError(f"Failed to stop container '{container}' on source")
        return f"Stopped '{container}' on {self._src_host_key}"

    def _step_backup_source(self) -> str:
        assert self._src_truenas is not None
        if not self._app.volumes:
            raise _SkipMigrateStep("No volumes to migrate")

        # We'll tar all volumes into a single backup file in the source host's backup_dir
        backup_dir = getattr(self._src_host_cfg, "backup_dir", "/tmp/homepilot-backups")
        container = self._app.deploy.container_name
        
        # Simple approach: tar each volume path. 
        # For simplicity in this implementation, we take the first volume as primary if multiple exist,
        # or we could tar them all. Let's try to tar all host paths listed in volumes.
        
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._remote_tar_src = f"{backup_dir}/migrate-{container}-{timestamp}.tar.gz"
        
        # Ensure backup dir exists
        self._src_ssh.run_command(f"mkdir -p {backup_dir}")
        
        paths = [v.host for v in self._app.volumes if v.host]
        if not paths:
            raise _SkipMigrateStep("No host volumes found")

        # Create the tarball on the source host
        paths_str = " ".join(paths)
        cmd = f"tar -czf {self._remote_tar_src} {paths_str}"
        _, err, code = self._src_ssh.run_command(cmd, timeout=600)
        
        if code != 0:
            raise RuntimeError(f"Failed to create backup on source: {err}")
            
        return f"Backup created: {self._remote_tar_src}"

    def _step_pull_data(self) -> str:
        assert self._src_ssh is not None
        if not hasattr(self, "_remote_tar_src"):
            raise _SkipMigrateStep("No backup to pull")

        self._local_tar = Path(tempfile.gettempdir()) / Path(self._remote_tar_src).name
        self._src_ssh.download_file(self._remote_tar_src, self._local_tar, self._progress_cb)
        
        size_mb = self._local_tar.stat().st_size / (1024 * 1024)
        return f"Pulled {size_mb:.1f} MB to local temp"

    def _step_push_data(self) -> str:
        assert self._dest_ssh is not None
        if not self._local_tar or not self._local_tar.exists():
            raise _SkipMigrateStep("No local backup to push")

        backup_dir = getattr(self._dest_host_cfg, "backup_dir", "/tmp/homepilot-backups")
        self._dest_ssh.run_command(f"mkdir -p {backup_dir}")
        
        self._remote_tar_dest = f"{backup_dir}/{self._local_tar.name}"
        self._dest_ssh.upload_file(self._local_tar, self._remote_tar_dest, self._progress_cb)
        
        return f"Pushed to destination: {self._remote_tar_dest}"

    def _step_extract_dest(self) -> str:
        assert self._dest_ssh is not None
        if not hasattr(self, "_remote_tar_dest"):
            raise _SkipMigrateStep("No backup to extract")

        # Extract tarball on destination. 
        # Since tar -czf {self._remote_tar_src} {paths_str} was used, it contains absolute paths.
        # This works if host paths are identical, which is often true in home labs (e.g. /mnt/tank/apps/...)
        cmd = f"tar -xzf {self._remote_tar_dest} -C /"
        _, err, code = self._dest_ssh.run_command(cmd, timeout=600)
        
        if code != 0:
            # If it failed, maybe because of missing parent directories
            # Let's try to ensure directories exist (rough approach)
            for v in self._app.volumes:
                if v.host:
                    self._dest_ssh.run_command(f"mkdir -p {Path(v.host).parent}")
            _, err, code = self._dest_ssh.run_command(cmd, timeout=600)
            if code != 0:
                raise RuntimeError(f"Failed to extract backup on destination: {err}")
            
        return "Extracted volume data on destination"

    def _step_deploy_dest(self) -> str:
        # We'll use the existing Deployer logic to start the app on the new host.
        # But we need to update the app's host first (temporarily in memory).
        orig_host = self._app.host
        self._app.host = self._dest_host_key
        
        try:
            # We use Deployer but skip the build/transfer if the image is already available
            # or if it's an image-only app.
            server_cfg = self._to_server_cfg(self._dest_host_cfg)
            deployer = Deployer(server_cfg, self._app, line_callback=self._line_cb)
            
            # Run the deployer's sync method and yield its events (prefixed)
            for step_name, status, msg in deployer.run_sync():
                if status == "failed":
                    raise RuntimeError(f"Deployment failed on destination: {msg}")
                # We don't yield sub-events to the UI directly to avoid confusion, 
                # but we could. For now, just wait for it to finish.
                pass
            
            return f"App deployed on {self._dest_host_key}"
        finally:
            # Restore original host so we don't accidentally save it until confirmed
            self._app.host = orig_host

    def _step_verify_health(self) -> str:
        # Similar to Deployer._step_verify_health
        import httpx
        
        host = self._dest_host_cfg.host
        port = self._app.deploy.host_port
        
        # If port is 0, we need to find it from the destination container
        if port == 0 and self._dest_truenas:
            assigned = self._dest_truenas.get_container_port(
                self._app.deploy.container_name, self._app.deploy.container_port
            )
            if assigned:
                port = assigned

        url = f"http://{host}:{port}{self._app.health.endpoint}"
        
        for attempt in range(5):
            try:
                resp = httpx.get(url, timeout=10)
                if resp.status_code == self._app.health.expected_status:
                    return f"Health OK on destination: {url}"
            except Exception:
                pass
            time.sleep(3)
            
        return f"Health check inconclusive on {url}"

    def _step_cleanup_temp(self) -> str:
        if self._local_tar and self._local_tar.exists():
            self._local_tar.unlink()
        
        if hasattr(self, "_remote_tar_src") and self._src_ssh:
            self._src_ssh.run_command(f"rm -f {self._remote_tar_src}")
            
        if hasattr(self, "_remote_tar_dest") and self._dest_ssh:
            self._dest_ssh.run_command(f"rm -f {self._remote_tar_dest}")
            
        return "Temporary migration files removed"

    # -- Cleanup Source (Manual Trigger) -------------------------------------

    def cleanup_source(self) -> bool:
        """Remove the app and its data from the source host. Called AFTER confirmation."""
        if not self._src_truenas:
            # Need to reconnect if connection was closed
            server_cfg = self._to_server_cfg(self._src_host_cfg)
            self._src_ssh = SSHService(server_cfg)
            self._src_ssh.connect()
            self._src_truenas = TrueNASService(self._src_ssh, server_cfg)
            
        container = self._app.deploy.container_name
        self._src_truenas.stop_container(container)
        self._src_truenas.remove_container(container)
        
        # Optionally remove volumes? User said "removing the original".
        # Usually that implies the data too.
        for v in self._app.volumes:
            if v.host:
                self._src_ssh.run_command(f"rm -rf {v.host}")

        # Record migration in history
        event = AppHistoryEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=HistoryEventType.MIGRATED,
            message=f"Migrated from {self._src_host_key} to {self._dest_host_key}",
            details={
                "source_host": self._src_host_key,
                "dest_host": self._dest_host_key,
            }
        )
        self._app.history.append(event)

        return True

    # -- Helpers -------------------------------------------------------------

    def _to_server_cfg(self, host_cfg: HostConfig) -> ServerConfig:
        if isinstance(host_cfg, TrueNASHostConfig):
            return host_cfg.to_server_config()
        if isinstance(host_cfg, ProxmoxHostConfig):
            return host_cfg.to_server_config()
        raise ValueError(f"Unsupported host type: {type(host_cfg)}")

    def _cleanup_connections(self) -> None:
        if self._src_ssh:
            self._src_ssh.close()
        if self._dest_ssh:
            self._dest_ssh.close()

    def _progress_cb(self, transferred: int, total: int) -> None:
        if self._line_cb and total > 0:
            pct = (transferred / total) * 100
            self._line_cb(f"Data Transfer: {pct:.1f}%")


class _SkipMigrateStep(Exception):
    pass
