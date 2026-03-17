"""Resource detail screen with tabbed views: Overview, Logs, Actions."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Footer,
    Header,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry
from homepilot.providers.base import Resource, ResourceStatus, ResourceType
from homepilot.widgets.log_viewer import LogViewer


class ResourceDetailScreen(Screen):
    """Detail view for any infrastructure resource (Docker, VM, LXC)."""

    BINDINGS = [
        Binding("s", "start_resource", "Start", show=True),
        Binding("x", "stop_resource", "Stop", show=True),
        Binding("r", "restart_resource", "Restart", show=True),
        Binding("d", "deploy_resource", "Deploy", show=True),
        Binding("m", "migrate_resource", "Migrate", show=True),
        Binding("b", "backup_resource", "Backup", show=True),
        Binding("ctrl+r", "refresh_logs", "Refresh Logs", show=True),
        Binding("escape", "go_back", "Back", show=True),
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

        # Try direct key match first, then by container_name
        self._app_config = config.apps.get(resource_id)
        if not self._app_config:
            for cfg in config.apps.values():
                if cfg.deploy.container_name == resource_id:
                    self._app_config = cfg
                    break

    def compose(self) -> ComposeResult:
        display_name = self._app_config.name if self._app_config else self._resource_id

        yield Header()
        with TabbedContent(initial=self._initial_tab):
            with TabPane("Overview", id="overview"):
                yield VerticalScroll(
                    Static(self._build_overview(), id="overview-content"),
                )
            with TabPane("Logs", id="logs"):
                yield LogViewer(id="log-viewer")
            with TabPane("History", id="history"):
                yield VerticalScroll(
                    Static(self._build_history_text(), id="history-content"),
                )
            with TabPane("Actions", id="actions"):
                yield VerticalScroll(
                    Static(self._build_actions_text(display_name), id="actions-content"),
                    Static("", id="action-result"),
                )
        yield Footer()

    def on_mount(self) -> None:
        if self._initial_tab == "logs":
            self._load_logs()

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def _build_overview(self) -> str:
        if self._app_config:
            return self._build_app_overview()
        if self._provider:
            resource = self._provider.get_resource(self._resource_id)
            if resource:
                return self._build_resource_overview(resource)
        return f"\n  Resource: {self._resource_id}\n  Provider: {self._provider_name}\n  (no details available)"

    def _build_history_text(self) -> str:
        if not self._app_config or not self._app_config.history:
            return "\n  No history events recorded."

        lines = ["\n  App History:"]
        for event in reversed(self._app_config.history):
            ts = event.timestamp.replace("T", " ").split(".")[0]
            icon = {
                "created": "🆕",
                "config_changed": "⚙️",
                "deployed": "🚀",
                "migrated": "🚛",
                "deleted": "🗑️",
            }.get(event.event_type.value, "•")

            lines.append(f"  {ts}  {icon}  {event.message}")
            if event.event_type == "deployed" and "commit_hash" in event.details:
                h = event.details["commit_hash"]
                if h:
                    lines.append(f"             Commit: #{h}")
        return "\n".join(lines)

    def _build_app_overview(self) -> str:
        app = self._app_config
        assert app is not None
        lines = [
            f"\n  App: {app.name}",
            f"  Host: {self._provider_name}",
            f"  Source: {app.source.type.value} — {app.source.path or app.source.git_url or '(not set)'}",
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

        lines += ["", "  Environment:"]
        for k, v in app.env.items():
            lines.append(f"    {k}={v}")
        if not app.env:
            lines.append("    (none)")

        return "\n".join(lines)

    @staticmethod
    def _build_resource_overview(resource: Resource) -> str:
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
            lines += [
                "",
                f"  Node: {meta.get('node', '—')}",
                f"  VMID: {meta.get('vmid', '—')}",
                f"  Max CPU: {meta.get('maxcpu', '—')}",
                f"  Max Memory: {_format_bytes(meta.get('maxmem', 0))}",
                f"  Template: {'Yes' if meta.get('template') else 'No'}",
            ]
        if resource.image:
            lines.append(f"  Image: {resource.image}")
        if resource.port:
            lines.append(f"  Port: {resource.port}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Actions panel (text only)
    # ------------------------------------------------------------------

    def _build_actions_text(self, display_name: str) -> str:
        is_app = self._app_config is not None
        has_volumes = is_app and bool(self._app_config.volumes)  # type: ignore[union-attr]
        has_source = is_app and bool(
            self._app_config.source.path or self._app_config.source.git_url  # type: ignore[union-attr]
        )

        lines = [
            f"\n  {display_name}",
            "",
            "  ── Lifecycle ──────────────────────────────────────────",
            "  s   Start       Power on this resource",
            "  x   Stop        Gracefully shut down this resource",
            "  r   Restart     Stop then start",
        ]

        if is_app:
            lines += ["", "  ── Application ────────────────────────────────────────"]
            if has_source:
                lines.append("  d   Deploy      Build a new image and redeploy from source")
            else:
                lines.append("  d   Deploy      (unavailable — no source path configured)")

            lines.append("  m   Migrate     Move this app to a different server")

            if has_volumes:
                lines.append("  b   Backup      Archive container data volumes to backup directory")
            else:
                lines.append("  b   Backup      (unavailable — no volumes configured)")

        lines += [
            "",
            "  ── Logs ───────────────────────────────────────────────",
            "  ctrl+r  Refresh Logs   Reload the latest log output",
            "",
            "  Results appear below and in the Logs tab.",
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    @work(thread=True)
    def _load_logs(self) -> None:
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
            self.query_one("#log-viewer", LogViewer).append_line(line)
        except Exception:
            pass

    def _set_action_result(self, msg: str) -> None:
        try:
            self.query_one("#action-result", Static).update(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_start_resource(self) -> None:
        self._run_provider_action("start")

    def action_stop_resource(self) -> None:
        self._run_provider_action("stop")

    def action_restart_resource(self) -> None:
        self._run_provider_action("restart")

    def action_refresh_logs(self) -> None:
        self._load_logs()

    def action_deploy_resource(self) -> None:
        if not self._app_config:
            self.notify("No app config — use 'i' on the dashboard to import first.", severity="warning")
            return
        if not (self._app_config.source.path or self._app_config.source.git_url):
            self.notify("No source configured — edit via 'c' on the dashboard first.", severity="warning")
            return
        from homepilot.screens.deploy import DeployScreen
        self.app.push_screen(DeployScreen(self._config, self._registry, self._app_config.name))

    def action_migrate_resource(self) -> None:
        if not self._app_config:
            self.notify("No app config — only HomePilot-managed apps can be migrated.", severity="warning")
            return
        from homepilot.screens.migrate import MigrateScreen
        self.app.push_screen(MigrateScreen(self._config, self._registry, self._app_config.name))

    def action_backup_resource(self) -> None:
        if not self._app_config or not self._app_config.volumes:
            self.notify("No volumes configured for backup.", severity="warning")
            return
        self._run_truenas_backup()

    @work(thread=True)
    def _run_provider_action(self, action: str) -> None:
        if not self._provider:
            self.app.call_from_thread(self._set_action_result, "  ❌ No provider available")
            return
        try:
            method = getattr(self._provider, action)
            success = method(self._resource_id)
            icon = "✅" if success else "⚠️"
            result = "succeeded" if success else "returned false"
            self.app.call_from_thread(
                self._set_action_result, f"\n  {icon} {action.capitalize()} {result}"
            )
        except Exception as exc:
            self.app.call_from_thread(self._set_action_result, f"\n  ❌ Error: {exc}")

    @work(thread=True)
    def _run_truenas_backup(self) -> None:
        try:
            from homepilot.providers.truenas import TrueNASProvider
            if not isinstance(self._provider, TrueNASProvider):
                self.app.call_from_thread(
                    self._set_action_result, "\n  ⚠️ Backup only available for TrueNAS hosts"
                )
                return

            truenas = self._provider.truenas
            if not truenas:
                self.app.call_from_thread(self._set_action_result, "\n  ❌ TrueNAS not connected")
                return

            app = self._app_config
            assert app is not None
            host_cfg = self._config.hosts.get(self._provider_name)
            backup_dir = getattr(host_cfg, "backup_dir", "/tmp/homepilot-backups")

            results = []
            for vol in app.volumes:
                result = truenas.backup_container_data(
                    app.deploy.container_name,
                    vol.container,
                    backup_dir,
                )
                if result:
                    results.append(result)
            if results:
                msg = "\n  💾 Backups saved:\n" + "\n".join(f"    {r}" for r in results)
            else:
                msg = "\n  ⚠️ Backup: no data found"
            self.app.call_from_thread(self._set_action_result, msg)
        except Exception as exc:
            self.app.call_from_thread(self._set_action_result, f"\n  ❌ Error: {exc}")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def action_go_back(self) -> None:
        self.app.pop_screen()


# -- Helpers ---------------------------------------------------------------

def _format_bytes(n: int) -> str:
    if n <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"
