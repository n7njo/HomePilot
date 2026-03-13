"""Config editor screen — form-based editing of app configuration."""

from __future__ import annotations

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
from homepilot.models import HomePilotConfig, PortMode, SourceType


class ConfigEditorScreen(Screen):
    """Edit an app's configuration via a form."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=True),
        Binding("escape", "go_back", "Back", show=True),
    ]

    def __init__(self, config: HomePilotConfig, app_name: str) -> None:
        super().__init__()
        self._config = config
        self._app_name = app_name
        self._app = config.apps[app_name]

    def compose(self) -> ComposeResult:
        app = self._app
        yield Header()
        yield VerticalScroll(
            Label(f"\n  Configure: {self._app_name}\n", id="config-title"),
            Static("", id="config-status"),

            # Source
            Label("  Source Type:"),
            Select(
                [(t.value, t.value) for t in SourceType],
                value=app.source.type.value,
                id="source-type",
            ),
            Label("  Source Path (local):"),
            Input(value=app.source.path, id="source-path", placeholder="/path/to/project"),
            Label("  Git URL:"),
            Input(value=app.source.git_url, id="git-url", placeholder="https://github.com/..."),
            Label("  Git Branch:"),
            Input(value=app.source.git_branch, id="git-branch", placeholder="main"),

            # Build
            Label("\n  Build Settings:"),
            Label("  Dockerfile:"),
            Input(value=app.build.dockerfile, id="dockerfile", placeholder="Dockerfile"),
            Label("  Platform:"),
            Input(value=app.build.platform, id="platform", placeholder="linux/amd64"),

            # Deploy
            Label("\n  Deployment:"),
            Label("  Image Name:"),
            Input(value=app.deploy.image_name, id="image-name"),
            Label("  Container Name:"),
            Input(value=app.deploy.container_name, id="container-name"),
            Label("  Compose File:"),
            Input(value=app.deploy.compose_file, id="compose-file", placeholder="truenas-app.yaml"),
            Label("  Host Port:"),
            Input(value=str(app.deploy.host_port), id="host-port"),
            Label("  Container Port:"),
            Input(value=str(app.deploy.container_port), id="container-port"),
            Label("  Port Mode:"),
            Select(
                [(m.value, m.value) for m in PortMode],
                value=app.deploy.port_mode.value,
                id="port-mode",
            ),

            # Health
            Label("\n  Health Check:"),
            Label("  Endpoint:"),
            Input(value=app.health.endpoint, id="health-endpoint", placeholder="/api/health"),
            Label("  Expected Status:"),
            Input(value=str(app.health.expected_status), id="health-status"),
            Label("  Check Interval (s):"),
            Input(value=str(app.health.interval_seconds), id="health-interval"),

            # Volumes
            Label("\n  Volumes (one host:container or host:container:mode per line):"),
            TextArea(
                "\n".join(
                    f"{v.host}:{v.container}" + (f":{v.mode}" if v.mode else "")
                    for v in app.volumes
                ),
                id="volumes",
            ),

            # Environment
            Label("\n  Environment (one KEY=VALUE per line):"),
            TextArea(
                "\n".join(f"{k}={v}" for k, v in app.env.items()),
                id="env-vars",
            ),

            id="config-form",
        )
        yield Footer()

    def action_save(self) -> None:
        app = self._app
        status = self.query_one("#config-status", Static)

        try:
            source_type_val = self.query_one("#source-type", Select).value
            app.source.type = SourceType(source_type_val)
            app.source.path = self.query_one("#source-path", Input).value
            app.source.git_url = self.query_one("#git-url", Input).value
            app.source.git_branch = self.query_one("#git-branch", Input).value

            app.build.dockerfile = self.query_one("#dockerfile", Input).value
            app.build.platform = self.query_one("#platform", Input).value

            app.deploy.image_name = self.query_one("#image-name", Input).value
            app.deploy.container_name = self.query_one("#container-name", Input).value
            app.deploy.compose_file = self.query_one("#compose-file", Input).value
            app.deploy.host_port = int(self.query_one("#host-port", Input).value or "0")
            app.deploy.container_port = int(self.query_one("#container-port", Input).value or "5000")
            port_mode_val = self.query_one("#port-mode", Select).value
            app.deploy.port_mode = PortMode(port_mode_val)

            app.health.endpoint = self.query_one("#health-endpoint", Input).value
            app.health.expected_status = int(self.query_one("#health-status", Input).value or "200")
            app.health.interval_seconds = int(self.query_one("#health-interval", Input).value or "30")

            from homepilot.models import VolumeMount
            volumes = []
            for line in self.query_one("#volumes", TextArea).text.splitlines():
                line = line.strip()
                if ":" in line:
                    parts = line.split(":", 2)
                    host = parts[0].strip()
                    container = parts[1].strip() if len(parts) > 1 else ""
                    mode = parts[2].strip() if len(parts) > 2 else ""
                    if host and container:
                        volumes.append(VolumeMount(host=host, container=container, mode=mode))
            app.volumes = volumes

            env_text = self.query_one("#env-vars", TextArea).text
            app.env = {}
            for line in env_text.split("\n"):
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    app.env[k.strip()] = v.strip()

            errors = validate_config(self._config)
            if errors:
                status.update(f"[red]Errors: {'; '.join(errors)}[/red]")
                return

            save_config(self._config)
            status.update("[green]✅ Saved[/green]")

        except ValueError as exc:
            status.update(f"[red]Invalid value: {exc}[/red]")
        except Exception as exc:
            status.update(f"[red]Error: {exc}[/red]")

    def action_go_back(self) -> None:
        self.app.pop_screen()
