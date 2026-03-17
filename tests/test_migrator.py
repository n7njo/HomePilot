"""Tests for the migrator service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from homepilot.models import (
    AppConfig,
    HomePilotConfig,
    TrueNASHostConfig,
    SourceConfig,
    DeployConfig,
    VolumeMount,
)
from homepilot.services.migrator import Migrator


@pytest.fixture
def sample_config():
    return HomePilotConfig(
        hosts={
            "src-host": TrueNASHostConfig(host="src.lan", user="neil"),
            "dest-host": TrueNASHostConfig(host="dest.lan", user="neil"),
        },
        apps={
            "my-app": AppConfig(
                name="my-app",
                host="src-host",
                source=SourceConfig(path="/tmp/my-app"),
                deploy=DeployConfig(
                    image_name="my-app",
                    container_name="my-app-container",
                    host_port=30200,
                    container_port=5000,
                ),
                volumes=[VolumeMount(host="/mnt/data", container="/app/data")],
            ),
        },
    )


class TestMigrator:
    @patch("homepilot.services.migrator.SSHService")
    @patch("homepilot.services.migrator.TrueNASService")
    @patch("homepilot.services.migrator.Deployer")
    @patch("homepilot.services.migrator.Path.stat")
    @patch("homepilot.services.migrator.Path.exists")
    @patch("homepilot.services.migrator.Path.unlink")
    def test_migration_steps_sequence(self, mock_unlink, mock_exists, mock_stat, mock_deployer, mock_truenas, mock_ssh, sample_config):
        app = sample_config.apps["my-app"]
        migrator = Migrator(sample_config, app, "dest-host")
        
        # Mock Path behaviors
        mock_stat.return_value.st_size = 1024 * 1024
        mock_exists.return_value = True
        mock_unlink.return_value = None
        
        # Mock SSH behavior
        mock_ssh_inst = MagicMock()
        mock_ssh.return_value = mock_ssh_inst
        mock_ssh_inst.run_command.return_value = ("ok", "", 0)
        
        # Mock TrueNAS behavior
        mock_truenas_inst = MagicMock()
        mock_truenas.return_value = mock_truenas_inst
        mock_truenas_inst.container_exists.return_value = True
        mock_truenas_inst.stop_container.return_value = True
        
        # Mock Deployer behavior
        mock_deployer_inst = MagicMock()
        mock_deployer.return_value = mock_deployer_inst
        mock_deployer_inst.run_sync.return_value = [("step", "success", "msg")]
        mock_deployer_inst.state.succeeded = True

        # Run migration
        events = list(migrator.run_sync())
        
        # Verify steps were yielded
        step_names = [e[0] for e in events if e[1] == "success"]
        assert "connect_source" in step_names
        assert "connect_dest" in step_names
        assert "stop_source" in step_names
        assert "backup_source" in step_names
        assert "pull_data" in step_names
        assert "push_data" in step_names
        assert "extract_dest" in step_names
        assert "deploy_dest" in step_names
        assert "verify_health" in step_names
        assert "cleanup_temp" in step_names

        # Verify SSH connections were made for both hosts
        assert mock_ssh.call_count == 2
        
        # Verify source was stopped
        mock_truenas_inst.stop_container.assert_called_with("my-app-container")
        
        # Verify backup was attempted (tar command)
        # One of the run_command calls should be the tar command
        tar_calls = [args[0] for args, kwargs in mock_ssh_inst.run_command.call_args_list if "tar -czf" in str(args[0])]
        assert len(tar_calls) > 0

    @patch("homepilot.services.migrator.SSHService")
    @patch("homepilot.services.migrator.TrueNASService")
    def test_cleanup_source(self, mock_truenas, mock_ssh, sample_config):
        app = sample_config.apps["my-app"]
        migrator = Migrator(sample_config, app, "dest-host")
        
        mock_ssh_inst = MagicMock()
        mock_ssh.return_value = mock_ssh_inst
        mock_ssh_inst.run_command.return_value = ("ok", "", 0)
        
        mock_truenas_inst = MagicMock()
        mock_truenas.return_value = mock_truenas_inst
        
        # Trigger manual cleanup
        migrator.cleanup_source()
        
        # Verify source container was stopped and removed
        mock_truenas_inst.stop_container.assert_called_with("my-app-container")
        mock_truenas_inst.remove_container.assert_called_with("my-app-container")
        
        # Verify volume removal
        rm_calls = [args[0] for args, kwargs in mock_ssh_inst.run_command.call_args_list if "rm -rf /mnt/data" in str(args[0])]
        assert len(rm_calls) > 0
