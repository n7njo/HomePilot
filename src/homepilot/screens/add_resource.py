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
)

from homepilot.config import save_config, validate_config
from homepilot.models import (
    AppConfig,
    BuildConfig,
    DeployConfig,
    HomePilotConfig,
    HealthConfig,
    PortMode,
    SourceConfig,
    SourceType,
    TrueNASHostConfig,
    VolumeMount,
)
from homepilot.providers import ProviderRegistry


class AddResourceScreen(Screen):
    """Wizard to register a new application or resource."""

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
            Label("\n  Add New Resource\n", id="wizard-title"),
            Static("", id="wizard-status"),

            Label("  Target Host:"),
            Select(host_options, value=default_host, id="host-select"),

            Label("\n  Resource Name (lowercase, dashes allowed):"),
            Input(value=p.get("app_name", ""), id="app-name", placeholder="my-app"),

            Label("\n  Source:"),
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

            Label("\n  Build:"),
            Label("  Dockerfile:"),
            Input(value="Dockerfile", id="dockerfile"),
            Label("  Platform:"),
            Input(value="linux/amd64", id="platform"),

            Label("\n  Deployment:"),
            Label("  Image Name:"),
            Input(value=p.get("image_name", ""), id="image-name", placeholder="my-app"),
            Label("  Container Name:"),
            Input(value=p.get("container_name", ""), id="container-name", placeholder="my-app-container"),
            Label("  Host Port (0 = dynamic):"),
            Input(value="0", id="host-port"),
            Label("  Container Port:"),
            Input(value="5000", id="container-port"),
            Label("  Port Mode:"),
            Select(
                [(m.value, m.value) for m in PortMode],
                value="dynamic",
                id="port-mode",
            ),

            Label("\n  Health Check:"),
            Label("  Endpoint:"),
            Input(value="/api/health", id="health-endpoint"),

            Label("\n  Volume Mount (optional):"),
            Label("  Host Path:"),
            Input(id="vol-host", placeholder="/mnt/tank/apps/my-app/data"),
            Label("  Container Path:"),
            Input(value="/app/data", id="vol-container"),

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

        vol_host = self.query_one("#vol-host", Input)
        if not vol_host.value:
            vol_host.value = f"/mnt/tank/apps/{name}/data"

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
            host_port = int(self.query_one("#host-port", Input).value or "0")

            volumes: list[VolumeMount] = []
            vol_host = self.query_one("#vol-host", Input).value.strip()
            vol_container = self.query_one("#vol-container", Input).value.strip()
            if vol_host and vol_container:
                volumes.append(VolumeMount(host=vol_host, container=vol_container))

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
                ),
                health=HealthConfig(
                    endpoint=self.query_one("#health-endpoint", Input).value.strip(),
                ),
                volumes=volumes,
            )

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
