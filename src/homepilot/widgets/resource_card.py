"""Resource card widget for the dashboard grid view."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Label, Static

from homepilot.providers.base import HealthStatus, Resource, ResourceStatus


class ResourceCard(Static):
    """A compact card showing a resource's name, type, status, and health."""

    DEFAULT_CSS = """
    ResourceCard {
        height: auto;
        min-height: 5;
        padding: 1 2;
        margin: 0 1 1 0;
        border: solid $surface-lighten-2;
        background: $surface;
    }
    ResourceCard:hover {
        border: solid $accent;
    }
    ResourceCard .card-name {
        text-style: bold;
        color: $text;
    }
    ResourceCard .card-status {
        color: $text-muted;
    }
    """

    resource_name: reactive[str] = reactive("")
    status_text: reactive[str] = reactive("Unknown")
    health_text: reactive[str] = reactive("Unknown")
    type_text: reactive[str] = reactive("")
    port_text: reactive[str] = reactive("—")
    host_text: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield Label("", id="card-name", classes="card-name")
        yield Label("", id="card-detail", classes="card-status")

    def watch_resource_name(self, value: str) -> None:
        try:
            self.query_one("#card-name", Label).update(f"  {value}")
        except Exception:
            pass

    def _update_detail(self) -> None:
        status_icon = {"Running": "🟢", "Stopped": "🔴", "Error": "🟡"}.get(
            self.status_text, "⚪"
        )
        health_icon = {"Healthy": "💚", "Unhealthy": "💔"}.get(
            self.health_text, ""
        )
        parts = [f"{status_icon} {self.status_text}"]
        if health_icon:
            parts.append(f"{health_icon} {self.health_text}")
        if self.type_text:
            parts.append(self.type_text)
        if self.port_text and self.port_text != "—":
            parts.append(f"🔌 {self.port_text}")
        if self.host_text:
            parts.append(f"🖥 {self.host_text}")
        detail = "  ".join(parts)
        try:
            self.query_one("#card-detail", Label).update(detail)
        except Exception:
            pass

    def watch_status_text(self, value: str) -> None:
        self._update_detail()

    def watch_health_text(self, value: str) -> None:
        self._update_detail()

    def watch_type_text(self, value: str) -> None:
        self._update_detail()

    def watch_port_text(self, value: str) -> None:
        self._update_detail()

    def watch_host_text(self, value: str) -> None:
        self._update_detail()

    def update_from_resource(self, resource: Resource) -> None:
        """Populate the card from a Resource instance."""
        self.resource_name = resource.name
        self.status_text = resource.status.value
        self.health_text = resource.health.value
        self.type_text = resource.resource_type.value
        self.port_text = str(resource.port) if resource.port else "—"
        self.host_text = resource.provider_name
