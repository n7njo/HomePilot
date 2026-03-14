"""Dashboard screen — the main home view showing all resources across all hosts."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry
from homepilot.providers.base import HealthStatus, Resource, ResourceStatus, ResourceType


COLUMNS = ("Host", "Name", "Type", "Status", "Health", "Address", "Uptime", "Info", "Deploy")

# Display-friendly type names
_TYPE_LABELS: dict[ResourceType, str] = {
    ResourceType.DOCKER_CONTAINER: "Docker",
    ResourceType.LXC_CONTAINER: "LXC",
    ResourceType.VM: "VM",
    ResourceType.APP: "App",
}


class DashboardScreen(Screen):
    """Main dashboard showing all resources across all connected hosts."""

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
        # Cached resources indexed by row key  (provider_name:resource_id)
        self._resources: dict[str, Resource] = {}

    @staticmethod
    def _row_key(resource: Resource) -> str:
        return f"{resource.provider_name}:{resource.id}"

    # -- Compose & mount -----------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Label(" HomePilot — Home Lab Manager", id="dashboard-title"),
            Static(" Loading…", id="dashboard-subtitle"),
            DataTable(id="resource-table"),
            id="dashboard-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#resource-table", DataTable)
        table.cursor_type = "row"
        for col in COLUMNS:
            table.add_column(col, key=col.lower())

        # Populate with config-only apps initially, then connect in background
        self._populate_config_apps()
        self._connect_and_refresh()

    # -- Initial population from config (no network needed) ------------------

    def _populate_config_apps(self) -> None:
        """Seed the table with configured Docker apps (before providers connect)."""
        table = self.query_one("#resource-table", DataTable)
        for name, app_cfg in self._config.apps.items():
            host_key = app_cfg.host or next(iter(self._config.hosts), "")
            host_cfg = self._config.hosts.get(host_key)
            host_display = host_key
            if host_cfg:
                host_display = host_cfg.host

            rkey = f"{host_key}:{name}"
            port = str(app_cfg.deploy.host_port) if app_cfg.deploy.host_port else "—"
            info = f"{app_cfg.deploy.image_name}:latest" if app_cfg.deploy.image_name else ""

            table.add_row(
                host_key, name, "Docker", "Unknown", "Unknown", port, "—", info,
                key=rkey,
            )

    # -- Background connect + refresh ----------------------------------------

    @work(thread=True)
    def _connect_and_refresh(self) -> None:
        """Connect providers and refresh the resource table."""
        self._registry.connect_all()
        self.app.call_from_thread(self._update_subtitle)
        self._do_refresh()

    @work(thread=True)
    def _refresh_in_background(self) -> None:
        """Refresh resource list without reconnecting."""
        self._do_refresh()

    def _do_refresh(self) -> None:
        """Fetch resources from all providers and update the table (runs in thread)."""
        resources = self._registry.list_all_resources()

        # Also run health checks for TrueNAS Docker apps that have a port
        from homepilot.services.health import check_health_sync

        for r in resources:
            if r.resource_type == ResourceType.DOCKER_CONTAINER and r.port:
                try:
                    # Find matching app config for health endpoint
                    app_cfg = self._config.apps.get(r.name)
                    endpoint = app_cfg.health.endpoint if app_cfg else "/api/health"
                    result = check_health_sync(r.host, r.port, endpoint)
                    if result == "Healthy":
                        r.health = HealthStatus.HEALTHY
                    else:
                        r.health = HealthStatus.UNHEALTHY
                except Exception:
                    pass

        self.app.call_from_thread(self._rebuild_table, resources)

    def _rebuild_table(self, resources: list[Resource]) -> None:
        """Rebuild the table with fresh resource data (runs on main thread)."""
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

            if r.port:
                scheme = "https" if r.port == 443 else "http"
                port_col = f"{scheme}://{r.host}:{r.port}"
            else:
                port_col = "—"
            uptime_col = r.uptime or "—"
            health_col = f"{health_icon} {r.health.value}" if r.health.value != "Unknown" else "—"
            info_col = r.image or ""
            deploy_col = self._deploy_readiness(r)

            table.add_row(
                r.provider_name,
                r.name,
                type_label,
                f"{status_icon} {r.status.value}",
                health_col,
                port_col,
                uptime_col,
                info_col,
                deploy_col,
                key=rkey,
            )

        self._update_subtitle()

    def _update_subtitle(self) -> None:
        """Refresh the subtitle with host connection info."""
        hosts = self._registry.connected_hosts_display()
        count = len(self._resources)
        subtitle = self.query_one("#dashboard-subtitle", Static)
        subtitle.update(f" {hosts}  │  Resources: {count}")

    # ------------------------------------------------------------------
    # Row selection helpers
    # ------------------------------------------------------------------

    def _get_selected_resource(self) -> Resource | None:
        """Return the Resource for the currently highlighted row."""
        table = self.query_one("#resource-table", DataTable)
        if table.cursor_row is None:
            return None
        try:
            row_data = table.get_row_at(table.cursor_row)
            if not row_data:
                return None
            # Reconstruct the row key from provider_name + name columns
            provider_name = str(row_data[0])
            resource_name = str(row_data[1])
            # Search for matching resource
            for rkey, r in self._resources.items():
                if r.provider_name == provider_name and r.name == resource_name:
                    return r
        except Exception:
            pass
        return None

    def _deploy_readiness(self, r: Resource) -> str:
        """Return a deploy-readiness indicator for a resource.

        ✅ Ready     — in config with source + image (full redeploy possible)
        ⚙  Config   — in config but missing source (can manage, can't rebuild)
        —            — not in HomePilot config
        """
        app_cfg = None
        if r.name in self._config.apps:
            app_cfg = self._config.apps[r.name]
        else:
            for cfg in self._config.apps.values():
                if cfg.deploy.container_name == r.name:
                    app_cfg = cfg
                    break

        if app_cfg is None:
            return "—"

        has_source = bool(app_cfg.source.path or app_cfg.source.git_url)
        has_image = bool(app_cfg.deploy.image_name)
        if has_source and has_image:
            return "✅ Ready"
        return "⚙  Config"

    def _get_selected_app_name(self) -> str | None:
        """If the selected resource is a configured app, return the config app key."""
        r = self._get_selected_resource()
        if r is None:
            return None
        # Direct key match (app key == container name)
        if r.name in self._config.apps:
            return r.name
        # Match by deploy.container_name (e.g. resource "house-tracker-app" → app key "house-tracker")
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
                ResourceDetailScreen(self._config, self._registry, r.provider_name, r.id, initial_tab="logs")
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
            self.app.call_from_thread(
                self.notify, f"Stopping {r.name}…", title="Stop", timeout=3
            )
            provider.stop(r.id)
        else:
            self.app.call_from_thread(
                self.notify, f"Starting {r.name}…", title="Start", timeout=3
            )
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
