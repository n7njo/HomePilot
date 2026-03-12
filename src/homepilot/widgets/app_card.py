"""App card widget for the dashboard grid view."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Label, Static

from homepilot.models import AppRuntimeInfo, AppStatus, HealthStatus


class AppCard(Static):
    """A compact card showing an app's name, status, health, and port."""

    DEFAULT_CSS = """
    AppCard {
        height: auto;
        min-height: 5;
        padding: 1 2;
        margin: 0 1 1 0;
        border: solid $surface-lighten-2;
        background: $surface;
    }
    AppCard:hover {
        border: solid $accent;
    }
    AppCard .card-name {
        text-style: bold;
        color: $text;
    }
    AppCard .card-status {
        color: $text-muted;
    }
    AppCard .card-healthy {
        color: $success;
    }
    AppCard .card-unhealthy {
        color: $error;
    }
    AppCard .card-unknown {
        color: $text-disabled;
    }
    """

    app_name: reactive[str] = reactive("")
    status_text: reactive[str] = reactive("Unknown")
    health_text: reactive[str] = reactive("Unknown")
    port_text: reactive[str] = reactive("—")
    image_text: reactive[str] = reactive("—")

    def compose(self) -> ComposeResult:
        yield Label("", id="card-name", classes="card-name")
        yield Label("", id="card-detail", classes="card-status")

    def watch_app_name(self, value: str) -> None:
        try:
            self.query_one("#card-name", Label).update(f"  {value}")
        except Exception:
            pass

    def _update_detail(self) -> None:
        status_icon = {"Running": "🟢", "Stopped": "🔴", "Error": "🟡"}.get(
            self.status_text, "⚪"
        )
        health_icon = {"Healthy": "💚", "Unhealthy": "💔"}.get(
            self.health_text, "❓"
        )
        detail = (
            f"{status_icon} {self.status_text}  {health_icon} {self.health_text}  "
            f"🔌 {self.port_text}  📦 {self.image_text}"
        )
        try:
            self.query_one("#card-detail", Label).update(detail)
        except Exception:
            pass

    def watch_status_text(self, value: str) -> None:
        self._update_detail()

    def watch_health_text(self, value: str) -> None:
        self._update_detail()

    def watch_port_text(self, value: str) -> None:
        self._update_detail()

    def watch_image_text(self, value: str) -> None:
        self._update_detail()

    def update_from_info(self, info: AppRuntimeInfo) -> None:
        """Populate the card from an AppRuntimeInfo instance."""
        self.app_name = info.name
        self.status_text = info.status.value
        self.health_text = info.health.value
        self.port_text = str(info.host_port) if info.host_port else "—"
        self.image_text = info.image_tag or "—"
