"""Tests for data models."""

from __future__ import annotations

from datetime import datetime, timezone

from homepilot.models import (
    AppConfig,
    AppRuntimeInfo,
    AppStatus,
    DeployConfig,
    DeploymentState,
    DeployStep,
    DeployStepStatus,
    HealthStatus,
    PortMode,
    SourceConfig,
    SourceType,
)


class TestAppConfig:
    def test_source_path_local(self, tmp_path):
        app = AppConfig(
            name="test",
            source=SourceConfig(type=SourceType.LOCAL, path=str(tmp_path)),
        )
        assert app.source_path() == tmp_path.resolve()

    def test_source_path_defaults_to_cwd(self):
        app = AppConfig(
            name="test",
            source=SourceConfig(type=SourceType.GIT, path=""),
        )
        # Should not raise
        path = app.source_path()
        assert path is not None


class TestDeploymentState:
    def test_current_step(self):
        state = DeploymentState(
            app_name="test",
            steps=[
                DeployStep("a", "Step A", status=DeployStepStatus.SUCCESS),
                DeployStep("b", "Step B", status=DeployStepStatus.RUNNING),
                DeployStep("c", "Step C", status=DeployStepStatus.PENDING),
            ],
        )
        assert state.current_step is not None
        assert state.current_step.name == "b"

    def test_no_current_step(self):
        state = DeploymentState(
            app_name="test",
            steps=[
                DeployStep("a", "Step A", status=DeployStepStatus.SUCCESS),
            ],
        )
        assert state.current_step is None

    def test_succeeded_all_success(self):
        state = DeploymentState(
            app_name="test",
            steps=[
                DeployStep("a", "Step A", status=DeployStepStatus.SUCCESS),
                DeployStep("b", "Step B", status=DeployStepStatus.SKIPPED),
            ],
        )
        assert state.succeeded is True

    def test_succeeded_with_failure(self):
        state = DeploymentState(
            app_name="test",
            steps=[
                DeployStep("a", "Step A", status=DeployStepStatus.SUCCESS),
                DeployStep("b", "Step B", status=DeployStepStatus.FAILED),
            ],
        )
        assert state.succeeded is False


class TestAppRuntimeInfo:
    def test_to_row(self):
        info = AppRuntimeInfo(
            name="my-app",
            status=AppStatus.RUNNING,
            health=HealthStatus.HEALTHY,
            image_tag="my-app:latest",
            host_port=30213,
            last_deployed=datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc),
        )
        row = info.to_row()
        assert row[0] == "my-app"
        assert row[1] == "Running"
        assert row[2] == "Healthy"
        assert row[3] == "my-app:latest"
        assert row[4] == "30213"
        assert "2025-01-15" in row[5]

    def test_to_row_defaults(self):
        info = AppRuntimeInfo(name="test")
        row = info.to_row()
        assert row[0] == "test"
        assert row[4] == "—"
        assert row[5] == "—"


class TestEnums:
    def test_source_type_values(self):
        assert SourceType.LOCAL.value == "local"
        assert SourceType.GIT.value == "git"

    def test_port_mode_values(self):
        assert PortMode.FIXED.value == "fixed"
        assert PortMode.DYNAMIC.value == "dynamic"
