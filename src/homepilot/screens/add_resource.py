"""Add Resource wizard — register a new app or bookmark a Proxmox resource."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)

from homepilot.config import save_config, validate_config
from homepilot.models import (
    AppConfig,
    BuildConfig,
    DeployConfig,
    HomePilotConfig,
    HealthConfig,
    PortMode,
    AccessLevel,
    NetworkMode,
    HistoryEventType,
    AppHistoryEvent,
    SourceConfig,
    SourceType,
    TrueNASHostConfig,
    VolumeMount,
)
from homepilot.providers import ProviderRegistry


class AddResourceScreen(Screen):
    """Wizard to register a new application or resource."""

    DEFAULT_CSS = """
    AddResourceScreen #wizard-body {
        padding: 0 2;
    }
    AddResourceScreen .section-header {
        text-style: bold;
        color: $accent;
        padding: 1 0 0 0;
    }
    AddResourceScreen Label {
        color: $text-muted;
        padding: 1 0 0 0;
    }
    AddResourceScreen Input {
        background: $surface;
        border: tall $primary 40%;
        color: $text;
    }
    AddResourceScreen Input:focus {
        border: tall $primary;
    }
    AddResourceScreen Input.-invalid {
        border: tall $error;
    }
    AddResourceScreen Select {
        background: $surface;
        border: tall $primary 40%;
    }
    AddResourceScreen Select:focus {
        border: tall $primary;
    }
    AddResourceScreen TextArea {
        background: $surface;
        border: tall $primary 40%;
        color: $text;
        height: 6;
    }
    AddResourceScreen TextArea:focus {
        border: tall $primary;
    }
    AddResourceScreen #wizard-status {
        padding: 0 0 1 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=True),
        Binding("ctrl+d", "auto_detect", "Auto-Detect", show=True),
        Binding("escape", "go_back", "Back", show=True),
    ]

    def __init__(
        self,
        config: HomePilotConfig,
        registry: ProviderRegistry,
        prefill: dict | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._prefill: dict = prefill or {}

    def compose(self) -> ComposeResult:
        p = self._prefill
        host_options = [(k, k) for k in self._config.hosts]
        default_host = p.get("host") or next(iter(self._config.hosts), "")

        yield Header()
        yield VerticalScroll(
            Label(f"\n  Add New Resource\n", id="wizard-title"),
            Static("", id="wizard-status"),

            Label("  Target Host:", classes="section-header"),
            Select(host_options, value=default_host, id="host-select"),

            Label("  Basic Info", classes="section-header"),
            Label("  Resource Name (lowercase, dashes allowed):"),
            Input(value=p.get("app_name", ""), id="app-name", placeholder="my-app"),

            Label("  Source", classes="section-header"),
            Label("  Source Type:"),
            Select(
                [(t.value, t.value) for t in SourceType],
                value="local",
                id="source-type",
            ),
            Label("  Local Path:"),
            Input(id="source-path", placeholder="/path/to/project"),
            Label("  Git URL (if git source):"),
            Input(id="git-url", placeholder="https://github.com/user/repo.git"),
            Label("  Git Branch:"),
            Input(value="main", id="git-branch"),

            Label("  Build", classes="section-header"),
            Label("  Dockerfile:"),
            Input(value="Dockerfile", id="dockerfile"),
            Label("  Platform:"),
            Input(value="linux/amd64", id="platform"),

            Label("  Deployment", classes="section-header"),
            Label("  Image Name:"),
            Input(value=p.get("image_name", ""), id="image-name", placeholder="my-app"),
            Label("  Container Name:"),
            Input(value=p.get("container_name", ""), id="container-name", placeholder="my-app-container"),
            Label("  Host Port (0 = dynamic):"),
            Input(value="0", id="host-port"),
            Label("  Container Port:"),
            Input(value=p.get("container_port", "5000"), id="container-port"),
            Label("  Port Mode:"),
            Select(
                [(m.value, m.value) for m in PortMode],
                value="dynamic",
                id="port-mode",
            ),
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
            Label("  Endpoint:"),
            Input(value=p.get("health_endpoint", "/api/health"), id="health-endpoint"),

            Label("  Volumes", classes="section-header"),
            Label("  One host:container or host:container:mode per line:"),
            TextArea(id="volumes"),

            Label("  Environment Variables", classes="section-header"),
            Label("  One KEY=VALUE per line:"),
            TextArea(id="env-vars"),

            Static("", id="detect-status"),
            id="wizard-body",
        )
        yield Footer()

    def _get_selected_host(self) -> str:
        return str(self.query_one("#host-select", Select).value)

    def _is_truenas_host(self) -> bool:
        host_cfg = self._config.hosts.get(self._get_selected_host())
        return isinstance(host_cfg, TrueNASHostConfig)

    def action_auto_detect(self) -> None:
        status = self.query_one("#detect-status", Static)

        if not self._is_truenas_host():
            status.update("[yellow]Auto-detect is only available for TrueNAS Docker apps[/yellow]")
            return

        path_val = self.query_one("#source-path", Input).value.strip()
        if not path_val:
            status.update("[yellow]Enter a source path first[/yellow]")
            return

        src = Path(path_val).expanduser().resolve()
        if not src.exists():
            status.update(f"[red]Path not found: {src}[/red]")
            return

        messages: list[str] = []

        if (src / "Dockerfile").exists():
            messages.append("✅ Dockerfile found")
        else:
            messages.append("⚠️ No Dockerfile found")

        name = src.name.lower().replace("_", "-").replace(" ", "-")
        name_input = self.query_one("#app-name", Input)
        if not name_input.value:
            name_input.value = name
            messages.append(f"Name: {name}")

        image_input = self.query_one("#image-name", Input)
        if not image_input.value:
            image_input.value = name

        container_input = self.query_one("#container-name", Input)
        if not container_input.value:
            container_input.value = f"{name}-app"

        if (src / "package.json").exists():
            messages.append("Node.js project detected")

        for compose_name in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml"):
            if (src / compose_name).exists():
                messages.append(f"{compose_name} found")

        vol_text = self.query_one("#volumes", TextArea)
        if not vol_text.text:
            vol_text.load_text(f"/mnt/tank/apps/{name}/data:/app/data")

        status.update("[green]" + " │ ".join(messages) + "[/green]")

    def action_save(self) -> None:
        status = self.query_one("#wizard-status", Static)
        host_key = self._get_selected_host()

        name = self.query_one("#app-name", Input).value.strip()
        if not name:
            status.update("[red]Name is required[/red]")
            return
        if name in self._config.apps:
            status.update(f"[red]'{name}' already exists[/red]")
            return

        try:
            source_type = SourceType(self.query_one("#source-type", Select).value)
            port_mode = PortMode(self.query_one("#port-mode", Select).value)
            access_level = AccessLevel(self.query_one("#access-level", Select).value)
            network_mode = NetworkMode(self.query_one("#network-mode", Select).value)
            host_port = int(self.query_one("#host-port", Input).value or "0")
            public_host = self.query_one("#public-host", Input).value.strip()

            volumes: list[VolumeMount] = []
            for line in self.query_one("#volumes", TextArea).text.splitlines():
                line = line.strip()
                if ":" in line:
                    parts = line.split(":", 2)
                    host = parts[0].strip()
                    container = parts[1].strip() if len(parts) > 1 else ""
                    mode = parts[2].strip() if len(parts) > 2 else ""
                    if host and container:
                        volumes.append(VolumeMount(host=host, container=container, mode=mode))

            env: dict[str, str] = {}
            for line in self.query_one("#env-vars", TextArea).text.splitlines():
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

            app = AppConfig(
                name=name,
                host=host_key,
                source=SourceConfig(
                    type=source_type,
                    path=self.query_one("#source-path", Input).value.strip(),
                    git_url=self.query_one("#git-url", Input).value.strip(),
                    git_branch=self.query_one("#git-branch", Input).value.strip(),
                ),
                build=BuildConfig(
                    dockerfile=self.query_one("#dockerfile", Input).value.strip(),
                    platform=self.query_one("#platform", Input).value.strip(),
                ),
                deploy=DeployConfig(
                    image_name=self.query_one("#image-name", Input).value.strip(),
                    container_name=self.query_one("#container-name", Input).value.strip(),
                    host_port=host_port,
                    container_port=int(self.query_one("#container-port", Input).value or "5000"),
                    port_mode=port_mode,
                    access_level=access_level,
                    network_mode=network_mode,
                ),
                health=HealthConfig(
                    endpoint=self.query_one("#health-endpoint", Input).value.strip(),
                ),
                volumes=volumes,
                env=env,
                public_host=public_host,
            )

            # Record creation in history
            from datetime import datetime, timezone
            app.history.append(AppHistoryEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type=HistoryEventType.CREATED,
                message=f"Application created on host {host_key}",
            ))

            self._config.apps[name] = app

            errors = validate_config(self._config)
            if errors:
                del self._config.apps[name]
                status.update(f"[red]Validation errors: {'; '.join(errors)}[/red]")
                return

            save_config(self._config)
            status.update(f"[green]✅ '{name}' added to {host_key}[/green]")

        except Exception as exc:
            status.update(f"[red]Error: {exc}[/red]")

    def action_go_back(self) -> None:
        self.app.pop_screen()
