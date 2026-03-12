"""Configuration loading, saving, and validation for HomePilot."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from homepilot.models import (
    AppConfig,
    BuildConfig,
    DeployConfig,
    HomePilotConfig,
    HealthConfig,
    PortMode,
    ServerConfig,
    SourceConfig,
    SourceType,
    VolumeMount,
)

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".homepilot"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


# ---------------------------------------------------------------------------
# Default / seed configuration
# ---------------------------------------------------------------------------

def _default_config() -> HomePilotConfig:
    """Return a default config pre-seeded with House_Tracker."""
    return HomePilotConfig(
        server=ServerConfig(),
        apps={
            "house-tracker": AppConfig(
                name="house-tracker",
                source=SourceConfig(
                    type=SourceType.LOCAL,
                    path="/Users/neil/Local Projects/Development/Application/House_Tracker",
                ),
                build=BuildConfig(
                    dockerfile="Dockerfile",
                    platform="linux/amd64",
                    context=".",
                ),
                deploy=DeployConfig(
                    image_name="house-tracker",
                    container_name="house-tracker-app",
                    compose_file="truenas-app.yaml",
                    host_port=30213,
                    container_port=5000,
                    port_mode=PortMode.FIXED,
                ),
                health=HealthConfig(
                    endpoint="/api/health",
                    expected_status=200,
                    interval_seconds=30,
                ),
                volumes=[
                    VolumeMount(
                        host="/mnt/tank/apps/house-tracker/data",
                        container="/app/data",
                    ),
                ],
                env={
                    "NODE_ENV": "production",
                    "PORT": "5000",
                    "DATA_DIR": "/app/data",
                },
            ),
        },
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _server_to_dict(server: ServerConfig) -> dict[str, Any]:
    return {
        "host": server.host,
        "user": server.user,
        "ssh_key": server.ssh_key,
        "docker_cmd": server.docker_cmd,
        "midclt_cmd": server.midclt_cmd,
        "data_root": server.data_root,
        "backup_dir": server.backup_dir,
        "dynamic_port_range_start": server.dynamic_port_range_start,
        "dynamic_port_range_end": server.dynamic_port_range_end,
    }


def _app_to_dict(app: AppConfig) -> dict[str, Any]:
    return {
        "source": {
            "type": app.source.type.value,
            "path": app.source.path,
            "git_url": app.source.git_url,
            "git_branch": app.source.git_branch,
        },
        "build": {
            "dockerfile": app.build.dockerfile,
            "platform": app.build.platform,
            "context": app.build.context,
        },
        "deploy": {
            "image_name": app.deploy.image_name,
            "container_name": app.deploy.container_name,
            "compose_file": app.deploy.compose_file,
            "host_port": app.deploy.host_port,
            "container_port": app.deploy.container_port,
            "port_mode": app.deploy.port_mode.value,
        },
        "health": {
            "endpoint": app.health.endpoint,
            "expected_status": app.health.expected_status,
            "interval_seconds": app.health.interval_seconds,
        },
        "volumes": [
            {"host": v.host, "container": v.container} for v in app.volumes
        ],
        "env": dict(app.env),
        "last_deployed": app.last_deployed,
    }


def config_to_dict(config: HomePilotConfig) -> dict[str, Any]:
    """Serialise the full config to a plain dict for YAML output."""
    return {
        "server": _server_to_dict(config.server),
        "apps": {name: _app_to_dict(app) for name, app in config.apps.items()},
        "theme": config.theme,
    }


# ---------------------------------------------------------------------------
# Deserialisation helpers
# ---------------------------------------------------------------------------

def _parse_server(data: dict[str, Any]) -> ServerConfig:
    return ServerConfig(
        host=data.get("host", "truenas.local"),
        user=data.get("user", "neil"),
        ssh_key=data.get("ssh_key", ""),
        docker_cmd=data.get("docker_cmd", "sudo docker"),
        midclt_cmd=data.get("midclt_cmd", "sudo -i midclt call"),
        data_root=data.get("data_root", "/mnt/tank/apps"),
        backup_dir=data.get("backup_dir", "/tmp/homepilot-backups"),
        dynamic_port_range_start=data.get("dynamic_port_range_start", 30200),
        dynamic_port_range_end=data.get("dynamic_port_range_end", 30299),
    )


def _parse_app(name: str, data: dict[str, Any]) -> AppConfig:
    src = data.get("source", {})
    bld = data.get("build", {})
    dep = data.get("deploy", {})
    hlth = data.get("health", {})
    vols = data.get("volumes", [])
    env = data.get("env", {})

    return AppConfig(
        name=name,
        source=SourceConfig(
            type=SourceType(src.get("type", "local")),
            path=src.get("path", ""),
            git_url=src.get("git_url", ""),
            git_branch=src.get("git_branch", "main"),
        ),
        build=BuildConfig(
            dockerfile=bld.get("dockerfile", "Dockerfile"),
            platform=bld.get("platform", "linux/amd64"),
            context=bld.get("context", "."),
        ),
        deploy=DeployConfig(
            image_name=dep.get("image_name", name),
            container_name=dep.get("container_name", f"{name}-app"),
            compose_file=dep.get("compose_file", ""),
            host_port=int(dep.get("host_port", 0)),
            container_port=int(dep.get("container_port", 5000)),
            port_mode=PortMode(dep.get("port_mode", "fixed")),
        ),
        health=HealthConfig(
            endpoint=hlth.get("endpoint", "/api/health"),
            expected_status=int(hlth.get("expected_status", 200)),
            interval_seconds=int(hlth.get("interval_seconds", 30)),
        ),
        volumes=[
            VolumeMount(host=v.get("host", ""), container=v.get("container", ""))
            for v in vols
        ],
        env={str(k): str(v) for k, v in env.items()},
        last_deployed=data.get("last_deployed", ""),
    )


def dict_to_config(data: dict[str, Any]) -> HomePilotConfig:
    """Parse a raw YAML dict into a HomePilotConfig."""
    server = _parse_server(data.get("server", {}))
    apps: dict[str, AppConfig] = {}
    for name, app_data in data.get("apps", {}).items():
        apps[name] = _parse_app(name, app_data)
    theme = data.get("theme", "dark")
    return HomePilotConfig(server=server, apps=apps, theme=theme)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config() -> HomePilotConfig:
    """Load config from disk, creating defaults if it doesn't exist."""
    if not CONFIG_FILE.exists():
        logger.info("No config found — creating default at %s", CONFIG_FILE)
        config = _default_config()
        save_config(config)
        return config

    with open(CONFIG_FILE, "r") as f:
        raw = yaml.safe_load(f) or {}

    return dict_to_config(raw)


def save_config(config: HomePilotConfig) -> None:
    """Write config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = config_to_dict(config)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    logger.info("Config saved to %s", CONFIG_FILE)


def validate_config(config: HomePilotConfig) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors: list[str] = []

    if not config.server.host:
        errors.append("server.host is required")
    if not config.server.user:
        errors.append("server.user is required")

    for name, app in config.apps.items():
        prefix = f"apps.{name}"
        if app.source.type == SourceType.LOCAL and not app.source.path:
            errors.append(f"{prefix}.source.path is required for local sources")
        if app.source.type == SourceType.GIT and not app.source.git_url:
            errors.append(f"{prefix}.source.git_url is required for git sources")
        if not app.deploy.image_name:
            errors.append(f"{prefix}.deploy.image_name is required")
        if app.deploy.port_mode == PortMode.FIXED and app.deploy.host_port == 0:
            errors.append(f"{prefix}.deploy.host_port is required when port_mode=fixed")

    return errors
