"""Migration screen — orchestrates app migration between hosts."""

from __future__ import annotations

from textual import work, on, events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static, OptionList
from textual.widgets.option_list import Option

from homepilot.config import save_config
from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry
from homepilot.widgets.log_viewer import LogViewer
from homepilot.services.migrator import Migrator


STEP_ICONS = {
    "running": "⏳",
    "success": "✅",
    "failed": "❌",
    "skipped": "⏭️",
    "pending": "○",
}


class MigrateScreen(Screen):
    """Migration progress display with live output."""

    BINDINGS = [
        Binding("enter", "start_migration", "Start", show=True, priority=True),
        Binding("y", "confirm_removal", "Confirm & Remove", show=False),
        Binding("n", "finish_keep_both", "Keep Both", show=False),
        Binding("ctrl+x", "abort", "Abort", show=True),
        Binding("escape", "go_back", "Back", show=True),
    ]

    def __init__(self, config: HomePilotConfig, registry: ProviderRegistry, app_name: str) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._app_name = app_name
        self._migrator = None
        self._done = False
        self._dest_host = None
        self._phase = "selection"  # selection, progress, confirmation, done

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Label(f"  🚛 Migrating: {self._app_name}", id="migrate-title"),
            Static("Select destination host and press [bold]Enter[/bold] to start:", id="migrate-instruction"),
            OptionList(id="dest-host-list"),
            VerticalScroll(id="steps-container", classes="hidden"),
            LogViewer(id="migrate-log", classes="hidden"),
            id="migrate-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        app_cfg = self._config.apps.get(self._app_name)
        current_host = app_cfg.host if app_cfg else ""
        
        hosts = [
            Option(f"{name} ({cfg.host})", id=name)
            for name, cfg in self._config.hosts.items()
            if name != current_host
        ]
        
        if not hosts:
            self.query_one("#migrate-instruction", Static).update(
                "[red]No other hosts available for migration.[/red]"
            )
            self._phase = "error"
        else:
            opt_list = self.query_one("#dest-host-list", OptionList)
            opt_list.add_options(hosts)
            opt_list.focus()

    def on_key(self, event: events.Key) -> None:
        """Force handle Enter key at the screen level if in selection phase."""
        if event.key == "enter" and self._phase == "selection":
            self.action_start_migration()
            event.stop()

    @on(OptionList.Selected, "#dest-host-list")
    def handle_option_selected(self, event: OptionList.Selected) -> None:
        """Handle Enter/Click on the option list."""
        if self._phase == "selection":
            self._dest_host = str(event.option.id)
            self._initiate_migration()

    def action_start_migration(self) -> None:
        if self._phase != "selection":
            return
            
        opt_list = self.query_one("#dest-host-list", OptionList)
        if opt_list.highlighted is not None:
            option = opt_list.get_option_at(opt_list.highlighted)
            self._dest_host = str(option.id)
            self._initiate_migration()
        else:
            self.notify("Please select a destination host first.", severity="warning")

    def _initiate_migration(self) -> None:
        """Common entry point to start migration after host is picked."""
        self._phase = "progress"
        
        # UI updates
        self.query_one("#dest-host-list").add_class("hidden")
        self.query_one("#migrate-instruction").update(f"Migrating to [cyan]{self._dest_host}[/cyan]...")
        self.query_one("#steps-container").remove_class("hidden")
        self.query_one("#migrate-log").remove_class("hidden")
        
        # Update bindings
        self._update_bindings()
        
        self._run_migration_worker()

    def _update_bindings(self) -> None:
        """Update visible bindings based on current phase."""
        if self._phase == "progress":
            self.query_one(Footer).bindings = [
                Binding("ctrl+x", "abort", "Abort", show=True),
                Binding("escape", "go_back", "Back", show=True),
            ]
        elif self._phase == "confirmation":
            self.query_one(Footer).bindings = [
                Binding("y", "confirm_removal", "Confirm & Remove Original", show=True),
                Binding("n", "finish_keep_both", "Keep Both (Finish)", show=True),
                Binding("escape", "go_back", "Back", show=True),
            ]
        elif self._phase == "done":
            self.query_one(Footer).bindings = [
                Binding("escape", "go_back", "Return", show=True),
            ]

    @work(thread=True)
    def _run_migration_worker(self) -> None:
        app_cfg = self._config.apps[self._app_name]

        def line_cb(line: str) -> None:
            self.app.call_from_thread(self._log_line, line)

        self._migrator = Migrator(
            self._config, app_cfg, self._dest_host, line_callback=line_cb
        )

        for step_name, status, message in self._migrator.run_sync():
            icon = STEP_ICONS.get(status, "•")
            self.app.call_from_thread(
                self._add_step,
                f" {icon}  {step_name}: {message}",
                f"step-{status}",
            )

        if self._migrator.state and self._migrator.state.succeeded:
            self.app.call_from_thread(self._ask_confirmation)
        else:
            self.app.call_from_thread(
                self._add_step, "\n ❌  Migration failed.", "step-failed"
            )
            self._done = True
            self._phase = "done"
            self.app.call_from_thread(self._mark_done, "Migration failed")

    def _ask_confirmation(self) -> None:
        """Switch to confirmation phase."""
        self._phase = "confirmation"
        self._update_bindings()
        self.query_one("#migrate-instruction").update(
            "[yellow]App is running on destination. Verify it works!\n"
            "Press [bold]y[/bold] to remove original, [bold]n[/bold] to keep both.[/yellow]"
        )

    def action_confirm_removal(self) -> None:
        if self._phase != "confirmation":
            return
        self._phase = "finalizing"
        self._run_finalize_worker(remove_source=True)

    def action_finish_keep_both(self) -> None:
        if self._phase != "confirmation":
            return
        self._phase = "finalizing"
        self._run_finalize_worker(remove_source=False)

    @work(thread=True)
    def _run_finalize_worker(self, remove_source: bool) -> None:
        self.app.call_from_thread(self._add_step, "Finalizing migration...", "step-running")
        
        # 1. Update config
        app_cfg = self._config.apps[self._app_name]
        app_cfg.host = self._dest_host
        save_config(self._config)
        
        # 2. Cleanup source if requested
        if remove_source:
            if self._migrator.cleanup_source():
                self.app.call_from_thread(self._add_step, "✅ Original removed successfully", "step-success")
            else:
                self.app.call_from_thread(self._add_step, "⚠️ Failed to remove original", "step-failed")
        
        self._done = True
        self._phase = "done"
        msg = "Migration complete" if remove_source else "Migration complete (source preserved)"
        self.app.call_from_thread(self._mark_done, msg)

    def _add_step(self, text: str, css_class: str) -> None:
        try:
            container = self.query_one("#steps-container", VerticalScroll)
            container.mount(Label(text, classes=f"step-row {css_class}"))
            container.scroll_end()
        except Exception:
            pass

    def _log_line(self, line: str) -> None:
        try:
            self.query_one("#migrate-log", LogViewer).append_line(line)
        except Exception:
            pass

    def _mark_done(self, message: str) -> None:
        try:
            self.query_one("#migrate-instruction").update(f"[green]{message} — press Escape to return[/green]")
            self._update_bindings()
        except Exception:
            pass

    def action_abort(self) -> None:
        if self._migrator and not self._done:
            self._migrator.abort()
            self._log_line("⛔ Abort requested…")

    def action_go_back(self) -> None:
        self.app.pop_screen()
