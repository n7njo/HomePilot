"""Config editor screen — form-based editing of app configuration."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll, Horizontal
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
)

from homepilot.config import save_config, validate_config
from homepilot.models import HomePilotConfig, PortMode, SourceType


class ConfigEditorScreen(Screen):
    """Edit an app's configuration via a form."""

    BINDINGS = [
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

            # Volumes (first volume only for simplicity)
            Label("\n  Primary Volume:"),
            Label("  Host Path:"),
            Input(
                value=app.volumes[0].host if app.volumes else "",
                id="vol-host",
                placeholder="/mnt/tank/apps/...",
            ),
            Label("  Container Path:"),
            Input(
                value=app.volumes[0].container if app.volumes else "",
                id="vol-container",
                placeholder="/app/data",
            ),

            # Environment (display as key=value lines)
            Label("\n  Environment (one KEY=VALUE per line):"),
            Input(
                value="\n".join(f"{k}={v}" for k, v in app.env.items()),
                id="env-vars",
                placeholder="KEY=VALUE",
            ),

            # Buttons
            Static(""),
            Horizontal(
                Button("💾 Save", id="btn-save", variant="success"),
                Button("Cancel", id="btn-cancel", variant="default"),
                id="config-buttons",
            ),
            Static("", id="config-status"),
            id="config-form",
        )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self._save()
        elif event.button.id == "btn-cancel":
            self.app.pop_screen()

    def _save(self) -> None:
        """Collect form values, validate, and save."""
        app = self._app
        status = self.query_one("#config-status", Static)

        try:
            # Source
            source_type_val = self.query_one("#source-type", Select).value
            app.source.type = SourceType(source_type_val)
            app.source.path = self.query_one("#source-path", Input).value
            app.source.git_url = self.query_one("#git-url", Input).value
            app.source.git_branch = self.query_one("#git-branch", Input).value

            # Build
            app.build.dockerfile = self.query_one("#dockerfile", Input).value
            app.build.platform = self.query_one("#platform", Input).value

            # Deploy
            app.deploy.image_name = self.query_one("#image-name", Input).value
            app.deploy.container_name = self.query_one("#container-name", Input).value
            app.deploy.compose_file = self.query_one("#compose-file", Input).value
            app.deploy.host_port = int(self.query_one("#host-port", Input).value or "0")
            app.deploy.container_port = int(self.query_one("#container-port", Input).value or "5000")
            port_mode_val = self.query_one("#port-mode", Select).value
            app.deploy.port_mode = PortMode(port_mode_val)

            # Health
            app.health.endpoint = self.query_one("#health-endpoint", Input).value
            app.health.expected_status = int(self.query_one("#health-status", Input).value or "200")
            app.health.interval_seconds = int(self.query_one("#health-interval", Input).value or "30")

            # Volumes
            vol_host = self.query_one("#vol-host", Input).value
            vol_container = self.query_one("#vol-container", Input).value
            if vol_host and vol_container:
                from homepilot.models import VolumeMount
                app.volumes = [VolumeMount(host=vol_host, container=vol_container)]

            # Environment
            env_text = self.query_one("#env-vars", Input).value
            app.env = {}
            for line in env_text.split("\n"):
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    app.env[k.strip()] = v.strip()

            # Validate
            errors = validate_config(self._config)
            if errors:
                status.update(f"[red]Errors: {'; '.join(errors)}[/red]")
                return

            # Save
            save_config(self._config)
            status.update("[green]✅ Configuration saved successfully![/green]")

        except ValueError as exc:
            status.update(f"[red]Invalid value: {exc}[/red]")
        except Exception as exc:
            status.update(f"[red]Error: {exc}[/red]")

    def action_go_back(self) -> None:
        self.app.pop_screen()
