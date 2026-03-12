"""App detail screen with tabbed views: Overview, Logs, Config, Actions."""

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

import yaml

from homepilot.config import config_to_dict
from homepilot.models import AppConfig, HomePilotConfig
from homepilot.widgets.log_viewer import LogViewer


class AppDetailScreen(Screen):
    """Detail view for a single application."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("d", "deploy_app", "Deploy", show=True),
    ]

    def __init__(
        self,
        config: HomePilotConfig,
        app_name: str,
        initial_tab: str = "overview",
    ) -> None:
        super().__init__()
        self._config = config
        self._app_name = app_name
        self._app_config = config.apps[app_name]
        self._initial_tab = initial_tab

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial=self._initial_tab):
            with TabPane("Overview", id="overview"):
                yield VerticalScroll(
                    Static(self._build_overview(), id="overview-content"),
                )
            with TabPane("Logs", id="logs"):
                yield LogViewer(id="log-viewer")
                yield Button("Refresh Logs", id="btn-refresh-logs", variant="default")
            with TabPane("Config", id="config"):
                yield VerticalScroll(
                    Static(self._build_config_view(), id="config-content"),
                )
            with TabPane("Actions", id="actions"):
                yield Vertical(
                    Label(f"\n  Actions for: {self._app_name}\n"),
                    Button("🚀 Deploy", id="btn-deploy", variant="success"),
                    Button("▶️  Start", id="btn-start", variant="primary"),
                    Button("⏹️  Stop", id="btn-stop", variant="warning"),
                    Button("🔄 Restart", id="btn-restart", variant="primary"),
                    Button("💾 Backup Data", id="btn-backup", variant="default"),
                    Button("🗑️  Remove Container", id="btn-remove", variant="error"),
                    id="actions-panel",
                )
        yield Footer()

    def on_mount(self) -> None:
        if self._initial_tab == "logs":
            self._load_logs()

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def _build_overview(self) -> str:
        app = self._app_config
        lines = [
            f"\n  App: {app.name}",
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

    # ------------------------------------------------------------------
    # Config view
    # ------------------------------------------------------------------

    def _build_config_view(self) -> str:
        """Render app config as YAML."""
        from homepilot.config import _app_to_dict
        data = _app_to_dict(self._app_config)
        return yaml.dump(data, default_flow_style=False, sort_keys=False)

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    @work(thread=True)
    def _load_logs(self) -> None:
        """Fetch container logs in a background thread."""
        try:
            from homepilot.services.ssh import SSHService
            from homepilot.services.truenas import TrueNASService

            ssh = SSHService(self._config.server)
            ssh.connect()
            truenas = TrueNASService(ssh, self._config.server)

            def on_line(line: str) -> None:
                self.app.call_from_thread(self._append_log, line)

            truenas.container_logs(
                self._app_config.deploy.container_name,
                lines=100,
                line_callback=on_line,
            )
            ssh.close()
        except Exception as exc:
            self.app.call_from_thread(
                self._append_log, f"[Error fetching logs: {exc}]"
            )

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
            self.action_deploy_app()
        elif btn_id == "btn-refresh-logs":
            self._load_logs()
        elif btn_id == "btn-start":
            self._run_action("start")
        elif btn_id == "btn-stop":
            self._run_action("stop")
        elif btn_id == "btn-restart":
            self._run_action("restart")
        elif btn_id == "btn-backup":
            self._run_action("backup")
        elif btn_id == "btn-remove":
            self._run_action("remove")

    @work(thread=True)
    def _run_action(self, action: str) -> None:
        """Execute a container action in a background thread."""
        try:
            from homepilot.services.ssh import SSHService
            from homepilot.services.truenas import TrueNASService

            ssh = SSHService(self._config.server)
            ssh.connect()
            truenas = TrueNASService(ssh, self._config.server)
            app = self._app_config

            if action == "start":
                truenas.app_start(app.name)
                self.app.call_from_thread(self._append_log, f"✅ {app.name} started")
            elif action == "stop":
                truenas.app_stop(app.name)
                self.app.call_from_thread(self._append_log, f"⏹️ {app.name} stopped")
            elif action == "restart":
                truenas.app_stop(app.name)
                truenas.app_start(app.name)
                self.app.call_from_thread(self._append_log, f"🔄 {app.name} restarted")
            elif action == "backup":
                if app.volumes:
                    result = truenas.backup_container_data(
                        app.deploy.container_name,
                        app.volumes[0].container,
                        self._config.server.backup_dir,
                    )
                    msg = f"💾 Backup: {result}" if result else "⚠️ Backup: no data found"
                    self.app.call_from_thread(self._append_log, msg)
            elif action == "remove":
                truenas.stop_container(app.deploy.container_name)
                truenas.remove_container(app.deploy.container_name)
                self.app.call_from_thread(
                    self._append_log, f"🗑️ Container {app.deploy.container_name} removed"
                )

            ssh.close()
        except Exception as exc:
            self.app.call_from_thread(self._append_log, f"❌ Error: {exc}")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_deploy_app(self) -> None:
        from homepilot.screens.deploy import DeployScreen
        self.app.push_screen(DeployScreen(self._config, self._app_name))
