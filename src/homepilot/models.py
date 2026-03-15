"""Data models for HomePilot configuration and runtime state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SourceType(str, Enum):
    """Where the application source code lives."""

    LOCAL = "local"
    GIT = "git"


class PortMode(str, Enum):
    """How host ports are assigned."""

    FIXED = "fixed"
    DYNAMIC = "dynamic"


class AppStatus(str, Enum):
    """Runtime status of a deployed container."""

    RUNNING = "Running"
    STOPPED = "Stopped"
    ERROR = "Error"
    UNKNOWN = "Unknown"


class HealthStatus(str, Enum):
    """Health-check result."""

    HEALTHY = "Healthy"
    UNHEALTHY = "Unhealthy"
    UNKNOWN = "Unknown"


class DeployStepStatus(str, Enum):
    """Status of an individual deployment step."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Configuration dataclasses  (serialised to / from ~/.homepilot/config.yaml)
# ---------------------------------------------------------------------------


@dataclass
class ServerConfig:
    """TrueNAS server connection settings (legacy, used internally by services)."""

    host: str = "truenas.local"
    user: str = "neil"
    ssh_key: str = ""  # empty = use agent
    docker_cmd: str = "sudo docker"
    midclt_cmd: str = "sudo -i midclt call"
    data_root: str = "/mnt/tank/apps"
    backup_dir: str = "/tmp/homepilot-backups"
    dynamic_port_range_start: int = 30200
    dynamic_port_range_end: int = 30299


# ---------------------------------------------------------------------------
# Multi-host configuration
# ---------------------------------------------------------------------------


@dataclass
class HostConfig:
    """Base host configuration. Subclassed per provider type."""

    type: str = ""  # "truenas" or "proxmox"
    host: str = ""


@dataclass
class TrueNASHostConfig(HostConfig):
    """TrueNAS host configuration."""

    type: str = "truenas"
    user: str = "neil"
    admin_user: str = ""  # original admin for bootstrap; preserved when user → homepilot
    ssh_key: str = ""  # empty = use SSH agent
    docker_cmd: str = "sudo docker"
    midclt_cmd: str = "sudo -i midclt call"
    data_root: str = "/mnt/tank/apps"
    backup_dir: str = "/tmp/homepilot-backups"
    dynamic_port_range_start: int = 30200
    dynamic_port_range_end: int = 30299

    def to_server_config(self) -> ServerConfig:
        """Convert to legacy ServerConfig for existing services."""
        return ServerConfig(
            host=self.host,
            user=self.user,
            ssh_key=self.ssh_key,
            docker_cmd=self.docker_cmd,
            midclt_cmd=self.midclt_cmd,
            data_root=self.data_root,
            backup_dir=self.backup_dir,
            dynamic_port_range_start=self.dynamic_port_range_start,
            dynamic_port_range_end=self.dynamic_port_range_end,
        )


@dataclass
class ProxmoxHostConfig(HostConfig):
    """Proxmox VE host configuration."""

    type: str = "proxmox"
    token_id: str = ""  # e.g. "user@pve!token-name"
    token_secret: str = ""  # inline secret (prefer keychain/env)
    token_source: str = "env"  # "keychain", "env", or "inline"
    verify_ssl: bool = False
    ssh_user: str = "root"  # for SSH-based operations
    ssh_key: str = ""

    def to_server_config(self) -> "ServerConfig":
        """Return a minimal ServerConfig for SSH operations."""
        return ServerConfig(
            host=self.host,
            user=self.ssh_user,
            ssh_key=self.ssh_key,
            docker_cmd="docker",  # Proxmox: homepilot is in docker group, no sudo needed
        )


@dataclass
class SourceConfig:
    """Where to find the application source."""

    type: SourceType = SourceType.LOCAL
    path: str = ""
    git_url: str = ""
    git_branch: str = "main"


@dataclass
class BuildConfig:
    """Docker build settings."""

    dockerfile: str = "Dockerfile"
    platform: str = "linux/amd64"
    context: str = "."  # relative to source path


@dataclass
class DeployConfig:
    """Deployment target settings."""

    image_name: str = ""
    container_name: str = ""
    compose_file: str = ""  # relative to source, or empty for auto-generated
    host_port: int = 0
    container_port: int = 5000
    port_mode: PortMode = PortMode.FIXED


@dataclass
class HealthConfig:
    """Health-check configuration."""

    endpoint: str = "/api/health"
    expected_status: int = 200
    interval_seconds: int = 30


@dataclass
class VolumeMount:
    """A single volume mapping."""

    host: str = ""
    container: str = ""
    mode: str = ""  # optional mount option, e.g. "ro"


@dataclass
class AppConfig:
    """Complete configuration for a single deployable application."""

    name: str = ""
    host: str = ""  # references a key in HomePilotConfig.hosts
    source: SourceConfig = field(default_factory=SourceConfig)
    build: BuildConfig = field(default_factory=BuildConfig)
    deploy: DeployConfig = field(default_factory=DeployConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    volumes: list[VolumeMount] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    last_deployed: str = ""  # ISO-8601 timestamp, empty = never deployed

    def source_path(self) -> Path:
        """Resolved local path to the source directory."""
        if self.source.type == SourceType.LOCAL:
            return Path(self.source.path).expanduser().resolve()
        # For git sources, the clone directory is managed by the deployer.
        return Path(self.source.path) if self.source.path else Path.cwd()


@dataclass
class HomePilotConfig:
    """Top-level configuration: hosts + apps."""

    hosts: dict[str, HostConfig] = field(default_factory=dict)
    apps: dict[str, AppConfig] = field(default_factory=dict)
    theme: str = "textual-dark"

    # Legacy compatibility — returns the first TrueNAS host as a ServerConfig.
    @property
    def server(self) -> ServerConfig:
        """Return the first TrueNAS host as a legacy ServerConfig."""
        for host_cfg in self.hosts.values():
            if isinstance(host_cfg, TrueNASHostConfig):
                return host_cfg.to_server_config()
        # Fallback: if no TrueNAS host, return defaults
        return ServerConfig()

    def get_host_for_app(self, app_name: str) -> HostConfig | None:
        """Look up the host config for a given app."""
        app = self.apps.get(app_name)
        if app and app.host:
            return self.hosts.get(app.host)
        # Fallback: return first host
        if self.hosts:
            return next(iter(self.hosts.values()))
        return None

    def get_truenas_host(self, key: str | None = None) -> TrueNASHostConfig | None:
        """Return a specific TrueNAS host config, or the first one found."""
        if key and key in self.hosts:
            h = self.hosts[key]
            return h if isinstance(h, TrueNASHostConfig) else None
        for h in self.hosts.values():
            if isinstance(h, TrueNASHostConfig):
                return h
        return None


# ---------------------------------------------------------------------------
# Runtime state (not persisted)
# ---------------------------------------------------------------------------


@dataclass
class DeployStep:
    """A single step in the deployment pipeline."""

    name: str
    description: str
    status: DeployStepStatus = DeployStepStatus.PENDING
    message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class DeploymentState:
    """Full state of an in-progress or completed deployment."""

    app_name: str
    steps: list[DeployStep] = field(default_factory=list)
    aborted: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def current_step(self) -> DeployStep | None:
        for step in self.steps:
            if step.status == DeployStepStatus.RUNNING:
                return step
        return None

    @property
    def succeeded(self) -> bool:
        return all(
            s.status in (DeployStepStatus.SUCCESS, DeployStepStatus.SKIPPED)
            for s in self.steps
        )


@dataclass
class AppRuntimeInfo:
    """Live runtime information for a deployed app."""

    name: str
    status: AppStatus = AppStatus.UNKNOWN
    health: HealthStatus = HealthStatus.UNKNOWN
    image_tag: str = ""
    host_port: int = 0
    last_deployed: datetime | None = None
    last_health_check: datetime | None = None
    health_response_ms: float = 0.0
    container_id: str = ""
    error_message: str = ""

    def to_row(self) -> tuple[str, ...]:
        """Return a tuple suitable for a DataTable row."""
        deployed = (
            self.last_deployed.strftime("%Y-%m-%d %H:%M")
            if self.last_deployed
            else "—"
        )
        return (
            self.name,
            self.status.value,
            self.health.value,
            self.image_tag or "—",
            str(self.host_port) if self.host_port else "—",
            deployed,
        )
