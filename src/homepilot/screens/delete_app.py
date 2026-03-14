"""Delete app screen — confirm removal from config, with optional server cleanup."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from homepilot.config import save_config
from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry


class DeleteAppScreen(Screen):
    """Ask the user how thoroughly to delete an app."""

    BINDINGS = [
        Binding("escape", "go_back", "Cancel", show=True),
    ]

    def __init__(
        self,
        config: HomePilotConfig,
        registry: ProviderRegistry,
        app_name: str,
    ) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._app_name = app_name
        self._done = False

    def compose(self) -> ComposeResult:
        app_cfg = self._config.apps.get(self._app_name)
        host_key = app_cfg.host if app_cfg else "unknown"
        container = app_cfg.deploy.container_name if app_cfg else self._app_name
        vol_paths = (
            [v.host for v in app_cfg.volumes] if app_cfg else []
        )
        vol_info = "\n".join(f"    {p}" for p in vol_paths) if vol_paths else "    (none)"

        yield Header()
        yield Vertical(
            Label(f"\n  Delete: {self._app_name}\n", id="del-title"),
            Label(f"  Host:       {host_key}"),
            Label(f"  Container:  {container}"),
            Label(f"  Volumes:\n{vol_info}\n"),
            Static("", id="del-status"),
            Label("  Choose how to remove this app:\n"),
            Button(
                "  Config only         — remove from HomePilot, leave server untouched",
                id="btn-config-only",
                variant="default",
            ),
            Button(
                "  Stop & remove       — stop + delete the container (keep volumes)",
                id="btn-stop-remove",
                variant="warning",
            ),
            Button(
                "  Full cleanup        — stop container + delete volumes on server",
                id="btn-full-cleanup",
                variant="error",
            ),
            Button("  Cancel", id="btn-cancel", variant="default"),
            Label(""),
            VerticalScroll(id="del-log"),
            id="del-body",
        )
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._done:
            return
        btn_id = event.button.id

        if btn_id == "btn-cancel":
            self.action_go_back()
            return

        cleanup_volumes = btn_id == "btn-full-cleanup"
        cleanup_server = btn_id in ("btn-stop-remove", "btn-full-cleanup")

        self._done = True
        self._run_delete(cleanup_server=cleanup_server, cleanup_volumes=cleanup_volumes)

    @work(thread=True)
    def _run_delete(self, *, cleanup_server: bool, cleanup_volumes: bool) -> None:
        app_cfg = self._config.apps.get(self._app_name)
        if app_cfg is None:
            self.app.call_from_thread(
                self.query_one("#del-status", Static).update,
                "[red]App not found in config.[/red]",
            )
            return

        if cleanup_server:
            self._server_cleanup(app_cfg, remove_volumes=cleanup_volumes)

        # Remove from config regardless
        del self._config.apps[self._app_name]
        save_config(self._config)

        label = "config + server" if cleanup_server else "config"
        vol_label = " + volumes" if cleanup_volumes else ""
        self.app.call_from_thread(
            self.query_one("#del-status", Static).update,
            f"[green]✅ '{self._app_name}' removed from {label}{vol_label}. Press Escape.[/green]",
        )

    def _server_cleanup(self, app_cfg, *, remove_volumes: bool) -> None:
        from homepilot.providers.proxmox import ProxmoxProvider
        from homepilot.providers.truenas import TrueNASProvider

        host_key = app_cfg.host or next(iter(self._config.hosts), "")
        provider = self._registry.get_provider(host_key)

        def log(msg: str) -> None:
            self.app.call_from_thread(self._append_log, msg)

        if isinstance(provider, ProxmoxProvider):
            self._cleanup_proxmox(provider, app_cfg, remove_volumes=remove_volumes, log=log)
        elif isinstance(provider, TrueNASProvider):
            self._cleanup_truenas(provider, app_cfg, remove_volumes=remove_volumes, log=log)
        else:
            log(f"⚠️  No SSH provider for '{host_key}' — skipping server cleanup")

    def _cleanup_proxmox(self, provider, app_cfg, *, remove_volumes: bool, log) -> None:
        from homepilot.services.ssh import SSHService
        from homepilot.services.remote_state import RemoteStateService

        server_cfg = provider._config.to_server_config()
        ssh = SSHService(server_cfg)
        try:
            ssh.connect()
            log(f"Connected to {provider._config.host}")

            container = app_cfg.deploy.container_name

            # Determine docker command
            _, _, code = ssh.run_command("docker --version")
            docker = "docker" if code == 0 else "sudo docker"

            # Stop and remove container
            _, _, code = ssh.run_command(f"{docker} inspect {container}")
            if code == 0:
                ssh.run_command(f"{docker} stop {container}", timeout=30)
                ssh.run_command(f"{docker} rm {container}")
                log(f"Container '{container}' stopped and removed")
            else:
                log(f"Container '{container}' not found — skipping stop")

            # Remove volumes if requested
            if remove_volumes:
                for vol in app_cfg.volumes:
                    _, err, code = ssh.run_command(f"rm -rf {vol.host}")
                    if code == 0:
                        log(f"Removed volume: {vol.host}")
                    else:
                        log(f"⚠️  Could not remove {vol.host}: {err.strip()}")

            # Update remote state
            state_svc = RemoteStateService(ssh, host_key=provider.name)
            state_svc.remove_app(app_cfg.name)
            log("Remote state updated")

        except Exception as exc:
            log(f"❌ Cleanup error: {exc}")
        finally:
            try:
                ssh.close()
            except Exception:
                pass

    def _cleanup_truenas(self, provider, app_cfg, *, remove_volumes: bool, log) -> None:
        truenas = provider.truenas
        if truenas is None:
            log("⚠️  TrueNAS not connected — skipping server cleanup")
            return

        container = app_cfg.deploy.container_name
        if truenas.container_exists(container):
            truenas.stop_container(container)
            truenas.remove_container(container)
            log(f"Container '{container}' stopped and removed")
        else:
            log(f"Container '{container}' not found")

    def _append_log(self, line: str) -> None:
        try:
            log = self.query_one("#del-log", VerticalScroll)
            log.mount(Label(f"  {line}"))
            log.scroll_end()
        except Exception:
            pass

    def action_go_back(self) -> None:
        self.app.pop_screen()
