"""Local Docker build and image management operations."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Callable

from homepilot.models import AppConfig, BuildConfig

logger = logging.getLogger(__name__)

LineCallback = Callable[[str], None]


class DockerService:
    """Wraps local Docker CLI operations."""

    # ------------------------------------------------------------------
    # Image building
    # ------------------------------------------------------------------

    def build_image(
        self,
        source_path: Path,
        build: BuildConfig,
        tag: str,
        line_callback: LineCallback | None = None,
    ) -> bool:
        """Build a Docker image. Returns True on success.

        Streams build output line-by-line via *line_callback*.
        """
        context_dir = source_path / build.context
        dockerfile = context_dir / build.dockerfile
        cmd = [
            "docker", "build",
            "--platform", build.platform,
            "-f", str(dockerfile),
            "-t", f"{tag}:latest",
            str(context_dir),
        ]
        logger.info("Building image: %s", " ".join(cmd))

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None

        for line in proc.stdout:
            stripped = line.rstrip("\n")
            if line_callback:
                line_callback(stripped)
            logger.debug("docker build: %s", stripped)

        exit_code = proc.wait()
        if exit_code != 0:
            logger.error("Docker build failed with exit code %d", exit_code)
            return False

        logger.info("Docker build succeeded for %s", tag)
        return True

    # ------------------------------------------------------------------
    # Image export
    # ------------------------------------------------------------------

    def save_image(self, tag: str, output_path: Path) -> bool:
        """Export a Docker image to a tar file. Returns True on success."""
        cmd = ["docker", "save", f"{tag}:latest", "-o", str(output_path)]
        logger.info("Saving image to %s", output_path)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("docker save failed: %s", result.stderr)
            return False

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info("Image saved: %.1f MB", size_mb)
        return True

    # ------------------------------------------------------------------
    # Image inspection
    # ------------------------------------------------------------------

    def inspect_image(self, tag: str) -> dict | None:
        """Return Docker inspect metadata for an image, or None if not found."""
        cmd = ["docker", "inspect", f"{tag}:latest"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        try:
            data = json.loads(result.stdout)
            return data[0] if data else None
        except (json.JSONDecodeError, IndexError):
            return None

    def image_exists(self, tag: str) -> bool:
        """Check if a local Docker image exists."""
        return self.inspect_image(tag) is not None

    def get_image_size(self, tag: str) -> int:
        """Return image size in bytes, or 0 if not found."""
        info = self.inspect_image(tag)
        if info:
            return info.get("Size", 0)
        return 0

    def get_image_architecture(self, tag: str) -> str:
        """Return image architecture string, e.g. 'amd64'."""
        info = self.inspect_image(tag)
        if info:
            return info.get("Architecture", "unknown")
        return "unknown"
