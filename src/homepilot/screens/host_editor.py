"""Host editor screen — add or edit a server configuration."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, Select, Static

from homepilot.config import save_config
from homepilot.models import HomePilotConfig, ProxmoxHostConfig, TrueNASHostConfig
from homepilot.providers import ProviderRegistry

_HOST_TYPES = [("TrueNAS (Docker)", "truenas"), ("Proxmox VE", "proxmox")]
_TOKEN_SOURCES = [("env var", "env"), ("keychain", "keychain"), ("inline", "inline")]
_SSL_OPTIONS = [("No (self-signed OK)", "false"), ("Yes (valid cert required)", "true")]


class HostEditorScreen(Screen):
    """Form to add or edit a host/server configuration."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", show=True),
        Binding("ctrl+t", "test_connection", "Test Connection", show=True),
        Binding("escape", "go_back", "Back", show=True),
    ]

    def __init__(
        self,
        config: HomePilotConfig,
        registry: ProviderRegistry,
        host_key: str | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._host_key = host_key
        self._existing = config.hosts.get(host_key) if host_key else None

    def compose(self) -> ComposeResult:
        ex = self._existing
        tn = ex if isinstance(ex, TrueNASHostConfig) else None
        px = ex if isinstance(ex, ProxmoxHostConfig) else None
        host_type_val = "proxmox" if px else "truenas"

        yield Header()
        yield VerticalScroll(
            Label(f"\n  {'Edit' if self._host_key else 'Add'} Server\n"),
            Static("", id="editor-status"),

            Label("  Config Name (unique key, e.g. 'truenas' or 'proxmox-home'):"),
            Input(
                value=self._host_key or "",
                id="host-key",
                placeholder="truenas",
                disabled=self._host_key is not None,
            ),

            Label("  Server Type:"),
            Select(_HOST_TYPES, value=host_type_val, id="host-type"),

            Label("  IP Address / Hostname:"),
            Input(
                value=ex.host if ex else "",
                id="host-addr",
                placeholder="192.168.1.x",
            ),

            # TrueNAS-specific fields
            Vertical(
                Label("\n  TrueNAS Settings:"),
                Label("  SSH User:"),
                Input(value=tn.user if tn else "neil", id="tn-user"),
                Label("  SSH Key Path (leave blank to use SSH agent):"),
                Input(
                    value=tn.ssh_key if tn else "",
                    id="tn-ssh-key",
                    placeholder="~/.ssh/id_rsa",
                ),
                Label("  Docker Command:"),
                Input(
                    value=tn.docker_cmd if tn else "sudo docker",
                    id="tn-docker-cmd",
                ),
                Label("  midclt Command:"),
                Input(
                    value=tn.midclt_cmd if tn else "sudo -i midclt call",
                    id="tn-midclt-cmd",
                ),
                Label("  Data Root:"),
                Input(
                    value=tn.data_root if tn else "/mnt/tank/apps",
                    id="tn-data-root",
                ),
                Label("  Backup Directory:"),
                Input(
                    value=tn.backup_dir if tn else "/tmp/homepilot-backups",
                    id="tn-backup-dir",
                ),
                Label("  Dynamic Port Range Start:"),
                Input(
                    value=str(tn.dynamic_port_range_start if tn else 30200),
                    id="tn-port-start",
                ),
                Label("  Dynamic Port Range End:"),
                Input(
                    value=str(tn.dynamic_port_range_end if tn else 30299),
                    id="tn-port-end",
                ),
                id="truenas-fields",
            ),

            # Proxmox-specific fields
            Vertical(
                Label("\n  Proxmox Settings:"),
                Label("  API Token ID (user@pve!token-name):"),
                Input(
                    value=px.token_id if px else "",
                    id="px-token-id",
                    placeholder="root@pam!homepilot",
                ),
                Label("  Token Secret (blank = read from env/keychain):"),
                Input(
                    value=px.token_secret if px else "",
                    id="px-token-secret",
                    password=True,
                ),
                Label("  Token Source:"),
                Select(
                    _TOKEN_SOURCES,
                    value=px.token_source if px else "env",
                    id="px-token-source",
                ),
                Label("  Verify SSL:"),
                Select(
                    _SSL_OPTIONS,
                    value="true" if (px and px.verify_ssl) else "false",
                    id="px-verify-ssl",
                ),
                Label("  SSH User (for SSH-based ops):"),
                Input(value=px.ssh_user if px else "root", id="px-ssh-user"),
                Label("  SSH Key Path:"),
                Input(
                    value=px.ssh_key if px else "",
                    id="px-ssh-key",
                    placeholder="~/.ssh/id_rsa",
                ),
                id="proxmox-fields",
            ),

            id="host-editor-form",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._toggle_type_fields()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "host-type":
            self._toggle_type_fields()

    def _toggle_type_fields(self) -> None:
        host_type = str(self.query_one("#host-type", Select).value)
        self.query_one("#truenas-fields").display = (host_type == "truenas")
        self.query_one("#proxmox-fields").display = (host_type == "proxmox")

    def action_save(self) -> None:
        status = self.query_one("#editor-status", Static)
        try:
            key = (self._host_key or self.query_one("#host-key", Input).value.strip())
            if not key:
                status.update("[red]Config name is required[/red]")
                return
            if not self._host_key and key in self._config.hosts:
                status.update(f"[red]'{key}' already exists[/red]")
                return

            host_type = str(self.query_one("#host-type", Select).value)
            addr = self.query_one("#host-addr", Input).value.strip()
            if not addr:
                status.update("[red]IP address / hostname is required[/red]")
                return

            if host_type == "truenas":
                host_cfg = TrueNASHostConfig(
                    host=addr,
                    user=self.query_one("#tn-user", Input).value.strip() or "neil",
                    ssh_key=self.query_one("#tn-ssh-key", Input).value.strip(),
                    docker_cmd=self.query_one("#tn-docker-cmd", Input).value.strip() or "sudo docker",
                    midclt_cmd=self.query_one("#tn-midclt-cmd", Input).value.strip() or "sudo -i midclt call",
                    data_root=self.query_one("#tn-data-root", Input).value.strip() or "/mnt/tank/apps",
                    backup_dir=self.query_one("#tn-backup-dir", Input).value.strip() or "/tmp/homepilot-backups",
                    dynamic_port_range_start=int(self.query_one("#tn-port-start", Input).value or "30200"),
                    dynamic_port_range_end=int(self.query_one("#tn-port-end", Input).value or "30299"),
                )
            else:
                host_cfg = ProxmoxHostConfig(
                    host=addr,
                    token_id=self.query_one("#px-token-id", Input).value.strip(),
                    token_secret=self.query_one("#px-token-secret", Input).value.strip(),
                    token_source=str(self.query_one("#px-token-source", Select).value),
                    verify_ssl=self.query_one("#px-verify-ssl", Select).value == "true",
                    ssh_user=self.query_one("#px-ssh-user", Input).value.strip() or "root",
                    ssh_key=self.query_one("#px-ssh-key", Input).value.strip(),
                )

            self._config.hosts[key] = host_cfg
            self._registry.register_host(key, host_cfg)
            save_config(self._config)
            status.update(f"[green]✅ '{key}' saved — use ctrl+t to test connection[/green]")

        except ValueError as exc:
            status.update(f"[red]Invalid value: {exc}[/red]")
        except Exception as exc:
            status.update(f"[red]Error: {exc}[/red]")

    def action_test_connection(self) -> None:
        status = self.query_one("#editor-status", Static)
        key = self._host_key or self.query_one("#host-key", Input).value.strip()
        if not key or key not in self._config.hosts:
            status.update("[yellow]Save the server first, then test[/yellow]")
            return
        status.update(f"  Testing connection to '{key}'…")
        self._run_test(key)

    @work(thread=True)
    def _run_test(self, key: str) -> None:
        status = self.query_one("#editor-status", Static)
        provider = self._registry.get_provider(key)
        if provider is None:
            self.app.call_from_thread(
                status.update, "[yellow]Provider not registered — save first[/yellow]"
            )
            return
        try:
            provider.connect()
            resources = provider.list_resources()
            msg = f"[green]✅ Connected — {len(resources)} resources found[/green]"
        except Exception as exc:
            msg = f"[red]❌ Connection failed: {exc}[/red]"
        self.app.call_from_thread(status.update, msg)

    def action_go_back(self) -> None:
        self.app.pop_screen()
