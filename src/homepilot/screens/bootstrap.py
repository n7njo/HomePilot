"""Bootstrap screen — provision a host for HomePilot management."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, Static

from homepilot.config import save_config
from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry

STEP_ICONS = {
    "running": "⏳",
    "success": "✅",
    "failed": "❌",
    "skipped": "⏭️",
}


class BootstrapScreen(Screen):
    """Run the host bootstrap pipeline and stream progress."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
    ]

    def __init__(
        self,
        config: HomePilotConfig,
        registry: ProviderRegistry,
        host_key: str,
        root_user: str = "root",
    ) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._host_key = host_key
        self._root_user = root_user
        self._bootstrapper = None
        self._done = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Label(f"  🔧 Bootstrap: {self._host_key}", id="bs-title"),
            Static("", id="bs-status"),
            VerticalScroll(id="bs-steps"),
            Static("", id="bs-log"),
            id="bs-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._run_bootstrap()

    @work(thread=True)
    def _run_bootstrap(self) -> None:
        from homepilot.services.bootstrap import make_bootstrap_service

        host_cfg = self._config.hosts.get(self._host_key)
        if host_cfg is None:
            self.app.call_from_thread(
                self._add_step, "❌ Host not found in config", "step-failed"
            )
            return

        def line_cb(line: str) -> None:
            self.app.call_from_thread(self._append_log, line)

        self._bootstrapper = make_bootstrap_service(
            host_cfg,
            root_user=self._root_user,
            line_callback=line_cb,
        )

        succeeded = True
        for step_name, status, message in self._bootstrapper.run_sync():
            icon = STEP_ICONS.get(status, "•")
            self.app.call_from_thread(
                self._add_step,
                f" {icon}  {step_name}: {message}",
                f"step-{status}",
            )
            if status == "failed":
                succeeded = False

        if succeeded:
            # Update host config to use homepilot user going forward
            from homepilot.services.bootstrap import HOMEPILOT_USER
            from homepilot.models import ProxmoxHostConfig, TrueNASHostConfig
            host_cfg = self._config.hosts.get(self._host_key)
            if isinstance(host_cfg, (ProxmoxHostConfig, TrueNASHostConfig)):
                if isinstance(host_cfg, ProxmoxHostConfig):
                    host_cfg.ssh_user = HOMEPILOT_USER
                else:
                    # Preserve the original admin user before overwriting
                    if not host_cfg.admin_user:
                        host_cfg.admin_user = host_cfg.user
                    host_cfg.user = HOMEPILOT_USER
                save_config(self._config)
                self._registry.register_host(self._host_key, host_cfg)

            self.app.call_from_thread(
                self._add_step,
                f"\n ✅  Bootstrap complete — SSH user updated to '{HOMEPILOT_USER}'",
                "step-success",
            )
            self.app.call_from_thread(
                self.query_one("#bs-status", Static).update,
                f"[green]Host ready. Future connections use '{HOMEPILOT_USER}'. Press Escape.[/green]",
            )
        else:
            self.app.call_from_thread(
                self.query_one("#bs-status", Static).update,
                "[red]Bootstrap failed — see errors above. Fix and re-run.[/red]",
            )

        self._done = True

    def _add_step(self, text: str, css_class: str) -> None:
        try:
            container = self.query_one("#bs-steps", VerticalScroll)
            container.mount(Label(text, classes=f"step-row {css_class}"))
            container.scroll_end()
        except Exception:
            pass

    def _append_log(self, line: str) -> None:
        try:
            log = self.query_one("#bs-log", Static)
            current = str(log.renderable)
            log.update(current + "\n" + line)
        except Exception:
            pass

    def action_go_back(self) -> None:
        self.app.pop_screen()
