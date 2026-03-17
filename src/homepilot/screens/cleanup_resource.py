"""Cleanup resource screen — confirm removal of a discovered resource from the server."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry
from homepilot.providers.base import Resource


class CleanupResourceScreen(Screen):
    """Confirm removal of a discovered server resource using keyboard shortcuts."""

    BINDINGS = [
        Binding("enter", "confirm_cleanup", "Confirm & Remove", show=True),
        Binding("escape", "go_back", "Cancel", show=True),
    ]

    def __init__(
        self,
        config: HomePilotConfig,
        registry: ProviderRegistry,
        resource: Resource,
    ) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._resource = resource
        self._done = False

    def compose(self) -> ComposeResult:
        r = self._resource
        yield Header()
        yield Vertical(
            Label(f"\n  Cleanup Discovered Resource: {r.name}\n", id="cleanup-title"),
            Label(f"  Provider: {r.provider_name}"),
            Label(f"  Type:     {r.resource_type.value}"),
            Label(f"  Image:    {r.image or '—'}"),
            Label(f"  Status:   {r.status.value}\n"),
            
            Static("[yellow]This resource is NOT managed by HomePilot (it was discovered on the server).[/yellow]\n", id="cleanup-warning"),
            Label("  [bold]Press [cyan]Enter[/cyan] to permanently remove this from the server.[/bold]"),
            Label("  [bold]Press [cyan]Esc[/cyan] to cancel.[/bold]\n"),
            
            Static("", id="cleanup-status"),
            VerticalScroll(id="cleanup-log"),
            id="cleanup-body",
        )
        yield Footer()

    def action_confirm_cleanup(self) -> None:
        if self._done:
            return
        self._done = True
        self.query_one("#cleanup-status", Static).update("[yellow]Removing from server...[/yellow]")
        self._run_cleanup()

    @work(thread=True)
    def _run_cleanup(self) -> None:
        provider = self._registry.get_provider(self._resource.provider_name)
        if not provider:
            self.app.call_from_thread(
                self.query_one("#cleanup-status", Static).update,
                "[red]Error: Provider not found.[/red]",
            )
            return

        try:
            success = provider.remove(self._resource.id)
            if success:
                self.app.call_from_thread(
                    self.query_one("#cleanup-status", Static).update,
                    f"[green]✅ '{self._resource.name}' removed from {self._resource.provider_name}. Press Escape.[/green]",
                )
            else:
                self.app.call_from_thread(
                    self.query_one("#cleanup-status", Static).update,
                    f"[red]❌ Failed to remove '{self._resource.name}'. Check server logs.[/red]",
                )
        except Exception as exc:
            self.app.call_from_thread(
                self.query_one("#cleanup-status", Static).update,
                f"[red]Error: {exc}[/red]",
            )

    def action_go_back(self) -> None:
        self.app.pop_screen()
