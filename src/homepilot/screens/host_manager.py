"""Host manager screen — list, add, edit and delete server configurations."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from homepilot.config import save_config
from homepilot.models import HomePilotConfig, ProxmoxHostConfig, TrueNASHostConfig
from homepilot.providers import ProviderRegistry


class HostManagerScreen(Screen):
    """List all configured servers with add / edit / delete / test actions."""

    BINDINGS = [
        Binding("a", "add_host", "Add Server", show=True),
        Binding("e", "edit_host", "Edit", show=True),
        Binding("d", "delete_host", "Delete", show=True),
        Binding("t", "test_connection", "Test", show=True),
        Binding("b", "bootstrap_host", "Bootstrap", show=True),
        Binding("escape", "go_back", "Back", show=True),
    ]

    def __init__(self, config: HomePilotConfig, registry: ProviderRegistry) -> None:
        super().__init__()
        self._config = config
        self._registry = registry

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Label("\n  Managed Servers\n", id="hm-title"),
            Static("", id="hm-status"),
            DataTable(id="host-table"),
            id="hm-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#host-table", DataTable)
        table.cursor_type = "row"
        table.add_column("Name", key="name")
        table.add_column("Type", key="type")
        table.add_column("Address", key="addr")
        table.add_column("Details", key="details")
        self._rebuild_table()

    def on_screen_resume(self) -> None:
        """Refresh when returning from an editor sub-screen."""
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        table = self.query_one("#host-table", DataTable)
        table.clear()
        for key, host in self._config.hosts.items():
            if isinstance(host, TrueNASHostConfig):
                type_label = "TrueNAS"
                details = f"user={host.user}  data={host.data_root}"
            elif isinstance(host, ProxmoxHostConfig):
                type_label = "Proxmox"
                details = f"token={host.token_id or '—'}  ssl={'yes' if host.verify_ssl else 'no'}"
            else:
                type_label = host.type
                details = ""
            table.add_row(key, type_label, host.host, details, key=key)

    def _selected_key(self) -> str | None:
        table = self.query_one("#host-table", DataTable)
        if table.cursor_row is None:
            return None
        try:
            row = table.get_row_at(table.cursor_row)
            return str(row[0]) if row else None
        except Exception:
            return None

    def action_add_host(self) -> None:
        from homepilot.screens.host_editor import HostEditorScreen
        self.app.push_screen(HostEditorScreen(self._config, self._registry))

    def action_edit_host(self) -> None:
        key = self._selected_key()
        if not key:
            return
        from homepilot.screens.host_editor import HostEditorScreen
        self.app.push_screen(HostEditorScreen(self._config, self._registry, host_key=key))

    def action_delete_host(self) -> None:
        key = self._selected_key()
        if not key:
            return
        status = self.query_one("#hm-status", Static)
        apps_using = [name for name, app in self._config.apps.items() if app.host == key]
        if apps_using:
            status.update(
                f"[red]Cannot delete '{key}': used by {', '.join(apps_using)}[/red]"
            )
            return
        self._registry.unregister_host(key)
        del self._config.hosts[key]
        save_config(self._config)
        self._rebuild_table()
        status.update(f"[green]Deleted '{key}'[/green]")

    def action_test_connection(self) -> None:
        key = self._selected_key()
        if not key:
            return
        status = self.query_one("#hm-status", Static)
        status.update(f"  Testing '{key}'…")
        self._run_test(key)

    @work(thread=True)
    def _run_test(self, key: str) -> None:
        status = self.query_one("#hm-status", Static)
        provider = self._registry.get_provider(key)
        if provider is None:
            self.app.call_from_thread(
                status.update,
                f"[yellow]No provider for '{key}' — refresh the dashboard to connect[/yellow]",
            )
            return
        try:
            provider.connect()
            resources = provider.list_resources()
            msg = f"[green]✅ '{key}' — connected, {len(resources)} resources[/green]"
        except Exception as exc:
            msg = f"[red]❌ '{key}' failed: {exc}[/red]"
        self.app.call_from_thread(status.update, msg)

    def action_bootstrap_host(self) -> None:
        key = self._selected_key()
        if not key:
            return
        from homepilot.screens.bootstrap import BootstrapScreen
        self.app.push_screen(BootstrapScreen(self._config, self._registry, key))

    def action_go_back(self) -> None:
        self.app.pop_screen()
