"""Deploy screen — shows step-by-step deployment progress."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from homepilot.config import save_config
from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry
from homepilot.widgets.log_viewer import LogViewer


STEP_ICONS = {
    "running": "⏳",
    "success": "✅",
    "failed": "❌",
    "skipped": "⏭️",
    "pending": "○",
}


class DeployScreen(Screen):
    """Deployment progress display with live output."""

    BINDINGS = [
        Binding("ctrl+x", "abort", "Abort", show=True),
        Binding("escape", "go_back", "Back", show=True),
    ]

    def __init__(self, config: HomePilotConfig, registry: ProviderRegistry, app_name: str) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._app_name = app_name
        self._deployer = None
        self._done = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Label(f"  🚀 Deploying: {self._app_name}", id="deploy-title"),
            Static("", id="deploy-status"),
            VerticalScroll(id="steps-container"),
            LogViewer(id="deploy-log"),
            id="deploy-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._start_deploy()

    def _resolve_server_config(self):
        app_config = self._config.apps[self._app_name]
        host_key = app_config.host or next(iter(self._config.hosts), "")
        provider = self._registry.get_provider(host_key)

        if provider:
            from homepilot.providers.truenas import TrueNASProvider
            if isinstance(provider, TrueNASProvider):
                return provider._config.to_server_config()

        return self._config.server

    @work(thread=True)
    def _start_deploy(self) -> None:
        app_config = self._config.apps[self._app_name]
        host_key = app_config.host or next(iter(self._config.hosts), "")
        provider = self._registry.get_provider(host_key)

        def line_cb(line: str) -> None:
            self.app.call_from_thread(self._log_line, line)

        from homepilot.providers.proxmox import ProxmoxProvider
        if isinstance(provider, ProxmoxProvider):
            from homepilot.services.proxmox_deployer import ProxmoxDeployer
            self._deployer = ProxmoxDeployer(provider._config, app_config, line_callback=line_cb)
        else:
            from homepilot.services.deployer import Deployer
            server_config = self._resolve_server_config()
            self._deployer = Deployer(server_config, app_config, line_callback=line_cb)

        for step_name, status, message in self._deployer.run_sync():
            icon = STEP_ICONS.get(status, "•")
            self.app.call_from_thread(
                self._add_step,
                f" {icon}  {step_name}: {message}",
                f"step-{status}",
            )

        if self._deployer.state and self._deployer.state.succeeded:
            from datetime import datetime, timezone
            app_cfg = self._config.apps.get(self._app_name)
            if app_cfg:
                app_cfg.last_deployed = datetime.now(timezone.utc).isoformat()
                save_config(self._config)
            self.app.call_from_thread(
                self._add_step, "\n ✅  Deployment completed successfully!", "step-success"
            )
        else:
            self.app.call_from_thread(
                self._add_step, "\n ❌  Deployment failed.", "step-failed"
            )

        self._done = True
        self.app.call_from_thread(self._mark_done)

    def _add_step(self, text: str, css_class: str) -> None:
        try:
            container = self.query_one("#steps-container", VerticalScroll)
            container.mount(Label(text, classes=f"step-row {css_class}"))
            container.scroll_end()
        except Exception:
            pass

    def _log_line(self, line: str) -> None:
        try:
            self.query_one("#deploy-log", LogViewer).append_line(line)
        except Exception:
            pass

    def _mark_done(self) -> None:
        try:
            self.query_one("#deploy-status", Static).update(
                "[green]Deployment complete — press Escape to return[/green]"
            )
        except Exception:
            pass

    def action_abort(self) -> None:
        if self._deployer and not self._done:
            self._deployer.abort()
            self._log_line("⛔ Abort requested…")

    def action_go_back(self) -> None:
        self.app.pop_screen()
