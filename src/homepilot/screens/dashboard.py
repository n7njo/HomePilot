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

SERVER_COLUMNS = ("Server", "Address", "Status", "Ready", "Apps", "CPU", "RAM", "Disk")


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
        Binding("s", "stop_start_selected", "Stop/Start", show=True, priority=True),
        Binding("i", "import_config", "Import Config", show=True, priority=True),
        Binding("a", "add_resource", "Add", show=True, priority=True),
        Binding("n", "registry_deploy", "Registry", show=True, priority=True),
        Binding("x", "delete_app", "Delete", show=True, priority=True),
        Binding("m", "migrate_selected", "Migrate", show=True, priority=True),
        Binding("h", "manage_hosts", "Servers", show=True, priority=True),
        Binding("r", "refresh_status", "Refresh", show=True, priority=True),
        Binding("enter", "view_detail", "Detail", show=True, priority=True),
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
        table.focus()

        # Server table
        srv = self.query_one("#server-table", DataTable)
        srv.cursor_type = "none"
        for col in SERVER_COLUMNS:
            srv.add_column(col, key=col.lower())

        self._populate_config_apps()
        self._populate_server_panel()
        self._connect_and_refresh()
        self.set_interval(5, self._refresh_in_background)
        self.set_interval(2, self._refresh_metrics_in_background)

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
        from homepilot.models import TrueNASHostConfig
        srv = self.query_one("#server-table", DataTable)
        srv.clear()
        for key, host in self._config.hosts.items():
            ready = "✅ Built-in" if isinstance(host, TrueNASHostConfig) else "Checking…"
            srv.add_row(key, host.host, "Connecting…", ready, "—", key=key)
        self._update_overview(list(self._resources.values()))

    # -- Background connect + refresh ----------------------------------------

    @work(thread=True)
    def _connect_and_refresh(self) -> None:
        self._registry.connect_all()
        self._do_refresh()
        self._do_bootstrap_checks()

    @work(thread=True)
    def _refresh_in_background(self) -> None:
        self._do_refresh()

    @work(thread=True)
    def _refresh_metrics_in_background(self) -> None:
        self._do_metrics_refresh()

    def _do_metrics_refresh(self) -> None:
        """Fetch host metrics from all providers (runs in background thread)."""
        for provider in self._registry.providers.values():
            try:
                provider.get_metrics() # This updates the internal .last_metrics
            except Exception:
                pass
        
        # Re-render server panel with latest cached metrics
        self.app.call_from_thread(
            self._rebuild_server_panel, list(self._resources.values())
        )

    def _do_bootstrap_checks(self) -> None:
        """Check bootstrap status for each host (runs in background thread)."""
        from homepilot.providers.proxmox import ProxmoxProvider
        from homepilot.providers.truenas import TrueNASProvider
        for provider in self._registry.providers.values():
            if isinstance(provider, (ProxmoxProvider, TrueNASProvider)):
                provider.check_bootstrap()
        # Re-render server panel with updated statuses
        self.app.call_from_thread(
            self._rebuild_server_panel, list(self._resources.values())
        )

    def _do_refresh(self) -> None:
        """Fetch resources from all providers (runs in background thread)."""
        resources = self._registry.list_all_resources()

        from homepilot.services.health import check_health_sync, check_tcp_health_async
        import asyncio
        from homepilot.models import HealthProtocol

        for r in resources:
            if r.resource_type == ResourceType.DOCKER_CONTAINER and r.port:
                try:
                    app_cfg = self._config.apps.get(r.name)
                    if app_cfg is None:
                        for cfg in self._config.apps.values():
                            if cfg.deploy.container_name == r.name:
                                app_cfg = cfg
                                break
                    
                    protocol = app_cfg.health.protocol if app_cfg else HealthProtocol.HTTP
                    
                    if protocol == HealthProtocol.TCP:
                        # Raw TCP connect test
                        try:
                            status, _ = asyncio.run(check_tcp_health_async(r.host, r.port, timeout=2))
                            r.health = status
                        except Exception:
                            r.health = HealthStatus.UNHEALTHY
                    else:
                        # HTTP check
                        endpoint = app_cfg.health.endpoint if app_cfg else "/api/health"
                        if not endpoint:
                            continue
                        result = check_health_sync(r.host, r.port, endpoint)
                        if result == "Healthy":
                            r.health = HealthStatus.HEALTHY
                        elif result == "Unhealthy":
                            r.health = HealthStatus.UNHEALTHY
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

        # Capture current selection by key
        selected_row_key = None
        if table.cursor_row is not None:
            try:
                selected_row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            except Exception:
                pass

        # BUILD LIVE SET BEFORE MODIFICATION
        # We track both the raw ID and the raw name to ensure we match apps in config
        live_identities = set()
        for r in resources:
            live_identities.add(r.id)
            live_identities.add(r.name)

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

            # Determine reachable host for display
            app_cfg = self._config.apps.get(r.name)
            if app_cfg is None:
                for cfg in self._config.apps.values():
                    if cfg.deploy.container_name == r.name:
                        app_cfg = cfg
                        break
            
            reachable_host = r.host
            if app_cfg and app_cfg.public_host:
                reachable_host = app_cfg.public_host
            elif r.address == "127.0.0.1":
                reachable_host = "localhost"

            port_col = (
                f"{r.protocol}://{reachable_host}:{r.port}"
                if r.port else "—"
            )
            health_col = (
                f"{health_icon} {r.health.value}" if r.health.value != "Unknown" else "—"
            )

            # Extract commit hash from history for the "Deploy" column
            deploy_status = self._deploy_readiness(r)
            if app_cfg and app_cfg.history:
                for event in reversed(app_cfg.history):
                    if event.event_type == "deployed":
                        commit = event.details.get("commit_hash", "")
                        if commit:
                            deploy_status = f"#{commit} {deploy_status}"
                        break

            # Source tag
            source_tag = "[blue][M][/blue]" if r.managed else "[yellow][D][/yellow]"

            table.add_row(
                r.provider_name,
                f"{source_tag} {r.name}",
                type_label,
                f"{status_icon} {r.status.value}",
                health_col,
                port_col,
                r.uptime or "—",
                r.image or "",
                deploy_status,
                key=rkey,
            )

        # Add configured apps that have no matching live resource (e.g. not yet deployed)
        for name, app_cfg in self._config.apps.items():
            container = app_cfg.deploy.container_name
            if name in live_identities or container in live_identities:
                continue
            host_key = app_cfg.host or next(iter(self._config.hosts), "")
            port = str(app_cfg.deploy.host_port) if app_cfg.deploy.host_port else "—"
            image = f"{app_cfg.deploy.image_name}:latest" if app_cfg.deploy.image_name else ""
            rkey = f"{host_key}:{name}"

            deploy_status = self._deploy_readiness_from_config(app_cfg)
            if app_cfg.history:
                for event in reversed(app_cfg.history):
                    if event.event_type == "deployed":
                        commit = event.details.get("commit_hash", "")
                        if commit:
                            deploy_status = f"#{commit} {deploy_status}"
                        break

            table.add_row(
                host_key, name, "Docker", "⚪ Not deployed",
                "—", port, "—", image,
                deploy_status,
                key=rkey,
            )

        # Restore selection
        if selected_row_key:
            try:
                table.move_cursor(row=table.get_row_index(selected_row_key))
            except Exception:
                pass

    def _deploy_readiness_from_config(self, app_cfg) -> str:
        from homepilot.models import ProxmoxHostConfig
        has_image = bool(app_cfg.deploy.image_name)
        host_cfg = self._config.hosts.get(app_cfg.host or "")
        if isinstance(host_cfg, ProxmoxHostConfig):
            return "✅ Ready" if has_image else "⚙  Config"
        has_source = bool(app_cfg.source.path or app_cfg.source.git_url)
        # Source-built apps need both; image-only (registry) apps just need an image name.
        return "✅ Ready" if has_image else "⚙  Config"

    def _rebuild_server_panel(self, resources: list[Resource]) -> None:
        """Update the server summary table with live status and app counts."""
        srv = self.query_one("#server-table", DataTable)
        srv.clear()

        counts: dict[str, int] = {}
        for r in resources:
            counts[r.provider_name] = counts.get(r.provider_name, 0) + 1

        for key, host in self._config.hosts.items():
            provider = self._registry.get_provider(key)
            connected = provider is not None and provider.is_connected()
            status = "🟢 Online" if connected else "🔴 Offline"
            
            ready = getattr(provider, "bootstrap_status", "—") if provider else "—"

            app_count = str(counts.get(key, 0))
            
            cpu, ram, disk = "—", "—", "—"
            if provider and provider.last_metrics:
                m = provider.last_metrics
                from homepilot.providers.base import render_sparkline
                spark = render_sparkline(provider.metrics_history)
                cpu = f"{spark} {m.cpu_pct:.1f}%"
                ram = f"{m.ram_pct:.1f}%"
                disk = f"{m.disk_pct:.1f}%"

            srv.add_row(key, host.host, status, ready, app_count, cpu, ram, disk, key=key)

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
            # Use coordinate_to_cell_key to get the RowKey directly
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            # RowKey.value is the string we use in self._resources
            return self._resources.get(str(row_key.value))
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
        return self._deploy_readiness_from_config(app_cfg)

    def _get_selected_app_name(self) -> str | None:
        r = self._get_selected_resource()
        if r is not None:
            if r.name in self._config.apps:
                return r.name
            for app_key, app_cfg in self._config.apps.items():
                if app_cfg.deploy.container_name == r.name:
                    return app_key
            return None

        # No live resource — check if the selected row is an undeployed config app
        table = self.query_one("#resource-table", DataTable)
        try:
            row_data = table.get_row_at(table.cursor_row)
            if row_data:
                name = str(row_data[1])
                if name in self._config.apps:
                    return name
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Keybinding actions
    # ------------------------------------------------------------------

    def action_deploy_selected(self) -> None:
        name = self._get_selected_app_name()
        if name:
            from homepilot.screens.deploy import DeployScreen
            self.app.push_screen(DeployScreen(self._config, self._registry, name))

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
                f"'{r.name}' is already in config. Press [bold]Enter[/bold] then select the 'Configure' tab to edit.",
                title="Already Managed",
                severity="warning",
                timeout=4,
            )
            return
        from homepilot.screens.import_config import ImportConfigScreen
        self.app.push_screen(ImportConfigScreen(self._config, self._registry, r))

    def action_add_resource(self) -> None:
        from homepilot.screens.add_resource import AddResourceScreen
        self.app.push_screen(AddResourceScreen(self._config, self._registry))

    def action_registry_deploy(self) -> None:
        from homepilot.screens.registry_browser import RegistryBrowserScreen
        self.app.push_screen(RegistryBrowserScreen(self._config, self._registry))

    def action_delete_app(self) -> None:
        name = self._get_selected_app_name()
        if name:
            from homepilot.screens.delete_app import DeleteAppScreen
            self.app.push_screen(DeleteAppScreen(self._config, self._registry, name))
            return

        r = self._get_selected_resource()
        if r:
            from homepilot.screens.cleanup_resource import CleanupResourceScreen
            self.app.push_screen(CleanupResourceScreen(self._config, self._registry, r))
        else:
            self.notify("Select an app or resource to delete.", severity="warning", timeout=3)

    def action_migrate_selected(self) -> None:
        name = self._get_selected_app_name()
        if name:
            from homepilot.screens.migrate import MigrateScreen
            self.app.push_screen(MigrateScreen(self._config, self._registry, name))
        else:
            self.notify("Select a HomePilot-managed app to migrate.", severity="warning", timeout=3)

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
