"""Tests for the deployer service."""

from __future__ import annotations

from homepilot.models import (
    AppConfig,
    BuildConfig,
    DeployConfig,
    HealthConfig,
    PortMode,
    ServerConfig,
    SourceConfig,
    SourceType,
    VolumeMount,
)
from homepilot.services.deployer import Deployer


def _make_app(tmp_path) -> AppConfig:
    """Create a minimal app config for testing."""
    return AppConfig(
        name="test-app",
        source=SourceConfig(type=SourceType.LOCAL, path=str(tmp_path)),
        build=BuildConfig(dockerfile="Dockerfile"),
        deploy=DeployConfig(
            image_name="test-app",
            container_name="test-app-container",
            host_port=30200,
            container_port=5000,
            port_mode=PortMode.FIXED,
        ),
        health=HealthConfig(endpoint="/health"),
        volumes=[VolumeMount(host="/mnt/data", container="/app/data")],
    )


class TestDeployerSteps:
    def test_steps_are_defined(self, tmp_path):
        server = ServerConfig()
        app = _make_app(tmp_path)
        deployer = Deployer(server, app)
        # Access private method to check steps.
        steps = deployer._build_steps()
        assert len(steps) == 11
        step_names = [s.name for s in steps]
        assert "validate_source" in step_names
        assert "build_image" in step_names
        assert "cleanup" in step_names

    def test_abort_skips_remaining(self, tmp_path):
        server = ServerConfig()
        app = _make_app(tmp_path)

        # Create a Dockerfile so validate_source passes
        (tmp_path / "Dockerfile").write_text("FROM alpine\n")

        deployer = Deployer(server, app)
        events = []

        for step_name, status, message in deployer.run_sync():
            events.append((step_name, status))
            # Abort after first successful step.
            if status == "success":
                deployer.abort()

        # After abort, remaining steps should be skipped.
        skipped = [e for e in events if e[1] == "skipped"]
        assert len(skipped) > 0

    def test_state_is_populated(self, tmp_path):
        server = ServerConfig()
        app = _make_app(tmp_path)

        # No Dockerfile, so it should fail at validate_source.
        deployer = Deployer(server, app)

        for _ in deployer.run_sync():
            pass

        assert deployer.state is not None
        assert deployer.state.app_name == "test-app"
        assert deployer.state.finished_at is not None


class TestDeployerVersion:
    def test_get_commit_hash_with_version_json(self, tmp_path):
        """Verify that version.json takes precedence over git."""
        import json
        from unittest.mock import patch
        
        server = ServerConfig()
        # Create build context directory
        context_dir = tmp_path / "solution_docs"
        context_dir.mkdir()
        
        app = AppConfig(
            name="homestead-docs",
            source=SourceConfig(type=SourceType.LOCAL, path=str(tmp_path)),
            build=BuildConfig(context="solution_docs"),
            deploy=DeployConfig(image_name="homestead-docs"),
        )
        
        # Create version.json with new format
        version_data = {"version": "20260327130522"}
        version_file = context_dir / "version.json"
        with open(version_file, "w") as f:
            json.dump(version_data, f)
            
        deployer = Deployer(server, app)
        
        with patch.object(Deployer, "_is_image_only", return_value=False):
            v = deployer._get_commit_hash()
            assert v == "20260327130522"

    def test_get_commit_hash_with_old_version_json(self, tmp_path):
        """Verify that old version.json format (hash field) is still supported."""
        import json
        from unittest.mock import patch
        
        server = ServerConfig()
        context_dir = tmp_path / "solution_docs"
        context_dir.mkdir()
        
        app = AppConfig(
            name="homestead-docs",
            source=SourceConfig(type=SourceType.LOCAL, path=str(tmp_path)),
            build=BuildConfig(context="solution_docs"),
            deploy=DeployConfig(image_name="homestead-docs"),
        )
        
        # Create version.json with old format
        version_data = {"hash": "cfeec9c"}
        version_file = context_dir / "version.json"
        with open(version_file, "w") as f:
            json.dump(version_data, f)
            
        deployer = Deployer(server, app)
        
        with patch.object(Deployer, "_is_image_only", return_value=False):
            v = deployer._get_commit_hash()
            assert v == "cfeec9c"

    def test_get_commit_hash_falls_back_to_git(self, tmp_path):
        """Verify that it falls back to git if version.json is missing."""
        import subprocess
        from unittest.mock import MagicMock, patch
        
        server = ServerConfig()
        app = AppConfig(
            name="homestead-docs",
            source=SourceConfig(type=SourceType.LOCAL, path=str(tmp_path)),
            build=BuildConfig(context="solution_docs"),
            deploy=DeployConfig(image_name="homestead-docs"),
        )
        
        deployer = Deployer(server, app)
        
        with patch.object(Deployer, "_is_image_only", return_value=False):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="git-hash\n")
                v = deployer._get_commit_hash()
                assert v == "git-hash"
                mock_run.assert_called_once()
