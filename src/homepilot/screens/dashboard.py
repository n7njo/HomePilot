"""Dashboard screen — the main home view showing all registered apps."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from homepilot.models import (
    AppConfig,
    AppRuntimeInfo,
    AppStatus,
    HomePilotConfig,
    HealthStatus,
)


COLUMNS = ("App", "Status", "Health", "Image", "Port", "Last Deployed", "URL")


class DashboardScreen(Screen):
    """Main dashboard showing all deployed apps."""

    BINDINGS = [
        Binding("d", "deploy_selected", "Deploy", show=True, priority=True),
        Binding("c", "configure_selected", "Configure", show=True, priority=True),
        Binding("l", "logs_selected", "Logs", show=True, priority=True),
        Binding("a", "add_app", "Add App", show=True, priority=True),
        Binding("r", "refresh_status", "Refresh", show=True, priority=True),
        Binding("t", "toggle_theme", "Theme", show=True, priority=True),
        Binding("enter", "view_detail", "Detail", show=True, priority=True),
        Binding("q", "quit_app", "Quit", show=True, priority=True),
    ]

    def __init__(self, config: HomePilotConfig) -> None:
        super().__init__()
        self._config = config
        self._runtime_info: dict[str, AppRuntimeInfo] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Label(
                " HomePilot — Home Lab Manager",
                id="dashboard-title",
            ),
            Static(
                f" Server: {self._config.server.user}@{self._config.server.host}  │  "
                f"Apps: {len(self._config.apps)}",
                id="dashboard-subtitle",
            ),
            DataTable(id="app-table"),
            id="dashboard-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#app-table", DataTable)
        table.cursor_type = "row"

        for col in COLUMNS:
            table.add_column(col, key=col.lower())

        self._populate_table()
        self._refresh_health()

    @staticmethod
    def _format_last_deployed(iso_str: str) -> str:
        """Format an ISO-8601 timestamp for display, or 'Never'."""
        if not iso_str:
            return "Never"
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(iso_str)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return iso_str

    def _populate_table(self) -> None:
        """Fill the table with configured apps (initial state)."""
        table = self.query_one("#app-table", DataTable)
        table.clear()

        for name, app in self._config.apps.items():
            info = self._runtime_info.get(name)
            status = info.status.value if info else "Unknown"
            health = info.health.value if info else "Unknown"
            image = f"{app.deploy.image_name}:latest"
            port = str(app.deploy.host_port) if app.deploy.host_port else "—"
            last_dep = self._format_last_deployed(app.last_deployed)
            url = (
                f"http://{self._config.server.host}:{app.deploy.host_port}"
                if app.deploy.host_port
                else "—"
            )
            table.add_row(name, status, health, image, port, last_dep, url, key=name)

    @work(thread=True)
    def _refresh_health(self) -> None:
        """Check health of all apps in a background thread."""
        from homepilot.services.health import check_health_sync

        for name, app in self._config.apps.items():
            if not app.deploy.host_port:
                continue

            health = check_health_sync(
                self._config.server.host,
                app.deploy.host_port,
                app.health.endpoint,
            )

            info = self._runtime_info.get(name)
            if info is None:
                info = AppRuntimeInfo(
                    name=name,
                    host_port=app.deploy.host_port,
                    image_tag=f"{app.deploy.image_name}:latest",
                )
                self._runtime_info[name] = info

            info.health = HealthStatus.HEALTHY if health == "Healthy" else HealthStatus.UNHEALTHY

            # Try to get container status via SSH.
            try:
                from homepilot.services.ssh import SSHService
                from homepilot.services.truenas import TrueNASService

                ssh = SSHService(self._config.server)
                ssh.connect()
                truenas = TrueNASService(ssh, self._config.server)
                cs = truenas.container_status(app.deploy.container_name)
                if cs == "running":
                    info.status = AppStatus.RUNNING
                elif cs == "not found":
                    info.status = AppStatus.UNKNOWN
                else:
                    info.status = AppStatus.STOPPED
                ssh.close()
            except Exception:
                pass

            # Update the table row.
            self.app.call_from_thread(self._update_row, name, info)

    def _update_row(self, name: str, info: AppRuntimeInfo) -> None:
        """Update a single row in the data table."""
        table = self.query_one("#app-table", DataTable)
        try:
            url = (
                f"http://{self._config.server.host}:{info.host_port}"
                if info.host_port
                else "—"
            )
            app = self._config.apps.get(name)
            last_dep = self._format_last_deployed(app.last_deployed) if app else "—"
            table.update_cell(name, "status", info.status.value)
            table.update_cell(name, "health", info.health.value)
            table.update_cell(name, "port", str(info.host_port) if info.host_port else "—")
            table.update_cell(name, "last deployed", last_dep)
            table.update_cell(name, "url", url)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Keybinding actions
    # ------------------------------------------------------------------

    def _get_selected_app(self) -> str | None:
        table = self.query_one("#app-table", DataTable)
        if table.cursor_row is not None:
            row_key = table.get_row_at(table.cursor_row)
            if row_key:
                # First cell is the app name.
                return str(row_key[0])
        return None

    def action_deploy_selected(self) -> None:
        name = self._get_selected_app()
        if name and name in self._config.apps:
            from homepilot.screens.deploy import DeployScreen
            self.app.push_screen(DeployScreen(self._config, name))

    def action_configure_selected(self) -> None:
        name = self._get_selected_app()
        if name and name in self._config.apps:
            from homepilot.screens.config_editor import ConfigEditorScreen
            self.app.push_screen(ConfigEditorScreen(self._config, name))

    def action_logs_selected(self) -> None:
        name = self._get_selected_app()
        if name and name in self._config.apps:
            from homepilot.screens.app_detail import AppDetailScreen
            self.app.push_screen(AppDetailScreen(self._config, name, initial_tab="logs"))

    def action_add_app(self) -> None:
        from homepilot.screens.add_app import AddAppScreen
        self.app.push_screen(AddAppScreen(self._config))

    def action_refresh_status(self) -> None:
        self._refresh_health()

    def action_view_detail(self) -> None:
        name = self._get_selected_app()
        if name and name in self._config.apps:
            from homepilot.screens.app_detail import AppDetailScreen
            self.app.push_screen(AppDetailScreen(self._config, name))

    def action_toggle_theme(self) -> None:
        self.app.action_toggle_theme()

    def action_quit_app(self) -> None:
        self.app.exit()
