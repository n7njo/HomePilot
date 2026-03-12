"""Paramiko-based SSH/SFTP wrapper for TrueNAS connectivity."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

import paramiko

from homepilot.models import ServerConfig

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]  # (bytes_transferred, total_bytes)


class SSHService:
    """Manages an SSH connection to the TrueNAS server."""

    def __init__(self, server: ServerConfig) -> None:
        self._server = server
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish (or re-establish) the SSH connection."""
        if self._client is not None:
            self.close()

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": self._server.host,
            "username": self._server.user,
        }

        if self._server.ssh_key:
            key_path = Path(self._server.ssh_key).expanduser()
            if key_path.exists():
                connect_kwargs["key_filename"] = str(key_path)
        # Otherwise paramiko will use the SSH agent automatically.

        logger.info("Connecting to %s@%s …", self._server.user, self._server.host)
        client.connect(**connect_kwargs)

        # Enable keep-alive so long-running transfers don't timeout.
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(30)

        self._client = client
        logger.info("SSH connection established.")

    def close(self) -> None:
        """Tear down the connection."""
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    @property
    def is_connected(self) -> bool:
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def _ensure_connected(self) -> paramiko.SSHClient:
        if not self.is_connected:
            self.connect()
        assert self._client is not None
        return self._client

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def run_command(
        self, cmd: str, *, timeout: float = 120
    ) -> tuple[str, str, int]:
        """Execute a command on the remote host.

        Returns (stdout, stderr, exit_code).
        """
        client = self._ensure_connected()
        logger.debug("SSH exec: %s", cmd)

        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")

        if exit_code != 0:
            logger.warning("SSH command failed (exit %d): %s\nstderr: %s", exit_code, cmd, err)

        return out, err, exit_code

    def run_command_stream(
        self,
        cmd: str,
        line_callback: Callable[[str], None] | None = None,
        *,
        timeout: float = 600,
    ) -> tuple[str, str, int]:
        """Execute a command and stream stdout line-by-line via callback.

        Returns (full_stdout, stderr, exit_code) after completion.
        """
        client = self._ensure_connected()
        logger.debug("SSH exec (stream): %s", cmd)

        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)

        lines: list[str] = []
        for raw_line in stdout:
            line = raw_line.rstrip("\n")
            lines.append(line)
            if line_callback:
                line_callback(line)

        exit_code = stdout.channel.recv_exit_status()
        err = stderr.read().decode(errors="replace")
        return "\n".join(lines), err, exit_code

    # ------------------------------------------------------------------
    # SFTP file transfer
    # ------------------------------------------------------------------

    def _get_sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None or self._sftp.get_channel() is None:
            client = self._ensure_connected()
            self._sftp = client.open_sftp()
        return self._sftp

    def upload_file(
        self,
        local_path: str | Path,
        remote_path: str,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Upload a local file to the remote host via SFTP."""
        sftp = self._get_sftp()
        local = Path(local_path)
        total_size = local.stat().st_size
        logger.info("Uploading %s → %s (%d bytes)", local, remote_path, total_size)

        def _progress(transferred: int, total: int) -> None:
            if progress_callback:
                progress_callback(transferred, total)

        sftp.put(str(local), remote_path, callback=_progress)
        logger.info("Upload complete.")

    def download_file(
        self,
        remote_path: str,
        local_path: str | Path,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """Download a remote file to a local path via SFTP."""
        sftp = self._get_sftp()
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading %s → %s", remote_path, local)

        def _progress(transferred: int, total: int) -> None:
            if progress_callback:
                progress_callback(transferred, total)

        sftp.get(remote_path, str(local), callback=_progress)
        logger.info("Download complete.")

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def file_exists(self, remote_path: str) -> bool:
        """Check if a remote file exists."""
        sftp = self._get_sftp()
        try:
            sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            return False

    def __enter__(self) -> SSHService:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
