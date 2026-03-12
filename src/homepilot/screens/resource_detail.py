"""Resource detail screen with tabbed views: Overview, Logs, Actions."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry
from homepilot.providers.base import Resource, ResourceType
from homepilot.widgets.log_viewer import LogViewer


class ResourceDetailScreen(Screen):
    """Detail view for any infrastructure resource (Docker, VM, LXC)."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("d", "deploy_resource", "Deploy", show=True),
    ]

    def __init__(
        self,
        config: HomePilotConfig,
        registry: ProviderRegistry,
        provider_name: str,
        resource_id: str,
        initial_tab: str = "overview",
    ) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._provider_name = provider_name
        self._resource_id = resource_id
        self._initial_tab = initial_tab

        self._provider = registry.get_provider(provider_name)
        # Matching AppConfig (if this is a configured Docker app)
        self._app_config = config.apps.get(resource_id)

    def compose(self) -> ComposeResult:
        display_name = self._resource_id
        if self._app_config:
            display_name = self._app_config.name

        yield Header()
        with TabbedContent(initial=self._initial_tab):
            with TabPane("Overview", id="overview"):
                yield VerticalScroll(
                    Static(self._build_overview(), id="overview-content"),
                )
            with TabPane("Logs", id="logs"):
                yield LogViewer(id="log-viewer")
                yield Button("Refresh Logs", id="btn-refresh-logs", variant="default")
            with TabPane("Actions", id="actions"):
                yield self._build_actions_panel(display_name)
        yield Footer()

    def on_mount(self) -> None:
        if self._initial_tab == "logs":
            self._load_logs()

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def _build_overview(self) -> str:
        # If this is a configured Docker app, show rich config info
        if self._app_config:
            return self._build_app_overview()

        # Otherwise show provider-sourced resource info
        if self._provider:
            resource = self._provider.get_resource(self._resource_id)
            if resource:
                return self._build_resource_overview(resource)

        return f"\n  Resource: {self._resource_id}\n  Provider: {self._provider_name}\n  (no details available)"

    def _build_app_overview(self) -> str:
        """Rich overview for a configured Docker app (TrueNAS)."""
        app = self._app_config
        assert app is not None
        lines = [
            f"\n  App: {app.name}",
            f"  Host: {self._provider_name}",
            f"  Source: {app.source.type.value} — {app.source.path or app.source.git_url}",
            f"  Dockerfile: {app.build.dockerfile}",
            f"  Platform: {app.build.platform}",
            "",
            f"  Image: {app.deploy.image_name}:latest",
            f"  Container: {app.deploy.container_name}",
            f"  Port: {app.deploy.host_port} → {app.deploy.container_port} ({app.deploy.port_mode.value})",
            f"  Compose: {app.deploy.compose_file or '(auto)'}",
            "",
            f"  Health endpoint: {app.health.endpoint}",
            f"  Health interval: {app.health.interval_seconds}s",
            "",
            "  Volumes:",
        ]
        for v in app.volumes:
            lines.append(f"    {v.host} → {v.container}")
        if not app.volumes:
            lines.append("    (none)")

        lines.append("")
        lines.append("  Environment:")
        for k, v in app.env.items():
            lines.append(f"    {k}={v}")
        if not app.env:
            lines.append("    (none)")

        return "\n".join(lines)

    @staticmethod
    def _build_resource_overview(resource: Resource) -> str:
        """Overview for a Proxmox VM/LXC or other non-app resource."""
        meta = resource.metadata
        lines = [
            f"\n  Name: {resource.name}",
            f"  Type: {resource.resource_type.value}",
            f"  Provider: {resource.provider_name}",
            f"  Host: {resource.host}",
            f"  Status: {resource.status.value}",
            f"  Uptime: {resource.uptime or '—'}",
        ]
        if resource.resource_type in (ResourceType.VM, ResourceType.LXC_CONTAINER):
            lines.extend([
                "",
                f"  Node: {meta.get('node', '—')}",
                f"  VMID: {meta.get('vmid', '—')}",
                f"  Max CPU: {meta.get('maxcpu', '—')}",
                f"  Max Memory: {_format_bytes(meta.get('maxmem', 0))}",
                f"  Template: {'Yes' if meta.get('template') else 'No'}",
            ])
        if resource.image:
            lines.append(f"  Image: {resource.image}")
        if resource.port:
            lines.append(f"  Port: {resource.port}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Actions panel
    # ------------------------------------------------------------------

    def _build_actions_panel(self, display_name: str) -> Vertical:
        """Build the actions panel with provider-appropriate buttons."""
        buttons = [
            Label(f"\n  Actions for: {display_name}\n"),
            Button("▶️  Start", id="btn-start", variant="primary"),
            Button("⏹️  Stop", id="btn-stop", variant="warning"),
            Button("🔄 Restart", id="btn-restart", variant="primary"),
        ]

        # Docker-app-specific actions
        if self._app_config:
            buttons.insert(1, Button("🚀 Deploy", id="btn-deploy", variant="success"))
            buttons.append(Button("💾 Backup Data", id="btn-backup", variant="default"))
            buttons.append(Button("🗑️  Remove Container", id="btn-remove", variant="error"))

        return Vertical(*buttons, id="actions-panel")

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    @work(thread=True)
    def _load_logs(self) -> None:
        """Fetch logs via the provider in a background thread."""
        if not self._provider:
            self.app.call_from_thread(self._append_log, "[No provider available]")
            return

        try:
            output = self._provider.logs(self._resource_id)
            for line in output.splitlines():
                self.app.call_from_thread(self._append_log, line)
        except Exception as exc:
            self.app.call_from_thread(self._append_log, f"[Error fetching logs: {exc}]")

    def _append_log(self, line: str) -> None:
        try:
            viewer = self.query_one("#log-viewer", LogViewer)
            viewer.append_line(line)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-deploy":
            self.action_deploy_resource()
        elif btn_id == "btn-refresh-logs":
            self._load_logs()
        elif btn_id == "btn-start":
            self._run_provider_action("start")
        elif btn_id == "btn-stop":
            self._run_provider_action("stop")
        elif btn_id == "btn-restart":
            self._run_provider_action("restart")
        elif btn_id == "btn-backup":
            self._run_truenas_action("backup")
        elif btn_id == "btn-remove":
            self._run_provider_action("remove")

    @work(thread=True)
    def _run_provider_action(self, action: str) -> None:
        """Execute start/stop/restart/remove via the provider."""
        if not self._provider:
            self.app.call_from_thread(self._append_log, "❌ No provider available")
            return

        icons = {"start": "▶️", "stop": "⏹️", "restart": "🔄", "remove": "🗑️"}
        try:
            method = getattr(self._provider, action)
            success = method(self._resource_id)
            if success:
                self.app.call_from_thread(
                    self._append_log,
                    f"{icons.get(action, '✅')} {action.capitalize()} succeeded",
                )
            else:
                self.app.call_from_thread(
                    self._append_log, f"⚠️ {action.capitalize()} returned false"
                )
        except Exception as exc:
            self.app.call_from_thread(self._append_log, f"❌ Error: {exc}")

    @work(thread=True)
    def _run_truenas_action(self, action: str) -> None:
        """TrueNAS-specific actions (backup) using the underlying services."""
        if action != "backup" or not self._app_config:
            return

        try:
            from homepilot.providers.truenas import TrueNASProvider

            if not isinstance(self._provider, TrueNASProvider):
                self.app.call_from_thread(self._append_log, "⚠️ Backup only available for TrueNAS hosts")
                return

            truenas = self._provider.truenas
            if not truenas:
                self.app.call_from_thread(self._append_log, "❌ TrueNAS service not connected")
                return

            app = self._app_config
            host_cfg = self._config.hosts.get(self._provider_name)
            backup_dir = getattr(host_cfg, "backup_dir", "/tmp/homepilot-backups")

            if app.volumes:
                result = truenas.backup_container_data(
                    app.deploy.container_name,
                    app.volumes[0].container,
                    backup_dir,
                )
                msg = f"💾 Backup: {result}" if result else "⚠️ Backup: no data found"
                self.app.call_from_thread(self._append_log, msg)
            else:
                self.app.call_from_thread(self._append_log, "⚠️ No volumes configured")
        except Exception as exc:
            self.app.call_from_thread(self._append_log, f"❌ Error: {exc}")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_deploy_resource(self) -> None:
        if self._app_config:
            from homepilot.screens.deploy import DeployScreen
            self.app.push_screen(
                DeployScreen(self._config, self._registry, self._app_config.name)
            )


# -- Helpers ---------------------------------------------------------------

def _format_bytes(n: int) -> str:
    """Format bytes as human-readable (e.g. 4.0 GB)."""
    if n <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"
