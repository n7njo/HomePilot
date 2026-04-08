"""Resource detail screen with tabbed views: Overview, Logs, Actions."""

from __future__ import annotations

from textual import work, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.screen import Screen
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from homepilot.config import save_config, validate_config
from homepilot.models import (
    HomePilotConfig,
    PortMode,
    SourceType,
    AccessLevel,
    NetworkMode,
    HealthProtocol,
    HistoryEventType,
    AppHistoryEvent,
    VolumeMount,
)
from homepilot.providers import ProviderRegistry
from homepilot.providers.base import Resource, ResourceStatus, ResourceType
from homepilot.widgets.log_viewer import LogViewer


class ResourceDetailScreen(Screen):
    """Detail view for any infrastructure resource (Docker, VM, LXC)."""

    DEFAULT_CSS = """
    ResourceDetailScreen #config-form {
        padding: 0 1;
    }
    ResourceDetailScreen .config-column {
        width: 1fr;
        padding: 0 2;
        margin: 0 1;
        border: round $primary 40%;
        background: $surface;
    }
    ResourceDetailScreen .section-header {
        text-style: bold;
        color: $accent;
        padding: 0 0;
        margin-top: 0;
        margin-bottom: 0;
        border-bottom: solid $accent;
    }
    ResourceDetailScreen .field-row {
        height: 2;
        margin-bottom: 0;
        padding: 0;
        align: left middle;
    }
    ResourceDetailScreen .field-label {
        width: 18;
        color: $text-muted;
        padding: 0 0 0 0;
    }
    ResourceDetailScreen .field-input {
        width: 1fr;
    }
    ResourceDetailScreen Input, ResourceDetailScreen Select, ResourceDetailScreen TextArea {
        background: $surface;
        border: none;
        color: $text;
        margin: 0;
        padding: 0 1;
    }
    ResourceDetailScreen Input {
        height: 1;
    }
    ResourceDetailScreen Select {
        height: 3;
        border: none;
    }
    ResourceDetailScreen TextArea {
        height: 5;
        border: tall $primary 20%;
    }
    ResourceDetailScreen Input:focus, ResourceDetailScreen Select:focus, ResourceDetailScreen TextArea:focus {
        background: $accent 20%;
        color: $text;
        border: none;
    }
    ResourceDetailScreen Select {
        padding: 0 1;
    }
    ResourceDetailScreen #config-status {
        padding: 0 0 1 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save_config", "Save Config", show=True),
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

        # Adjust initial tab if managed (Overview is removed)
        if self._app_config and initial_tab == "overview":
            self._initial_tab = "configure"
        else:
            self._initial_tab = initial_tab

    def compose(self) -> ComposeResult:
        display_name = self._app_config.name if self._app_config else self._resource_id

        yield Header()
        with TabbedContent(initial=self._initial_tab):
            if self._app_config:
                with TabPane("Configure", id="configure"):
                    with VerticalScroll(id="config-form"):
                        yield from self._compose_config_fields()
            else:
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
    # Configuration
    # ------------------------------------------------------------------

    def _compose_config_fields(self) -> ComposeResult:
        app = self._app_config
        if not app:
            return

        host_options = [(k, k) for k in self._config.hosts]
        # Ensure current host is in options to avoid Select crash
        if app.host and app.host not in self._config.hosts:
            host_options.append((app.host, f"{app.host} (unknown)"))

        # Fetch current runtime info for summary
        resource = None
        if self._provider:
            resource = self._provider.get_resource(self._resource_id)

        yield Static("", id="config-status")

        with Horizontal():
            # --- Column 1 ---
            with Vertical(classes="config-column"):
                yield Label("Runtime Summary", classes="section-header")
                if resource:
                    with Horizontal(classes="field-row"):
                        yield Label("Live Status", classes="field-label")
                        yield Label(f"[bold]{resource.status.value}[/bold]", classes="field-input")
                    with Horizontal(classes="field-row"):
                        yield Label("Uptime", classes="field-label")
                        yield Label(resource.uptime or "—", classes="field-input")
                    if resource.resource_type in (ResourceType.VM, ResourceType.LXC_CONTAINER):
                        with Horizontal(classes="field-row"):
                            yield Label("Node / VMID", classes="field-label")
                            yield Label(f"{resource.metadata.get('node', '—')} / {resource.metadata.get('vmid', '—')}", classes="field-input")
                else:
                    yield Label("  (Resource not currently active)", classes="field-input")

                yield Label("Source", classes="section-header")
                with Horizontal(classes="field-row"):
                    yield Label("Type", classes="field-label")
                    yield Select([(t.value, t.value) for t in SourceType], value=app.source.type.value, id="conf-src-type", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Local Path", classes="field-label")
                    yield Input(value=app.source.path, id="conf-src-path", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Git URL", classes="field-label")
                    yield Input(value=app.source.git_url, id="conf-src-git-url", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Git Branch", classes="field-label")
                    yield Input(value=app.source.git_branch, id="conf-src-git-branch", classes="field-input")

                yield Label("Build", classes="section-header")
                with Horizontal(classes="field-row"):
                    yield Label("Dockerfile", classes="field-label")
                    yield Input(value=app.build.dockerfile, id="conf-bld-dockerfile", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Build Context", classes="field-label")
                    yield Input(value=app.build.context, id="conf-bld-context", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Platform", classes="field-label")
                    yield Input(value=app.build.platform, id="conf-bld-platform", classes="field-input")

                yield Label("Health Check", classes="section-header")
                with Horizontal(classes="field-row"):
                    yield Label("Protocol", classes="field-label")
                    yield Select([(p.value, p.value) for p in HealthProtocol], value=app.health.protocol.value, id="conf-hlth-proto", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Endpoint", classes="field-label")
                    yield Input(value=app.health.endpoint, id="conf-hlth-endpt", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Exp. Status", classes="field-label")
                    yield Input(value=str(app.health.expected_status), id="conf-hlth-status", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Interval (s)", classes="field-label")
                    yield Input(value=str(app.health.interval_seconds), id="conf-hlth-interval", classes="field-input")

            # --- Column 2 ---
            with Vertical(classes="config-column"):
                yield Label("Deployment", classes="section-header")
                with Horizontal(classes="field-row"):
                    yield Label("Target Server", classes="field-label")
                    yield Select(host_options, value=app.host, id="conf-host", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Image Name", classes="field-label")
                    yield Input(value=app.deploy.image_name, id="conf-dep-image", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Container", classes="field-label")
                    yield Input(value=app.deploy.container_name, id="conf-dep-container", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Host Port", classes="field-label")
                    yield Input(value=str(app.deploy.host_port), id="conf-dep-host-port", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Access Level", classes="field-label")
                    yield Select([(a.value, a.value) for a in AccessLevel], value=app.deploy.access_level.value, id="conf-dep-access", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Network Mode", classes="field-label")
                    yield Select([(n.value, n.value) for n in NetworkMode], value=app.deploy.network_mode.value, id="conf-dep-net", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Public Host", classes="field-label")
                    yield Input(value=app.public_host, id="conf-pub-host", classes="field-input")

                yield Label("Resource Limits (0=None)", classes="section-header")
                with Horizontal(classes="field-row"):
                    yield Label("Max CPU", classes="field-label")
                    yield Input(value=str(app.deploy.cpu_limit), id="conf-dep-cpu", classes="field-input")
                with Horizontal(classes="field-row"):
                    yield Label("Max RAM (MB)", classes="field-label")
                    yield Input(value=str(app.deploy.memory_limit_mb), id="conf-dep-ram", classes="field-input")

                yield Label("Volumes", classes="section-header")
                yield TextArea(
                    "\n".join(f"{v.host}:{v.container}" + (f":{v.mode}" if v.mode else "") for v in app.volumes),
                    id="conf-volumes"
                )

                yield Label("Environment Variables", classes="section-header")
                yield TextArea(
                    "\n".join(f"{k}={v}" for k, v in app.env.items()),
                    id="conf-env"
                )

    def action_save_config(self) -> None:
        if not self._app_config:
            return

        app = self._app_config
        status = self.query_one("#config-status", Static)

        try:
            app.host = str(self.query_one("#conf-host", Select).value)
            app.source.type = SourceType(self.query_one("#conf-src-type", Select).value)
            app.source.path = self.query_one("#conf-src-path", Input).value
            app.source.git_url = self.query_one("#conf-src-git-url", Input).value
            app.source.git_branch = self.query_one("#conf-src-git-branch", Input).value

            app.build.dockerfile = self.query_one("#conf-bld-dockerfile", Input).value
            app.build.context = self.query_one("#conf-bld-context", Input).value
            app.build.platform = self.query_one("#conf-bld-platform", Input).value

            app.deploy.image_name = self.query_one("#conf-dep-image", Input).value
            app.deploy.container_name = self.query_one("#conf-dep-container", Input).value
            app.deploy.host_port = int(self.query_one("#conf-dep-host-port", Input).value or "0")
            app.deploy.access_level = AccessLevel(self.query_one("#conf-dep-access", Select).value)
            app.deploy.network_mode = NetworkMode(self.query_one("#conf-dep-net", Select).value)
            app.deploy.cpu_limit = float(self.query_one("#conf-dep-cpu", Input).value or "0.0")
            app.deploy.memory_limit_mb = int(self.query_one("#conf-dep-ram", Input).value or "0")
            app.public_host = self.query_one("#conf-pub-host", Input).value

            app.health.protocol = HealthProtocol(self.query_one("#conf-hlth-proto", Select).value)
            app.health.endpoint = self.query_one("#conf-hlth-endpt", Input).value
            app.health.expected_status = int(self.query_one("#conf-hlth-status", Input).value or "200")
            app.health.interval_seconds = int(self.query_one("#conf-hlth-interval", Input).value or "30")


            volumes = []
            for line in self.query_one("#conf-volumes", TextArea).text.splitlines():
                line = line.strip()
                if ":" in line:
                    parts = line.split(":", 2)
                    h = parts[0].strip()
                    c = parts[1].strip() if len(parts) > 1 else ""
                    m = parts[2].strip() if len(parts) > 2 else ""
                    if h and c:
                        volumes.append(VolumeMount(host=h, container=c, mode=m))
            app.volumes = volumes

            app.env = {}
            for line in self.query_one("#conf-env", TextArea).text.splitlines():
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    app.env[k.strip()] = v.strip()

            errors = validate_config(self._config)
            if errors:
                status.update(f"[red]Errors: {'; '.join(errors)}[/red]")
                return

            save_config(self._config)

            # Record config change in history
            from datetime import datetime, timezone
            app.history.append(AppHistoryEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type=HistoryEventType.CONFIG_CHANGED,
                message="Configuration updated via detail tab",
            ))
            save_config(self._config)

            status.update("[green]✅ Configuration saved successfully[/green]")
            self.query_one("#overview-content", Static).update(self._build_overview())

        except Exception as exc:
            status.update(f"[red]Error saving config: {exc}[/red]")

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
