"""Import Config screen — review and save config extracted from a running container."""

from __future__ import annotations

from pathlib import Path

import yaml

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, Select, Static, TextArea

from homepilot.config import save_config
from homepilot.models import (
    AppConfig,
    BuildConfig,
    DeployConfig,
    HealthConfig,
    HealthProtocol,
    HomePilotConfig,
    PortMode,
    AccessLevel,
    NetworkMode,
    HistoryEventType,
    AppHistoryEvent,
    SourceConfig,
    SourceType,
    VolumeMount,
)
from homepilot.providers import ProviderRegistry
from homepilot.providers.base import Resource

_LEGACY_CONFIG = Path.home() / ".dockpilot" / "config.yaml"

_INTERNAL_ENV_PREFIXES = (
    "PATH=", "HOME=", "HOSTNAME=", "TERM=",
    "NGINX_VERSION=", "NJS_VERSION=", "NJS_RELEASE=",
    "PKG_RELEASE=", "DYNPKG_RELEASE=", "ACME_VERSION=",
)


def _load_legacy_app(container_name: str) -> dict:
    """Look up a matching app in the legacy config by container_name."""
    if not _LEGACY_CONFIG.exists():
        return {}
    try:
        raw = yaml.safe_load(_LEGACY_CONFIG.read_text()) or {}
        for app_data in raw.get("apps", {}).values():
            if isinstance(app_data, dict):
                if app_data.get("deploy", {}).get("container_name") == container_name:
                    return app_data
    except Exception:
        pass
    return {}


class ImportConfigScreen(Screen):
    """Review config extracted from a running container and save it."""

    DEFAULT_CSS = """
    ImportConfigScreen #wizard-body {
        padding: 0 2;
    }
    ImportConfigScreen .section-header {
        text-style: bold;
        color: $accent;
        padding: 1 0 0 0;
    }
    ImportConfigScreen Label {
        color: $text-muted;
        padding: 1 0 0 0;
    }
    ImportConfigScreen Input {
        background: $surface;
        border: tall $primary 40%;
        color: $text;
    }
    ImportConfigScreen Input:focus {
        border: tall $primary;
    }
    ImportConfigScreen Input.-invalid {
        border: tall $error;
    }
    ImportConfigScreen Select {
        background: $surface;
        border: tall $primary 40%;
    }
    ImportConfigScreen Select:focus {
        border: tall $primary;
    }
    ImportConfigScreen TextArea {
        background: $surface;
        border: tall $primary 40%;
        color: $text;
        height: 6;
    }
    ImportConfigScreen TextArea:focus {
        border: tall $primary;
    }
    ImportConfigScreen #import-status, ImportConfigScreen #save-status {
        padding: 0 0 1 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=True),
        Binding("escape", "go_back", "Cancel", show=True),
    ]

    def __init__(
        self,
        config: HomePilotConfig,
        registry: ProviderRegistry,
        resource: Resource,
    ) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._resource = resource

    def compose(self) -> ComposeResult:
        r = self._resource
        yield Header()
        yield VerticalScroll(
            Label(f"\n  Import Config: {r.name}\n", id="import-title"),
            Static("  Extracting configuration from container…", id="import-status"),
            Static("", id="save-status"),

            Label("  Basic Info", classes="section-header"),
            Label("  App Name (config key):"),
            Input(id="app-name", placeholder="my-app"),
            Label("  Image Name:"),
            Input(id="image-name"),

            Label("  Source", classes="section-header"),
            Label("  Source Type:"),
            Select(
                [(t.value, t.value) for t in SourceType],
                value="local",
                id="source-type",
            ),
            Label("  Local Path:"),
            Input(id="source-path", placeholder="/path/to/project"),
            Label("  Git URL:"),
            Input(id="git-url", placeholder="https://github.com/user/repo.git"),
            Label("  Git Branch:"),
            Input(value="main", id="git-branch"),

            Label("  Build", classes="section-header"),
            Label("  Build Context (subdirectory, or '.' for root):"),
            Input(value=".", id="build-context"),

            Label("  Deployment", classes="section-header"),
            Label("  Host Port:"),
            Input(id="host-port"),
            Label("  Container Port:"),
            Input(id="container-port"),
            Label("  Access Level:"),
            Select(
                [(a.value, a.value) for a in AccessLevel],
                value=AccessLevel.PUBLIC.value,
                id="access-level",
            ),
            Label("  Network Mode:"),
            Select(
                [(n.value, n.value) for n in NetworkMode],
                value=NetworkMode.BRIDGE.value,
                id="network-mode",
            ),
            Label("  Public Host Override:"),
            Input(id="public-host", placeholder="e.g. truenas.local"),

            Label("  Health Check", classes="section-header"),
            Label("  Protocol:"),
            Select(
                [(p.value, p.value) for p in HealthProtocol],
                value=HealthProtocol.HTTP.value,
                id="health-protocol",
            ),
            Label("  Endpoint (HTTP only):"),
            Input(value="/", id="health-endpoint"),

            Label("  Volumes", classes="section-header"),
            Label("  One host:container or host:container:mode per line:"),
            TextArea(id="volumes"),

            Label("  Environment Variables", classes="section-header"),
            Label("  One KEY=VALUE per line:"),
            TextArea(id="env-vars"),

            id="wizard-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._load_extracted()

    def _load_extracted(self) -> None:
        from homepilot.providers.truenas import TrueNASProvider

        r = self._resource
        provider = self._registry.get_provider(r.provider_name)
        status = self.query_one("#import-status", Static)

        inspect: dict = {}
        if isinstance(provider, TrueNASProvider):
            inspect = provider.extract_app_config(r.name)

        legacy = _load_legacy_app(r.name)

        if not inspect and not legacy:
            status.update("[red]  Could not extract config — fill in fields manually.[/red]")
            self.query_one("#app-name", Input).value = r.name
            return

        suggested_name = r.name.removesuffix("-app") if r.name.endswith("-app") else r.name
        self.query_one("#app-name", Input).value = suggested_name
        self.query_one("#image-name", Input).value = inspect.get("image_name", "") or suggested_name

        self.query_one("#host-port", Input).value = str(
            inspect.get("host_port") or legacy.get("deploy", {}).get("host_port", 0)
        )
        self.query_one("#container-port", Input).value = str(
            inspect.get("container_port") or legacy.get("deploy", {}).get("container_port", 80)
        )

        if legacy:
            src = legacy.get("source", {})
            src_type = src.get("type", "local")
            try:
                self.query_one("#source-type", Select).value = src_type
            except Exception:
                pass
            self.query_one("#source-path", Input).value = src.get("path", "") if src_type == "local" else ""
            self.query_one("#git-url", Input).value = src.get("git_url", "")
            self.query_one("#git-branch", Input).value = src.get("git_branch", "main")
            self.query_one("#build-context", Input).value = legacy.get("build", {}).get("context", ".")
            self.query_one("#health-endpoint", Input).value = legacy.get("health", {}).get("endpoint", "/")

        vols = inspect.get("volumes", []) or [
            {"host": v.get("host", ""), "container": v.get("container", "")}
            for v in legacy.get("volumes", [])
        ]
        self.query_one("#volumes", TextArea).load_text("\n".join(
            f"{v['host']}:{v['container']}" for v in vols if v.get("host")
        ))

        env: dict[str, str] = {}
        if legacy:
            env = {str(k): str(v) for k, v in legacy.get("env", {}).items()}
        elif inspect:
            for k, v in inspect.get("env", {}).items():
                entry = f"{k}={v}"
                if not any(entry.startswith(p) for p in _INTERNAL_ENV_PREFIXES):
                    env[k] = v

        self.query_one("#env-vars", TextArea).load_text("\n".join(f"{k}={v}" for k, v in env.items()))

        source = "docker inspect + legacy config" if legacy else "docker inspect"
        status.update(f"[green]  Extracted from {source} — review and save.[/green]")

    def action_save(self) -> None:
        save_status = self.query_one("#save-status", Static)
        r = self._resource

        name = self.query_one("#app-name", Input).value.strip()
        if not name:
            save_status.update("[red]App name is required.[/red]")
            return
        if name in self._config.apps:
            save_status.update(f"[red]'{name}' already exists in config.[/red]")
            return

        try:
            src_type = SourceType(str(self.query_one("#source-type", Select).value))
            host_port = int(self.query_one("#host-port", Input).value or "0")
            container_port = int(self.query_one("#container-port", Input).value or "80")
            access_level = AccessLevel(self.query_one("#access-level", Select).value)
            network_mode = NetworkMode(self.query_one("#network-mode", Select).value)
            public_host = self.query_one("#public-host", Input).value.strip()

            health_proto_val = self.query_one("#health-protocol", Select).value
            health_protocol = HealthProtocol(health_proto_val)

            volumes: list[VolumeMount] = []
            for line in self.query_one("#volumes", TextArea).text.splitlines():
                line = line.strip()
                if ":" in line:
                    parts = line.split(":", 2)
                    host = parts[0].strip()
                    container = parts[1].strip() if len(parts) > 1 else ""
                    mode = parts[2].strip() if len(parts) > 2 else ""
                    if host and container:
                        volumes.append(VolumeMount(host=host.strip(), container=container.strip(), mode=mode))

            env: dict[str, str] = {}
            for line in self.query_one("#env-vars", TextArea).text.splitlines():
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

            image_name = self.query_one("#image-name", Input).value.strip() or name

            app = AppConfig(
                name=name,
                host=r.provider_name,
                source=SourceConfig(
                    type=src_type,
                    path=self.query_one("#source-path", Input).value.strip(),
                    git_url=self.query_one("#git-url", Input).value.strip(),
                    git_branch=self.query_one("#git-branch", Input).value.strip() or "main",
                ),
                build=BuildConfig(
                    dockerfile="Dockerfile",
                    platform="linux/amd64",
                    context=self.query_one("#build-context", Input).value.strip() or ".",
                ),
                deploy=DeployConfig(
                    image_name=image_name,
                    container_name=r.name,
                    host_port=host_port,
                    container_port=container_port,
                    port_mode=PortMode.FIXED if host_port else PortMode.DYNAMIC,
                    access_level=access_level,
                    network_mode=network_mode,
                ),
                health=HealthConfig(
                    protocol=health_protocol,
                    endpoint=self.query_one("#health-endpoint", Input).value.strip() or "/",
                ),
                volumes=volumes,
                env=env,
                public_host=public_host,
            )

            self._config.apps[name] = app

            # Record creation in history
            from datetime import datetime, timezone
            app.history.append(AppHistoryEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type=HistoryEventType.CREATED,
                message=f"Application imported from existing container: {r.name}",
            ))

            save_config(self._config)
            save_status.update(f"[green]✅ '{name}' imported and saved.[/green]")

        except Exception as exc:
            save_status.update(f"[red]Error: {exc}[/red]")

    def action_go_back(self) -> None:
        self.app.pop_screen()
