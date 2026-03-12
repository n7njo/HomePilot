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
