"""Dashboard screen — the main home view showing all resources across all hosts."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry
from homepilot.providers.base import HealthStatus, Resource, ResourceStatus, ResourceType


COLUMNS = ("Host", "Name", "Type", "Status", "Health", "Address", "Uptime", "Info", "Deploy")

_TYPE_LABELS: dict[ResourceType, str] = {
    ResourceType.DOCKER_CONTAINER: "Docker",
    ResourceType.LXC_CONTAINER: "LXC",
    ResourceType.VM: "VM",
    ResourceType.APP: "App",
}

SERVER_COLUMNS = ("Server", "Address", "Status", "Apps")


class DashboardScreen(Screen):
    """Main dashboard showing all resources across all connected hosts."""

    DEFAULT_CSS = """
    #top-panels {
        height: 11;
        width: 100%;
    }
    #overview-panel {
        width: 1fr;
        border: round $panel-darken-2;
        padding: 0 1;
        height: 100%;
    }
    #overview-title {
        text-style: bold;
        color: $text-muted;
    }
    #overview-stats {
        height: 1fr;
    }
    #servers-panel {
        width: 2fr;
        border: round $panel-darken-2;
        height: 100%;
    }
    #servers-title {
        text-style: bold;
        color: $text-muted;
        padding: 0 1;
    }
    #server-table {
        height: 1fr;
    }
    #resource-table {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("d", "deploy_selected", "Deploy", show=True, priority=True),
        Binding("c", "configure_selected", "Configure", show=True, priority=True),
        Binding("l", "logs_selected", "Logs", show=True, priority=True),
        Binding("s", "stop_start_selected", "Stop/Start", show=True, priority=True),
        Binding("i", "import_config", "Import Config", show=True, priority=True),
        Binding("a", "add_resource", "Add", show=True, priority=True),
        Binding("h", "manage_hosts", "Servers", show=True, priority=True),
        Binding("r", "refresh_status", "Refresh", show=True, priority=True),
        Binding("enter", "view_detail", "Detail", show=True, priority=True),
        Binding("q", "quit_app", "Quit", show=True, priority=True),
    ]

    def __init__(
        self, config: HomePilotConfig, registry: ProviderRegistry,
    ) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._resources: dict[str, Resource] = {}

    @staticmethod
    def _row_key(resource: Resource) -> str:
        return f"{resource.provider_name}:{resource.id}"

    # -- Compose & mount -----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Horizontal(
                # Left: summary stats
                Vertical(
                    Label(" Overview", id="overview-title"),
                    Static("", id="overview-stats"),
                    id="overview-panel",
                ),
                # Right: server list
                Vertical(
                    Label(" Servers", id="servers-title"),
                    DataTable(id="server-table", show_cursor=False),
                    id="servers-panel",
                ),
                id="top-panels",
            ),
            DataTable(id="resource-table"),
            id="dashboard-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        # Resource table
        table = self.query_one("#resource-table", DataTable)
        table.cursor_type = "row"
        for col in COLUMNS:
            table.add_column(col, key=col.lower())

        # Server table
        srv = self.query_one("#server-table", DataTable)
        srv.cursor_type = "none"
        for col in SERVER_COLUMNS:
            srv.add_column(col, key=col.lower())

        self._populate_config_apps()
        self._populate_server_panel()
        self._connect_and_refresh()

    # -- Initial population (config only, no network) ------------------------

    def _populate_config_apps(self) -> None:
        """Seed the resource table from config before providers connect."""
        table = self.query_one("#resource-table", DataTable)
        for name, app_cfg in self._config.apps.items():
            host_key = app_cfg.host or next(iter(self._config.hosts), "")
            port = str(app_cfg.deploy.host_port) if app_cfg.deploy.host_port else "—"
            info = f"{app_cfg.deploy.image_name}:latest" if app_cfg.deploy.image_name else ""
            table.add_row(
                host_key, name, "Docker", "—", "—", port, "—", info, "—",
                key=f"{host_key}:{name}",
            )

    def _populate_server_panel(self) -> None:
        """Seed the server table with configured hosts (before connecting)."""
        srv = self.query_one("#server-table", DataTable)
        srv.clear()
        for key, host in self._config.hosts.items():
            srv.add_row(key, host.host, "Connecting…", "—", key=key)
        self._update_overview(list(self._resources.values()))

    # -- Background connect + refresh ----------------------------------------

    @work(thread=True)
    def _connect_and_refresh(self) -> None:
        self._registry.connect_all()
        self._do_refresh()

    @work(thread=True)
    def _refresh_in_background(self) -> None:
        self._do_refresh()

    def _do_refresh(self) -> None:
        """Fetch resources from all providers (runs in background thread)."""
        resources = self._registry.list_all_resources()

        from homepilot.services.health import check_health_sync
        for r in resources:
            if r.resource_type == ResourceType.DOCKER_CONTAINER and r.port:
                try:
                    app_cfg = self._config.apps.get(r.name)
                    endpoint = app_cfg.health.endpoint if app_cfg else "/api/health"
                    result = check_health_sync(r.host, r.port, endpoint)
                    r.health = HealthStatus.HEALTHY if result == "Healthy" else HealthStatus.UNHEALTHY
                except Exception:
                    pass

        self.app.call_from_thread(self._rebuild_all, resources)

    def _rebuild_all(self, resources: list[Resource]) -> None:
        """Rebuild resource table, server panel and overview (main thread)."""
        self._rebuild_resource_table(resources)
        self._rebuild_server_panel(resources)
        self._update_overview(resources)

    # -- Individual panel updates --------------------------------------------

    def _rebuild_resource_table(self, resources: list[Resource]) -> None:
        table = self.query_one("#resource-table", DataTable)
        table.clear()
        self._resources.clear()

        for r in resources:
            rkey = self._row_key(r)
            self._resources[rkey] = r

            type_label = _TYPE_LABELS.get(r.resource_type, r.resource_type.value)
            status_icon = {
                ResourceStatus.RUNNING: "🟢",
                ResourceStatus.STOPPED: "🔴",
                ResourceStatus.ERROR: "🟡",
            }.get(r.status, "⚪")
            health_icon = {
                HealthStatus.HEALTHY: "💚",
                HealthStatus.UNHEALTHY: "💔",
            }.get(r.health, "")

            port_col = (
                f"{'https' if r.port == 443 else 'http'}://{r.host}:{r.port}"
                if r.port else "—"
            )
            health_col = (
                f"{health_icon} {r.health.value}" if r.health.value != "Unknown" else "—"
            )

            table.add_row(
                r.provider_name,
                r.name,
                type_label,
                f"{status_icon} {r.status.value}",
                health_col,
                port_col,
                r.uptime or "—",
                r.image or "",
                self._deploy_readiness(r),
                key=rkey,
            )

    def _rebuild_server_panel(self, resources: list[Resource]) -> None:
        """Update the server summary table with live status and app counts."""
        srv = self.query_one("#server-table", DataTable)
        srv.clear()

        # Count running resources per provider
        counts: dict[str, int] = {}
        for r in resources:
            counts[r.provider_name] = counts.get(r.provider_name, 0) + 1

        for key, host in self._config.hosts.items():
            provider = self._registry.get_provider(key)
            connected = provider is not None and provider.is_connected()
            status = "🟢 Online" if connected else "🔴 Offline"
            app_count = str(counts.get(key, 0))
            srv.add_row(key, host.host, status, app_count, key=key)

    def _update_overview(self, resources: list[Resource]) -> None:
        """Refresh the left-hand overview stats panel."""
        total = len(resources)
        running = sum(1 for r in resources if r.status == ResourceStatus.RUNNING)
        stopped = sum(1 for r in resources if r.status == ResourceStatus.STOPPED)
        healthy = sum(1 for r in resources if r.health == HealthStatus.HEALTHY)
        unhealthy = sum(1 for r in resources if r.health == HealthStatus.UNHEALTHY)
        hosts_total = len(self._config.hosts)
        hosts_online = sum(
            1 for key in self._config.hosts
            if (p := self._registry.get_provider(key)) and p.is_connected()
        )

        stats = self.query_one("#overview-stats", Static)
        stats.update(
            f" Servers:   {hosts_online}/{hosts_total}\n"
            f"\n"
            f" Resources: {total}\n"
            f" Running:   {running}  🟢\n"
            f" Stopped:   {stopped}  🔴\n"
            f" Healthy:   {healthy}  💚\n"
            f" Unhealthy: {unhealthy}  💔"
        )

    # ------------------------------------------------------------------
    # Row selection helpers
    # ------------------------------------------------------------------

    def _get_selected_resource(self) -> Resource | None:
        table = self.query_one("#resource-table", DataTable)
        if table.cursor_row is None:
            return None
        try:
            row_data = table.get_row_at(table.cursor_row)
            if not row_data:
                return None
            provider_name = str(row_data[0])
            resource_name = str(row_data[1])
            for rkey, r in self._resources.items():
                if r.provider_name == provider_name and r.name == resource_name:
                    return r
        except Exception:
            pass
        return None

    def _deploy_readiness(self, r: Resource) -> str:
        app_cfg = self._config.apps.get(r.name)
        if app_cfg is None:
            for cfg in self._config.apps.values():
                if cfg.deploy.container_name == r.name:
                    app_cfg = cfg
                    break
        if app_cfg is None:
            return "—"
        has_source = bool(app_cfg.source.path or app_cfg.source.git_url)
        has_image = bool(app_cfg.deploy.image_name)
        return "✅ Ready" if (has_source and has_image) else "⚙  Config"

    def _get_selected_app_name(self) -> str | None:
        r = self._get_selected_resource()
        if r is None:
            return None
        if r.name in self._config.apps:
            return r.name
        for app_key, app_cfg in self._config.apps.items():
            if app_cfg.deploy.container_name == r.name:
                return app_key
        return None

    # ------------------------------------------------------------------
    # Keybinding actions
    # ------------------------------------------------------------------

    def action_deploy_selected(self) -> None:
        name = self._get_selected_app_name()
        if name:
            from homepilot.screens.deploy import DeployScreen
            self.app.push_screen(DeployScreen(self._config, self._registry, name))

    def action_configure_selected(self) -> None:
        name = self._get_selected_app_name()
        if name:
            from homepilot.screens.config_editor import ConfigEditorScreen
            self.app.push_screen(ConfigEditorScreen(self._config, name))
        else:
            r = self._get_selected_resource()
            label = r.name if r else "this resource"
            self.notify(
                f"'{label}' is not a HomePilot-managed app.\nUse [bold]a[/bold] to add it first.",
                title="No Config",
                severity="warning",
                timeout=4,
            )

    def action_logs_selected(self) -> None:
        r = self._get_selected_resource()
        if r:
            from homepilot.screens.resource_detail import ResourceDetailScreen
            self.app.push_screen(
                ResourceDetailScreen(
                    self._config, self._registry, r.provider_name, r.id,
                    initial_tab="logs",
                )
            )

    def action_stop_start_selected(self) -> None:
        r = self._get_selected_resource()
        if r:
            self._run_stop_start(r)

    @work(thread=True)
    def _run_stop_start(self, r: Resource) -> None:
        provider = self._registry.get_provider(r.provider_name)
        if provider is None:
            return
        if r.status == ResourceStatus.RUNNING:
            self.app.call_from_thread(self.notify, f"Stopping {r.name}…", title="Stop", timeout=3)
            provider.stop(r.id)
        else:
            self.app.call_from_thread(self.notify, f"Starting {r.name}…", title="Start", timeout=3)
            provider.start(r.id)
        self._do_refresh()

    def action_import_config(self) -> None:
        r = self._get_selected_resource()
        if r is None:
            return
        if self._get_selected_app_name():
            self.notify(
                f"'{r.name}' is already in config. Use [bold]c[/bold] to edit it.",
                title="Already Managed",
                severity="warning",
                timeout=3,
            )
            return
        from homepilot.screens.import_config import ImportConfigScreen
        self.app.push_screen(ImportConfigScreen(self._config, self._registry, r))

    def action_add_resource(self) -> None:
        from homepilot.screens.add_resource import AddResourceScreen
        self.app.push_screen(AddResourceScreen(self._config, self._registry))

    def action_manage_hosts(self) -> None:
        from homepilot.screens.host_manager import HostManagerScreen
        self.app.push_screen(HostManagerScreen(self._config, self._registry))

    def action_refresh_status(self) -> None:
        self._refresh_in_background()

    def action_view_detail(self) -> None:
        r = self._get_selected_resource()
        if r:
            from homepilot.screens.resource_detail import ResourceDetailScreen
            self.app.push_screen(
                ResourceDetailScreen(self._config, self._registry, r.provider_name, r.id)
            )

    def action_quit_app(self) -> None:
        self.app.exit()
