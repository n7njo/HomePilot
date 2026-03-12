"""Deploy screen — shows step-by-step deployment progress."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from homepilot.config import save_config
from homepilot.models import HomePilotConfig
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
        Binding("escape", "go_back", "Back", show=True),
    ]

    def __init__(self, config: HomePilotConfig, app_name: str) -> None:
        super().__init__()
        self._config = config
        self._app_name = app_name
        self._deployer = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Label(f"  🚀 Deploying: {self._app_name}", id="deploy-title"),
            VerticalScroll(id="steps-container"),
            LogViewer(id="deploy-log"),
            Button("⛔ Abort", id="btn-abort", variant="error"),
            id="deploy-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._start_deploy()

    @work(thread=True)
    def _start_deploy(self) -> None:
        """Run the deployment pipeline in a background thread."""
        from homepilot.services.deployer import Deployer

        app_config = self._config.apps[self._app_name]

        def line_cb(line: str) -> None:
            self.app.call_from_thread(self._log_line, line)

        self._deployer = Deployer(
            self._config.server,
            app_config,
            line_callback=line_cb,
        )

        for step_name, status, message in self._deployer.run_sync():
            icon = STEP_ICONS.get(status, "•")
            step_text = f" {icon}  {step_name}: {message}"
            css_class = f"step-{status}"
            self.app.call_from_thread(self._add_step, step_text, css_class)

        # Final summary
        if self._deployer.state and self._deployer.state.succeeded:
            # Record the deploy timestamp.
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

        # Disable abort button.
        self.app.call_from_thread(self._disable_abort)

    def _add_step(self, text: str, css_class: str) -> None:
        """Add a step label to the steps container."""
        try:
            container = self.query_one("#steps-container", VerticalScroll)
            label = Label(text, classes=f"step-row {css_class}")
            container.mount(label)
            container.scroll_end()
        except Exception:
            pass

    def _log_line(self, line: str) -> None:
        """Append a line to the deploy log viewer."""
        try:
            log = self.query_one("#deploy-log", LogViewer)
            log.append_line(line)
        except Exception:
            pass

    def _disable_abort(self) -> None:
        """Disable the abort button after deployment completes."""
        try:
            btn = self.query_one("#btn-abort", Button)
            btn.disabled = True
            btn.label = "Done"
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-abort" and self._deployer:
            self._deployer.abort()
            self._log_line("⛔ Abort requested…")

    def action_go_back(self) -> None:
        self.app.pop_screen()
