"""Main Textual application for HomePilot."""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from homepilot.config import load_config, save_config
from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry
from homepilot.screens.dashboard import DashboardScreen


class HomePilotApp(App):
    """HomePilot — Home Lab Manager TUI."""

    TITLE = "HomePilot"
    SUB_TITLE = "Home Lab Manager"

    BINDINGS = [
        Binding("t", "toggle_theme", "Theme", show=True, priority=True),
    ]

    CSS = """
    Screen {
        background: $surface;
    }

    #dashboard-title {
        text-style: bold;
        color: $accent;
        padding: 1 2 0 2;
        text-align: center;
    }

    #dashboard-subtitle {
        color: $text-muted;
        padding: 0 2 1 2;
        text-align: center;
    }

    #dashboard-body {
        height: 1fr;
    }

    #resource-table {
        height: 1fr;
        margin: 0 2;
    }

    /* Deploy screen */
    #deploy-body {
        height: 1fr;
        padding: 1 2;
    }

    .step-row {
        height: auto;
        padding: 0 1;
    }

    .step-pending {
        color: $text-disabled;
    }

    .step-running {
        color: $warning;
        text-style: bold;
    }

    .step-success {
        color: $success;
    }

    .step-failed {
        color: $error;
        text-style: bold;
    }

    .step-skipped {
        color: $text-disabled;
    }

    /* Config editor */
    #config-form {
        padding: 1 2;
        height: auto;
    }

    #config-form Input {
        margin: 0 0 1 0;
    }

    #config-form Select {
        margin: 0 0 1 0;
    }

    /* Actions panel */
    #actions-panel {
        padding: 1 2;
    }

    #actions-panel Button {
        margin: 0 0 1 0;
        width: 40;
    }

    /* Add resource wizard */
    #wizard-body {
        padding: 1 2;
        height: 1fr;
    }

    #wizard-body Input {
        margin: 0 0 1 0;
    }

    #wizard-buttons {
        dock: bottom;
        height: auto;
        padding: 1 2;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._config = load_config()
        self.providers = ProviderRegistry(self._config)
        self.dark = self._config.theme != "light"

    @property
    def config(self) -> HomePilotConfig:
        return self._config

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen(self._config, self.providers))

    def on_unmount(self) -> None:
        self.providers.disconnect_all()

    def action_toggle_theme(self) -> None:
        """Toggle between dark and light themes."""
        self.dark = not self.dark
        if self._config is not None:
            self._config.theme = "dark" if self.dark else "light"
            save_config(self._config)
