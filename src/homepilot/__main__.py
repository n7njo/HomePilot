"""CLI entry point for HomePilot."""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from homepilot import __version__

console = Console()


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="HomePilot")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """HomePilot — Home Lab Manager."""
    if ctx.invoked_subcommand is None:
        _launch_tui()


def _launch_tui() -> None:
    """Start the Textual TUI application."""
    from homepilot.app import HomePilotApp

    app = HomePilotApp()
    app.run()


def _build_registry():
    """Load config and build a ProviderRegistry (shared helper for CLI commands)."""
    from homepilot.config import load_config
    from homepilot.providers import ProviderRegistry

    config = load_config()
    registry = ProviderRegistry(config)
    return config, registry


# ------------------------------------------------------------------
# homepilot status
# ------------------------------------------------------------------


@cli.command()
@click.option("--host", "-h", default=None, help="Filter to a specific host.")
def status(host: str | None) -> None:
    """Show all resources across all connected hosts."""
    config, registry = _build_registry()

    console.print("[bold]Connecting to hosts…[/bold]")
    registry.connect_all()

    table = Table(title="HomePilot — Resource Status")
    table.add_column("Host", style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Port/VMID")
    table.add_column("Uptime")
    table.add_column("Info")

    from homepilot.providers.base import ResourceStatus as RS

    resources = registry.list_all_resources()
    if host:
        resources = [r for r in resources if r.provider_name == host]

    if not resources:
        console.print("[dim]No resources found.[/dim]")
    else:
        for r in resources:
            style = "green" if r.status == RS.RUNNING else "red" if r.status == RS.STOPPED else "dim"
            port_col = str(r.port) if r.port else "—"
            table.add_row(
                r.provider_name,
                r.name,
                r.resource_type.value,
                f"[{style}]{r.status.value}[/{style}]",
                port_col,
                r.uptime or "—",
                r.image or "",
            )
        console.print(table)

    registry.disconnect_all()


# ------------------------------------------------------------------
# homepilot hosts  (backwards-compat top-level command)
# ------------------------------------------------------------------


@cli.command()
def hosts() -> None:
    """List configured hosts and test connectivity."""
    config, registry = _build_registry()

    table = Table(title="HomePilot — Hosts")
    table.add_column("Name", style="bold cyan")
    table.add_column("Type")
    table.add_column("Address")
    table.add_column("Status")

    for key, provider in registry.providers.items():
        try:
            provider.connect()
            connected = provider.is_connected()
            status_str = "[green]● Connected[/green]" if connected else "[red]○ Failed[/red]"
        except Exception as exc:
            status_str = f"[red]○ {exc}[/red]"

        table.add_row(
            key,
            provider.provider_type,
            provider.host_display,
            status_str,
        )

    console.print(table)
    registry.disconnect_all()


# ------------------------------------------------------------------
# homepilot host  (subcommand group)
# ------------------------------------------------------------------


def _print_hosts_table(registry) -> None:
    """Shared helper: print a hosts connectivity table and disconnect."""
    table = Table(title="HomePilot — Hosts")
    table.add_column("Name", style="bold cyan")
    table.add_column("Type")
    table.add_column("Address")
    table.add_column("Status")

    for key, provider in registry.providers.items():
        try:
            provider.connect()
            connected = provider.is_connected()
            status_str = "[green]● Connected[/green]" if connected else "[red]○ Failed[/red]"
        except Exception as exc:
            status_str = f"[red]○ {exc}[/red]"

        table.add_row(key, provider.provider_type, provider.host_display, status_str)

    console.print(table)
    registry.disconnect_all()


@cli.group(name="host")
def host_group() -> None:
    """Manage HomePilot hosts."""


@host_group.command(name="list")
def host_list() -> None:
    """List configured hosts and test connectivity."""
    _config, registry = _build_registry()
    _print_hosts_table(registry)


@host_group.command(name="add")
def host_add() -> None:
    """Interactively add a new host to configuration."""
    from homepilot.config import load_config, save_config
    from homepilot.models import TrueNASHostConfig, ProxmoxHostConfig

    config = load_config()

    name = click.prompt("Config name (unique key, e.g. 'truenas' or 'proxmox-home')")
    if name in config.hosts:
        console.print(
            f"[red]Host '{name}' already exists. Use 'host delete' first or choose a different name.[/red]"
        )
        sys.exit(1)

    host_type = click.prompt(
        "Host type",
        type=click.Choice(["truenas", "proxmox"]),
        default="truenas",
    )
    address = click.prompt("IP address / hostname")

    if host_type == "truenas":
        ssh_user = click.prompt("SSH user", default="neil")
        admin_user = click.prompt("Admin user (original admin for bootstrap)", default="")
        docker_cmd = click.prompt("Docker command", default="/usr/bin/docker")
        data_root = click.prompt("Data root", default="/mnt/SixNine/homepilot")

        host_cfg: TrueNASHostConfig | ProxmoxHostConfig = TrueNASHostConfig(
            type="truenas",
            host=address,
            user=ssh_user,
            admin_user=admin_user,
            docker_cmd=docker_cmd,
            data_root=data_root,
        )
    else:
        token_id = click.prompt("API token ID (e.g. user@pve!token-name)", default="")
        token_secret = click.prompt("API token secret", default="", hide_input=True)
        verify_ssl = click.confirm("Require valid SSL certificate?", default=False)

        host_cfg = ProxmoxHostConfig(
            type="proxmox",
            host=address,
            token_id=token_id,
            token_secret=token_secret,
            verify_ssl=verify_ssl,
        )

    config.hosts[name] = host_cfg
    save_config(config)
    console.print(f"[green]Host '{name}' ({host_type}) saved.[/green]")

    if click.confirm("Test connectivity now?", default=True):
        from homepilot.providers import ProviderRegistry

        registry = ProviderRegistry(config)
        provider = registry.get_provider(name)
        if provider is None:
            console.print("[red]No provider registered for this host type.[/red]")
            return
        try:
            provider.connect()
            if provider.is_connected():
                console.print(f"[green]● Connected to '{name}' successfully.[/green]")
            else:
                console.print(
                    f"[red]○ Connection to '{name}' failed (provider reported not connected).[/red]"
                )
            provider.disconnect()
        except Exception as exc:
            console.print(f"[red]○ Connection failed: {exc}[/red]")


@host_group.command(name="delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
def host_delete(name: str, yes: bool) -> None:
    """Remove a host from configuration."""
    from homepilot.config import load_config, save_config

    config = load_config()

    if name not in config.hosts:
        console.print(f"[red]Host '{name}' not found.[/red]")
        console.print(f"Known hosts: {', '.join(config.hosts.keys()) or '(none)'}")
        sys.exit(1)

    host_cfg = config.hosts[name]

    if not yes:
        console.print(
            f"Host to delete: [bold]{name}[/bold] (type: {host_cfg.type}, address: {host_cfg.host})"
        )
        if not click.confirm("Are you sure you want to delete this host?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            return

    del config.hosts[name]
    save_config(config)
    console.print(f"[green]Host '{name}' deleted.[/green]")

    # Warn about orphaned apps
    orphaned = [app_name for app_name, app in config.apps.items() if app.host == name]
    if orphaned:
        console.print(
            f"[yellow]Warning: {len(orphaned)} app(s) still reference '{name}': "
            f"{', '.join(orphaned)}[/yellow]"
        )


@host_group.command(name="test")
@click.argument("name", required=False, default=None)
def host_test(name: str | None) -> None:
    """Test connectivity to one or all hosts.

    Pass NAME to test a single host, or omit to test all configured hosts.
    """
    from homepilot.providers import ProviderRegistry
    from homepilot.config import load_config

    config = load_config()
    registry = ProviderRegistry(config)

    if name is not None and name not in config.hosts:
        console.print(f"[red]Host '{name}' not found.[/red]")
        console.print(f"Known hosts: {', '.join(config.hosts.keys()) or '(none)'}")
        sys.exit(1)

    targets = {name: registry.get_provider(name)} if name else dict(registry.providers)

    table = Table(title="HomePilot — Connectivity Test")
    table.add_column("Name", style="bold cyan")
    table.add_column("Type")
    table.add_column("Address")
    table.add_column("Status")

    for key, provider in targets.items():
        if provider is None:
            table.add_row(key, "—", "—", "[red]○ No provider[/red]")
            continue
        try:
            provider.connect()
            if provider.is_connected():
                status_str = "[green]● Connected[/green]"
            else:
                status_str = "[red]○ Failed[/red]"
            provider.disconnect()
        except Exception as exc:
            status_str = f"[red]○ {exc}[/red]"

        table.add_row(key, provider.provider_type, provider.host_display, status_str)

    console.print(table)


@host_group.command(name="bootstrap")
@click.argument("name")
@click.option(
    "--root-user",
    default="root",
    show_default=True,
    help="The admin/root SSH user to connect with for provisioning.",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Stream raw log output from each bootstrap step.")
def host_bootstrap(name: str, root_user: str, verbose: bool) -> None:
    """Run the bootstrap provisioning pipeline on a host."""
    from homepilot.config import load_config, save_config
    from homepilot.services.bootstrap import make_bootstrap_service, HOMEPILOT_USER
    from homepilot.models import TrueNASHostConfig, ProxmoxHostConfig
    from homepilot.providers import ProviderRegistry

    config = load_config()

    if name not in config.hosts:
        console.print(f"[red]Host '{name}' not found.[/red]")
        console.print(f"Known hosts: {', '.join(config.hosts.keys()) or '(none)'}")
        sys.exit(1)

    host_cfg = config.hosts[name]

    console.print(f"[bold]Bootstrapping '{name}' ({host_cfg.type}) as {root_user}...[/bold]")

    def line_cb(line: str) -> None:
        if verbose:
            console.print(f"    [dim]{line}[/dim]")

    try:
        bootstrapper = make_bootstrap_service(
            host_cfg,
            root_user=root_user,
            line_callback=line_cb,
        )
    except RuntimeError as exc:
        console.print(f"[red]Cannot bootstrap this host type: {exc}[/red]")
        sys.exit(1)

    step_icons = {
        "running": "...",
        "success": "OK ",
        "failed": "ERR",
        "skipped": "---",
    }

    succeeded = True
    for step_name, step_status, message in bootstrapper.run_sync():
        icon = step_icons.get(step_status, "   ")
        color = "green" if step_status == "success" else "red" if step_status == "failed" else "dim"
        console.print(f"  [{color}][{icon}][/{color}] {step_name}: {message}")
        if step_status == "failed":
            succeeded = False

    if succeeded:
        # Mirror what BootstrapScreen does: update SSH user in config to HOMEPILOT_USER
        host_cfg = config.hosts.get(name)
        if isinstance(host_cfg, (TrueNASHostConfig, ProxmoxHostConfig)):
            if isinstance(host_cfg, ProxmoxHostConfig):
                host_cfg.ssh_user = HOMEPILOT_USER
            else:
                if not host_cfg.admin_user:
                    host_cfg.admin_user = host_cfg.user
                host_cfg.user = HOMEPILOT_USER

                # Update data_root if bootstrapper discovered the actual pool
                from homepilot.services.bootstrap import TrueNASBootstrapService

                if isinstance(bootstrapper, TrueNASBootstrapService):
                    pool = bootstrapper.pool_root
                    if pool and pool != "/mnt/tank":
                        host_cfg.data_root = f"{pool}/apps"

            save_config(config)
            registry = ProviderRegistry(config)
            registry.register_host(name, host_cfg)

        console.print(
            f"\n[green]Bootstrap complete — future SSH connections use '{HOMEPILOT_USER}'.[/green]"
        )
    else:
        console.print("\n[red]Bootstrap failed — see errors above. Fix issues and re-run.[/red]")
        sys.exit(1)


# ------------------------------------------------------------------
# homepilot deploy <app>
# ------------------------------------------------------------------


@cli.command()
@click.argument("app_name")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Stream raw log output from each deploy step.")
def deploy(app_name: str, verbose: bool) -> None:
    """Deploy a Docker app to its configured host."""
    from homepilot.config import load_config, validate_config
    from homepilot.services.deployer import Deployer

    config = load_config()
    errors = validate_config(config)
    if errors:
        console.print("[red]Configuration errors:[/red]")
        for e in errors:
            console.print(f"  • {e}")
        sys.exit(1)

    if app_name not in config.apps:
        console.print(f"[red]Unknown app:[/red] {app_name}")
        console.print(f"Available: {', '.join(config.apps.keys())}")
        sys.exit(1)

    app_config = config.apps[app_name]

    # Resolve the correct server config for this app's host
    from homepilot.providers import ProviderRegistry
    from homepilot.providers.truenas import TrueNASProvider

    registry = ProviderRegistry(config)
    host_key = app_config.host or next(iter(config.hosts), "")
    provider = registry.get_provider(host_key)

    if provider and isinstance(provider, TrueNASProvider):
        server_config = provider._config.to_server_config()
    else:
        server_config = config.server

    def line_cb(line: str) -> None:
        if verbose:
            console.print(f"    [dim]{line}[/dim]")

    deployer = Deployer(server_config, app_config, line_callback=line_cb)

    console.print(f"[bold]Deploying {app_name} to {host_key}…[/bold]")
    try:
        for step_name, step_status, message in deployer.run_sync():
            icon = {"running": "⏳", "success": "✅", "failed": "❌", "skipped": "⏭️"}.get(
                step_status, "•"
            )
            console.print(f"  {icon} {step_name}: {message}")

        if deployer.state and deployer.state.succeeded:
            from datetime import datetime, timezone
            from homepilot.config import save_config

            app_config.last_deployed = datetime.now(timezone.utc).isoformat()
            save_config(config)
            console.print(f"\n[green]✅ {app_name} deployed successfully![/green]")
        else:
            console.print("\n[red]❌ Deployment failed.[/red]")
            sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Deployment aborted by user.[/yellow]")
        sys.exit(130)


# ------------------------------------------------------------------
# homepilot config
# ------------------------------------------------------------------


@cli.command(name="config")
def config_cmd() -> None:
    """Show current configuration."""
    from homepilot.config import load_config, CONFIG_FILE, validate_config
    from homepilot.models import TrueNASHostConfig, ProxmoxHostConfig

    config = load_config()
    errors = validate_config(config)

    console.print(f"[bold]Config file:[/bold] {CONFIG_FILE}")
    console.print(f"[bold]Hosts:[/bold] {len(config.hosts)}")

    for key, host_cfg in config.hosts.items():
        console.print(f"\n  [cyan]{key}[/cyan] ({host_cfg.type})")
        console.print(f"    Address: {host_cfg.host}")
        if isinstance(host_cfg, TrueNASHostConfig):
            console.print(f"    User:    {host_cfg.user}")
            console.print(f"    Docker:  {host_cfg.docker_cmd}")
        elif isinstance(host_cfg, ProxmoxHostConfig):
            console.print(f"    Token:   {host_cfg.token_id or '(not set)'}")
            console.print(f"    Source:  {host_cfg.token_source}")

    console.print(f"\n[bold]Apps:[/bold] {len(config.apps)}")
    for name, app in config.apps.items():
        host_label = f" → {app.host}" if app.host else ""
        console.print(f"\n  [cyan]{name}[/cyan]{host_label}")
        console.print(f"    Source: {app.source.type.value} — {app.source.path or app.source.git_url}")
        console.print(f"    Image:  {app.deploy.image_name}")
        console.print(f"    Port:   {app.deploy.host_port} → {app.deploy.container_port}")

    if errors:
        console.print("\n[red]Validation errors:[/red]")
        for e in errors:
            console.print(f"  • {e}")


# ------------------------------------------------------------------
# homepilot add
# ------------------------------------------------------------------


@cli.command()
@click.argument("app_name")
@click.option("--host", "host_key", default=None, help="Target host name from config.")
@click.option("--image", default=None, help="Docker image name.")
@click.option(
    "--port",
    "port_mapping",
    default=None,
    metavar="HOST_PORT:CONTAINER_PORT",
    help="Port mapping, e.g. 8080:5000.",
)
@click.option("--source-path", default=None, help="Local source path (for local source type).")
@click.option("--git-url", default=None, help="Git repository URL (for git source type).")
@click.option(
    "--health-protocol",
    default=None,
    type=click.Choice(["http", "tcp"]),
    help="Health check protocol.",
)
@click.option("--health-endpoint", default=None, help="Health check HTTP endpoint path.")
@click.option("--container-name", default=None, help="Docker container name override.")
def add(
    app_name: str,
    host_key: str | None,
    image: str | None,
    port_mapping: str | None,
    source_path: str | None,
    git_url: str | None,
    health_protocol: str | None,
    health_endpoint: str | None,
    container_name: str | None,
) -> None:
    """Register a new app.

    If --host and --image are provided the app is saved directly without opening the TUI.
    If either flag is missing and stdin is a terminal, the TUI is launched instead.
    """
    # If the required flags for non-interactive mode are absent and we have a TTY, fall back to TUI.
    if not (host_key and image):
        if sys.stdin.isatty():
            _launch_tui()
            return
        else:
            console.print(
                "[red]Error: --host and --image are required in non-interactive mode.[/red]"
            )
            sys.exit(1)

    # Non-interactive path
    from homepilot.config import load_config, save_config
    from homepilot.models import (
        AppConfig,
        BuildConfig,
        DeployConfig,
        HealthConfig,
        HealthProtocol,
        PortMode,
        SourceConfig,
        SourceType,
    )

    config = load_config()

    if host_key not in config.hosts:
        console.print(f"[red]Unknown host:[/red] '{host_key}'")
        console.print(f"Known hosts: {', '.join(config.hosts.keys()) or '(none)'}")
        sys.exit(1)

    if app_name in config.apps:
        console.print(f"[yellow]Warning: app '{app_name}' already exists and will be overwritten.[/yellow]")
        if not click.confirm("Overwrite?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            return

    # Parse port mapping
    host_port = 0
    c_port = 5000
    if port_mapping:
        parts = port_mapping.split(":")
        if len(parts) != 2:
            console.print(
                f"[red]Invalid --port format '{port_mapping}'. Expected HOST_PORT:CONTAINER_PORT.[/red]"
            )
            sys.exit(1)
        try:
            host_port = int(parts[0])
            c_port = int(parts[1])
        except ValueError:
            console.print(f"[red]Port values must be integers, got '{port_mapping}'.[/red]")
            sys.exit(1)

    # Determine source type
    src_type = SourceType.GIT if git_url else SourceType.LOCAL

    # Health protocol
    h_protocol = HealthProtocol(health_protocol) if health_protocol else HealthProtocol.HTTP
    h_endpoint = health_endpoint or "/api/health"

    app_cfg = AppConfig(
        name=app_name,
        host=host_key,
        source=SourceConfig(
            type=src_type,
            path=source_path or "",
            git_url=git_url or "",
        ),
        build=BuildConfig(),
        deploy=DeployConfig(
            image_name=image,
            container_name=container_name or f"{app_name}-app",
            host_port=host_port,
            container_port=c_port,
            port_mode=PortMode.FIXED if host_port else PortMode.DYNAMIC,
        ),
        health=HealthConfig(
            protocol=h_protocol,
            endpoint=h_endpoint,
        ),
    )

    config.apps[app_name] = app_cfg
    save_config(config)

    console.print(f"[green]App '{app_name}' saved.[/green]")
    console.print(f"  Host:      {host_key}")
    console.print(f"  Image:     {image}")
    console.print(f"  Container: {app_cfg.deploy.container_name}")
    if host_port:
        console.print(f"  Port:      {host_port} -> {c_port}")
    else:
        console.print(f"  Port:      dynamic -> {c_port}")
    if git_url:
        console.print(f"  Source:    git -- {git_url}")
    elif source_path:
        console.print(f"  Source:    local -- {source_path}")
    console.print(f"  Health:    {h_protocol.value} {h_endpoint}")


# ------------------------------------------------------------------
# Shared helper — resolve a resource by name across providers
# ------------------------------------------------------------------


def _find_resource(name: str, host: str | None, registry):
    """Return (provider, resource) matching *name*, optionally filtered by *host*.

    Connects to each provider.  Returns (None, None) when not found.
    """
    from homepilot.providers.base import Resource

    for key, provider in registry.providers.items():
        if host and key != host:
            continue
        try:
            provider.connect()
        except Exception as exc:
            console.print(f"[yellow]  Warning: could not connect to {key}: {exc}[/yellow]")
            continue
        resource = provider.get_resource(name)
        if resource is not None:
            return provider, resource

    return None, None


# ------------------------------------------------------------------
# homepilot start / stop / restart <name> [--host HOST]
# ------------------------------------------------------------------


@cli.command(name="start")
@click.argument("name")
@click.option("--host", "-h", default=None, help="Restrict search to this host key.")
def start_cmd(name: str, host: str | None) -> None:
    """Start a container by name."""
    config, registry = _build_registry()
    console.print(f"[bold]Looking up '{name}'…[/bold]")
    provider, resource = _find_resource(name, host, registry)

    if provider is None:
        console.print(f"[red]Resource '{name}' not found.[/red]")
        registry.disconnect_all()
        sys.exit(1)

    console.print(f"  Found on [cyan]{resource.provider_name}[/cyan] — starting…")
    ok = provider.start(name)
    registry.disconnect_all()

    if ok:
        console.print(f"[green]'{name}' started.[/green]")
    else:
        console.print(f"[red]Failed to start '{name}'.[/red]")
        sys.exit(1)


@cli.command(name="stop")
@click.argument("name")
@click.option("--host", "-h", default=None, help="Restrict search to this host key.")
def stop_cmd(name: str, host: str | None) -> None:
    """Stop a container by name."""
    config, registry = _build_registry()
    console.print(f"[bold]Looking up '{name}'…[/bold]")
    provider, resource = _find_resource(name, host, registry)

    if provider is None:
        console.print(f"[red]Resource '{name}' not found.[/red]")
        registry.disconnect_all()
        sys.exit(1)

    console.print(f"  Found on [cyan]{resource.provider_name}[/cyan] — stopping…")
    ok = provider.stop(name)
    registry.disconnect_all()

    if ok:
        console.print(f"[green]'{name}' stopped.[/green]")
    else:
        console.print(f"[red]Failed to stop '{name}'.[/red]")
        sys.exit(1)


@cli.command(name="restart")
@click.argument("name")
@click.option("--host", "-h", default=None, help="Restrict search to this host key.")
def restart_cmd(name: str, host: str | None) -> None:
    """Restart a container by name."""
    config, registry = _build_registry()
    console.print(f"[bold]Looking up '{name}'…[/bold]")
    provider, resource = _find_resource(name, host, registry)

    if provider is None:
        console.print(f"[red]Resource '{name}' not found.[/red]")
        registry.disconnect_all()
        sys.exit(1)

    console.print(f"  Found on [cyan]{resource.provider_name}[/cyan] — restarting…")
    ok = provider.restart(name)
    registry.disconnect_all()

    if ok:
        console.print(f"[green]'{name}' restarted.[/green]")
    else:
        console.print(f"[red]Failed to restart '{name}'.[/red]")
        sys.exit(1)


# ------------------------------------------------------------------
# homepilot logs <name> [--host HOST] [--follow] [--tail N]
# ------------------------------------------------------------------


@cli.command(name="logs")
@click.argument("name")
@click.option("--host", "-h", default=None, help="Restrict search to this host key.")
@click.option("--follow", "-f", is_flag=True, default=False, help="Stream logs live.")
@click.option("--tail", "-n", default=100, show_default=True, help="Number of recent lines to show.")
def logs_cmd(name: str, host: str | None, follow: bool, tail: int) -> None:
    """Fetch or stream container logs."""
    config, registry = _build_registry()
    provider, resource = _find_resource(name, host, registry)

    if provider is None:
        console.print(f"[red]Resource '{name}' not found.[/red]")
        registry.disconnect_all()
        sys.exit(1)

    if not follow:
        # Tail-only — use the provider's built-in logs() method
        output = provider.logs(name, lines=tail)
        registry.disconnect_all()
        if output:
            print(output, end="")
        return

    # Follow mode — stream via SSH directly.
    # provider.logs() buffers all output; we need to call docker logs -f.
    from homepilot.providers.truenas import TrueNASProvider

    if isinstance(provider, TrueNASProvider):
        truenas_svc = provider.truenas
        if truenas_svc is None:
            console.print("[red]Not connected to TrueNAS host.[/red]")
            registry.disconnect_all()
            sys.exit(1)

        docker_cmd = provider._config.docker_cmd
        cmd = f"{docker_cmd} logs --tail {tail} -f {name}"

        try:
            truenas_svc._ssh.run_command_stream(
                cmd,
                line_callback=lambda line: print(line, flush=True),
            )
        except KeyboardInterrupt:
            pass  # User pressed Ctrl-C — normal exit
    else:
        # Fallback: non-streaming tail for unsupported providers
        output = provider.logs(name, lines=tail)
        if output:
            print(output, end="")

    registry.disconnect_all()


# ------------------------------------------------------------------
# homepilot delete <name> [--level 1|2|3] [--yes]
# ------------------------------------------------------------------


@cli.command(name="delete")
@click.argument("name")
@click.option(
    "--level",
    type=click.Choice(["1", "2", "3"]),
    default=None,
    help=(
        "1=config only (default), "
        "2=stop+remove container (keep volumes), "
        "3=full cleanup (stop+remove container+delete volumes)"
    ),
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
def delete_cmd(name: str, level: str | None, yes: bool) -> None:
    """Delete an app from HomePilot.

    Level 1 removes it from config only.
    Level 2 also stops and removes the container.
    Level 3 additionally deletes all volume data on the server.
    """
    config, registry = _build_registry()

    if name not in config.apps:
        console.print(f"[red]App '{name}' not found in HomePilot config.[/red]")
        console.print(f"Available apps: {', '.join(config.apps.keys()) or '(none)'}")
        sys.exit(1)

    app_cfg = config.apps[name]
    container = app_cfg.deploy.container_name or name
    host_key = app_cfg.host or next(iter(config.hosts), "(none)")
    vol_paths = [v.host for v in app_cfg.volumes if v.host]

    if level is None:
        level = "1"
        console.print(
            "[dim]--level not specified; defaulting to level 1 (config only).[/dim]"
        )

    level_int = int(level)
    level_labels = {
        1: "Remove from HomePilot config only (server untouched)",
        2: "Stop + remove container on server (volumes kept)",
        3: "Stop + remove container + delete volume data on server",
    }

    # Show what will happen
    console.print(f"\n[bold]Delete:[/bold] {name}")
    console.print(f"  Host:      {host_key}")
    console.print(f"  Container: {container}")
    if vol_paths:
        console.print("  Volumes:")
        for p in vol_paths:
            console.print(f"    {p}")
    else:
        console.print("  Volumes:   (none)")
    console.print(f"  Action:    [yellow]{level_labels[level_int]}[/yellow]\n")

    if not yes:
        click.confirm("Proceed?", abort=True)

    cleanup_server = level_int >= 2
    cleanup_volumes = level_int >= 3

    if cleanup_server:
        from homepilot.providers.truenas import TrueNASProvider
        from homepilot.providers.proxmox import ProxmoxProvider

        provider = registry.get_provider(host_key)
        if provider is None:
            console.print(f"[yellow]  No provider configured for '{host_key}' — skipping server cleanup.[/yellow]")
        else:
            try:
                provider.connect()
            except Exception as exc:
                console.print(f"[red]  Could not connect to {host_key}: {exc}[/red]")
                console.print("[yellow]  Skipping server cleanup.[/yellow]")
                provider = None

        if provider is not None:
            if isinstance(provider, TrueNASProvider):
                truenas_svc = provider.truenas
                if truenas_svc:
                    if truenas_svc.container_exists(container):
                        truenas_svc.stop_container(container)
                        truenas_svc.remove_container(container)
                        console.print(f"  Container '{container}' stopped and removed.")
                    else:
                        console.print(f"  [dim]Container '{container}' not found on server — skipping.[/dim]")

                    if cleanup_volumes:
                        for vol in app_cfg.volumes:
                            if vol.host:
                                _, err, code = provider.ssh.run_command(f"rm -rf {vol.host}")
                                if code == 0:
                                    console.print(f"  Removed volume: {vol.host}")
                                else:
                                    console.print(f"  [yellow]Could not remove {vol.host}: {err.strip()}[/yellow]")

            elif isinstance(provider, ProxmoxProvider):
                from homepilot.services.ssh import SSHService

                server_cfg = provider._config.to_server_config()
                ssh = SSHService(server_cfg)
                try:
                    ssh.connect()
                    _, _, code = ssh.run_command(f"docker inspect {container}")
                    docker = "docker" if code == 0 else "sudo docker"
                    _, _, code = ssh.run_command(f"docker inspect {container}")
                    if code == 0:
                        ssh.run_command(f"{docker} stop {container}", timeout=30)
                        ssh.run_command(f"{docker} rm {container}")
                        console.print(f"  Container '{container}' stopped and removed.")
                    else:
                        console.print(f"  [dim]Container '{container}' not found — skipping.[/dim]")

                    if cleanup_volumes:
                        for vol in app_cfg.volumes:
                            if vol.host:
                                _, err, code = ssh.run_command(f"rm -rf {vol.host}")
                                if code == 0:
                                    console.print(f"  Removed volume: {vol.host}")
                                else:
                                    console.print(f"  [yellow]Could not remove {vol.host}: {err.strip()}[/yellow]")
                except Exception as exc:
                    console.print(f"  [red]Server cleanup error: {exc}[/red]")
                finally:
                    try:
                        ssh.close()
                    except Exception:
                        pass
            else:
                console.print(f"  [yellow]Provider type not supported for server cleanup — skipping.[/yellow]")

    # Remove from config
    del config.apps[name]
    from homepilot.config import save_config
    save_config(config)
    console.print(f"\n[green]'{name}' removed from HomePilot config.[/green]")

    registry.disconnect_all()


# ------------------------------------------------------------------
# homepilot import-config <container_name> --host HOST [--save]
# ------------------------------------------------------------------


@cli.command(name="import-config")
@click.argument("container_name")
@click.option("--host", "-h", required=True, help="Host key to connect to.")
@click.option("--save", is_flag=True, default=False, help="Register as a managed app in HomePilot config.")
def import_config_cmd(container_name: str, host: str, save: bool) -> None:
    """Inspect a running container and extract its configuration as YAML.

    Prints the extracted config to stdout.  With --save, also registers
    it as a new managed app in the HomePilot configuration file.
    """
    import yaml

    config, registry = _build_registry()

    provider = registry.get_provider(host)
    if provider is None:
        console.print(f"[red]No host '{host}' in config.[/red]")
        console.print(f"Configured hosts: {', '.join(registry.providers.keys()) or '(none)'}")
        sys.exit(1)

    try:
        provider.connect()
    except Exception as exc:
        console.print(f"[red]Could not connect to '{host}': {exc}[/red]")
        sys.exit(1)

    from homepilot.providers.truenas import TrueNASProvider

    if not isinstance(provider, TrueNASProvider):
        console.print(f"[red]import-config is only supported for TrueNAS hosts (host '{host}' is {provider.provider_type}).[/red]")
        registry.disconnect_all()
        sys.exit(1)

    inspect = provider.extract_app_config(container_name)
    if not inspect:
        console.print(f"[red]Could not inspect container '{container_name}' on '{host}'.[/red]")
        console.print("  Ensure the container name is correct and the host is reachable.")
        registry.disconnect_all()
        sys.exit(1)

    # Print the extracted config as YAML
    print(yaml.dump(inspect, default_flow_style=False, sort_keys=False), end="")

    if save:
        from homepilot.models import (
            AppConfig,
            BuildConfig,
            DeployConfig,
            HealthConfig,
            HealthProtocol,
            PortMode,
            AccessLevel,
            NetworkMode,
            SourceConfig,
            SourceType,
            VolumeMount,
            HistoryEventType,
            AppHistoryEvent,
        )
        from homepilot.config import save_config
        from datetime import datetime, timezone

        suggested_name = container_name.removesuffix("-app") if container_name.endswith("-app") else container_name

        if suggested_name in config.apps:
            console.print(
                f"[red]App '{suggested_name}' already exists in config — skipping save.[/red]"
            )
            registry.disconnect_all()
            sys.exit(1)

        host_port = inspect.get("host_port") or 0
        container_port = inspect.get("container_port") or 80
        image_name = inspect.get("image_name") or suggested_name
        volumes = [
            VolumeMount(host=v["host"], container=v["container"])
            for v in inspect.get("volumes", [])
            if v.get("host") and v.get("container")
        ]
        env: dict[str, str] = dict(inspect.get("env") or {})

        app = AppConfig(
            name=suggested_name,
            host=host,
            source=SourceConfig(type=SourceType.LOCAL),
            build=BuildConfig(),
            deploy=DeployConfig(
                image_name=image_name,
                container_name=container_name,
                host_port=host_port,
                container_port=container_port,
                port_mode=PortMode.FIXED if host_port else PortMode.DYNAMIC,
                access_level=AccessLevel.PUBLIC,
                network_mode=NetworkMode.BRIDGE,
            ),
            health=HealthConfig(protocol=HealthProtocol.HTTP, endpoint="/"),
            volumes=volumes,
            env=env,
        )
        app.history.append(AppHistoryEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=HistoryEventType.CREATED,
            message=f"Imported from existing container: {container_name} on {host}",
        ))

        config.apps[suggested_name] = app
        save_config(config)
        console.print(f"[green]'{suggested_name}' registered in HomePilot config.[/green]", err=True)

    registry.disconnect_all()


# ------------------------------------------------------------------
# homepilot migrate <app_name> --to <dest_host> [--remove-source] [--yes]
# ------------------------------------------------------------------


@cli.command(name="migrate")
@click.argument("app_name")
@click.option("--to", "dest_host", required=True, help="Destination host key to migrate the app to.")
@click.option(
    "--remove-source",
    is_flag=True,
    default=False,
    help="Remove the app from the source host after successful migration.",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Stream raw log output from each migration step.")
def migrate_cmd(app_name: str, dest_host: str, remove_source: bool, yes: bool, verbose: bool) -> None:
    """Migrate a managed app from its current host to a destination host.

    Streams live progress to the console.  After the migration succeeds,
    use --remove-source to clean up the original copy automatically, or
    omit it to keep both copies until you manually delete the old one.
    """
    from homepilot.config import load_config, save_config
    from homepilot.services.migrator import Migrator

    config = load_config()

    if app_name not in config.apps:
        console.print(f"[red]App '{app_name}' not found in HomePilot config.[/red]")
        console.print(f"Available apps: {', '.join(config.apps.keys()) or '(none)'}")
        sys.exit(1)

    app_cfg = config.apps[app_name]
    src_host = app_cfg.host or "(unknown)"

    if dest_host not in config.hosts:
        console.print(f"[red]Destination host '{dest_host}' not found in config.[/red]")
        console.print(f"Known hosts: {', '.join(config.hosts.keys()) or '(none)'}")
        sys.exit(1)

    if src_host == dest_host:
        console.print(f"[yellow]App '{app_name}' is already on '{dest_host}'. Nothing to do.[/yellow]")
        sys.exit(0)

    # Show plan and ask for confirmation
    console.print(f"\n[bold]Migration plan:[/bold]")
    console.print(f"  App:         [cyan]{app_name}[/cyan]")
    console.print(f"  Source:      [cyan]{src_host}[/cyan]")
    console.print(f"  Destination: [cyan]{dest_host}[/cyan]")
    if app_cfg.volumes:
        console.print(f"  Volumes:     {len(app_cfg.volumes)} volume(s) will be transferred")
    else:
        console.print(f"  Volumes:     none (image-only)")
    console.print(
        f"  After:       {'[yellow]source copy will be removed[/yellow]' if remove_source else '[dim]source copy preserved[/dim]'}"
    )
    console.print()

    if not yes:
        if not click.confirm("Proceed with migration?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            return

    step_icons = {
        "running": "...",
        "success": "OK ",
        "failed": "ERR",
        "skipped": "---",
    }

    def line_cb(line: str) -> None:
        if verbose:
            console.print(f"    [dim]{line}[/dim]")

    migrator = Migrator(config, app_cfg, dest_host, line_callback=line_cb)

    console.print(f"[bold]Starting migration of '{app_name}'...[/bold]\n")
    succeeded = True
    try:
        for step_name, step_status, message in migrator.run_sync():
            icon = step_icons.get(step_status, "   ")
            color = (
                "green" if step_status == "success"
                else "red" if step_status == "failed"
                else "yellow" if step_status == "skipped"
                else "dim"
            )
            console.print(f"  [{color}][{icon}][/{color}] {step_name}: {message}")
            if step_status == "failed":
                succeeded = False
    except KeyboardInterrupt:
        console.print("\n[yellow]Migration aborted by user.[/yellow]")
        sys.exit(130)

    if not succeeded or not (migrator.state and migrator.state.succeeded):
        console.print("\n[red]Migration failed — see errors above.[/red]")
        sys.exit(1)

    # Update config: reassign app to destination host
    app_cfg.host = dest_host
    save_config(config)
    console.print(f"\n[green]App '{app_name}' is now running on '{dest_host}'.[/green]")

    # Optionally clean up source
    if remove_source:
        console.print(f"[bold]Removing app from source host '{src_host}'...[/bold]")
        try:
            if migrator.cleanup_source():
                console.print(f"[green]Source copy on '{src_host}' removed.[/green]")
            else:
                console.print(f"[yellow]Warning: could not fully remove source copy on '{src_host}'.[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]Warning: cleanup failed: {exc}[/yellow]")
    else:
        console.print(
            f"[dim]Source copy on '{src_host}' preserved. "
            "Use 'homepilot delete' to remove it when ready.[/dim]"
        )


# ------------------------------------------------------------------
# homepilot registry  (subcommand group)
# ------------------------------------------------------------------


@cli.group(name="registry")
def registry_group() -> None:
    """Manage and deploy from container registries (Docker Hub)."""


@registry_group.command(name="search")
@click.argument("query")
@click.option("--limit", default=25, show_default=True, help="Maximum results to return.")
def registry_search(query: str, limit: int) -> None:
    """Search Docker Hub for images matching QUERY.

    Prints a table of results with name, description, stars, and
    an indicator for official images.
    """
    from homepilot.services.registry import search_images

    console.print(f"[bold]Searching Docker Hub for '[cyan]{query}[/cyan]'...[/bold]")
    results = search_images(query, page_size=limit)

    if not results:
        console.print("[yellow]No results found (or network error).[/yellow]")
        return

    table = Table(title=f"Docker Hub results for '{query}'")
    table.add_column("Image", style="bold cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Stars", justify="right")
    table.add_column("Official", justify="center")

    for r in results:
        official_badge = "[green]Yes[/green]" if r.is_official else ""
        desc = r.description[:70] + "..." if len(r.description) > 70 else r.description
        table.add_row(r.name, desc, str(r.star_count), official_badge)

    console.print(table)
    console.print(
        f"\n[dim]{len(results)} result(s) — "
        "use 'homepilot registry deploy <image> --host <host>' to deploy.[/dim]"
    )


@registry_group.command(name="deploy")
@click.argument("image")
@click.option("--host", "host_key", required=True, help="Target host name from config.")
@click.option(
    "--port",
    "port_mapping",
    default=None,
    metavar="HOST:CONTAINER",
    help="Port mapping, e.g. 8080:80. Omit for dynamic host port.",
)
@click.option("--name", "app_name", default=None, help="App/container name override.")
@click.option("--tag", default="latest", show_default=True, help="Image tag to deploy.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Stream raw log output from each deploy step.")
def registry_deploy(
    image: str,
    host_key: str,
    port_mapping: str | None,
    app_name: str | None,
    tag: str,
    verbose: bool,
) -> None:
    """Deploy an image from a registry directly to a host.

    Creates a minimal AppConfig (image-only, no source path) and then
    runs the standard deploy pipeline.

    Examples:\n
        homepilot registry deploy nginx --host truenas --port 8080:80\n
        homepilot registry deploy grafana/grafana --host truenas --port 3000:3000 --name grafana
    """
    from homepilot.config import load_config, save_config, validate_config
    from homepilot.models import (
        AppConfig,
        BuildConfig,
        DeployConfig,
        HealthConfig,
        HealthProtocol,
        PortMode,
        SourceConfig,
        SourceType,
    )
    from homepilot.services.deployer import Deployer
    from homepilot.providers import ProviderRegistry
    from homepilot.providers.truenas import TrueNASProvider

    config = load_config()

    if host_key not in config.hosts:
        console.print(f"[red]Unknown host:[/red] '{host_key}'")
        console.print(f"Known hosts: {', '.join(config.hosts.keys()) or '(none)'}")
        sys.exit(1)

    # Derive app name from image (strip org prefix and tag)
    image_base = image.split("/")[-1].split(":")[0]
    derived_name = app_name or image_base
    image_ref = f"{image}:{tag}" if ":" not in image else image

    # Parse port mapping
    host_port = 0
    container_port = 80
    if port_mapping:
        parts = port_mapping.split(":")
        if len(parts) != 2:
            console.print(
                f"[red]Invalid --port format '{port_mapping}'. Expected HOST:CONTAINER.[/red]"
            )
            sys.exit(1)
        try:
            host_port = int(parts[0])
            container_port = int(parts[1])
        except ValueError:
            console.print(f"[red]Port values must be integers, got '{port_mapping}'.[/red]")
            sys.exit(1)

    container_name = f"{derived_name}-app"

    # Warn if app name already exists
    if derived_name in config.apps:
        console.print(f"[yellow]App '{derived_name}' already exists in config.[/yellow]")
        if not click.confirm("Overwrite?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            return

    # Guess health endpoint
    is_web = any(kw in image.lower() for kw in ("nginx", "apache", "caddy", "httpd"))
    health_endpoint = "/" if is_web else "/api/health"

    app_cfg = AppConfig(
        name=derived_name,
        host=host_key,
        source=SourceConfig(type=SourceType.LOCAL, path=""),
        build=BuildConfig(),
        deploy=DeployConfig(
            image_name=image_ref,
            container_name=container_name,
            host_port=host_port,
            container_port=container_port,
            port_mode=PortMode.FIXED if host_port else PortMode.DYNAMIC,
        ),
        health=HealthConfig(protocol=HealthProtocol.HTTP, endpoint=health_endpoint),
    )

    config.apps[derived_name] = app_cfg

    errors = validate_config(config)
    if errors:
        console.print("[red]Configuration errors:[/red]")
        for e in errors:
            console.print(f"  - {e}")
        sys.exit(1)

    # Resolve server config for the target host
    registry = ProviderRegistry(config)
    provider = registry.get_provider(host_key)

    if provider and isinstance(provider, TrueNASProvider):
        server_config = provider._config.to_server_config()
    else:
        server_config = config.server

    def line_cb(line: str) -> None:
        if verbose:
            console.print(f"    [dim]{line}[/dim]")

    deployer = Deployer(server_config, app_cfg, line_callback=line_cb)

    console.print(f"[bold]Deploying [cyan]{image_ref}[/cyan] to [cyan]{host_key}[/cyan] as '{derived_name}'...[/bold]")
    try:
        for step_name, step_status, message in deployer.run_sync():
            icon = {"running": "...", "success": "OK ", "failed": "ERR", "skipped": "---"}.get(
                step_status, "   "
            )
            color = (
                "green" if step_status == "success"
                else "red" if step_status == "failed"
                else "yellow" if step_status == "skipped"
                else "dim"
            )
            console.print(f"  [{color}][{icon}][/{color}] {step_name}: {message}")

        if deployer.state and deployer.state.succeeded:
            from datetime import datetime, timezone
            from homepilot.config import save_config

            app_cfg.last_deployed = datetime.now(timezone.utc).isoformat()
            save_config(config)
            console.print(f"\n[green]'{derived_name}' deployed successfully from registry.[/green]")
        else:
            console.print("\n[red]Deployment failed.[/red]")
            sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Deployment aborted by user.[/yellow]")
        sys.exit(130)


# ------------------------------------------------------------------
# homepilot backup <app_name> [--output DIR]
# homepilot restore <app_name> --from <backup_path>
# ------------------------------------------------------------------


@cli.command(name="backup")
@click.argument("app_name")
@click.option(
    "--output",
    "output_dir",
    default=None,
    help="Directory to write the backup file. Defaults to the current directory.",
)
def backup_cmd(app_name: str, output_dir: str | None) -> None:
    """Export an app's config and volume data to a backup archive.

    The backup contains:
      - A JSON manifest with the full AppConfig
      - Volume data tarballs (TrueNAS hosts only, via SSH)

    The output is a timestamped .tar.gz file in OUTPUT_DIR.
    """
    import json
    import dataclasses
    import tarfile
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path

    config, _registry = _build_registry()

    if app_name not in config.apps:
        console.print(f"[red]App '{app_name}' not found in HomePilot config.[/red]")
        console.print(f"Available apps: {', '.join(config.apps.keys()) or '(none)'}")
        sys.exit(1)

    app_cfg = config.apps[app_name]
    host_key = app_cfg.host or next(iter(config.hosts), "")

    out_path = Path(output_dir).expanduser().resolve() if output_dir else Path.cwd()
    if not out_path.exists():
        console.print(f"[red]Output directory does not exist: {out_path}[/red]")
        sys.exit(1)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_name = f"{app_name}-backup-{timestamp}"
    backup_tar = out_path / f"{backup_name}.tar.gz"

    console.print(f"[bold]Backing up '[cyan]{app_name}[/cyan]' from host '[cyan]{host_key}[/cyan]'...[/bold]\n")

    # -----------------------------------------------------------------
    # Helper: serialise a dataclass instance to a JSON-safe dict
    # -----------------------------------------------------------------
    def _to_dict(obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
        if isinstance(obj, list):
            return [_to_dict(i) for i in obj]
        if isinstance(obj, dict):
            return {k: _to_dict(v) for k, v in obj.items()}
        if hasattr(obj, "value"):  # Enum
            return obj.value
        return obj

    manifest = {
        "homepilot_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "app_name": app_name,
        "source_host": host_key,
        "app_config": _to_dict(app_cfg),
        "volume_paths": [v.host for v in app_cfg.volumes if v.host],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        manifest_file = tmp / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2, default=str))
        console.print(f"  [green][OK ][/green] manifest.json written")

        # Try to pull volume data via TrueNAS SSH
        pulled_volumes: list[Path] = []
        if app_cfg.volumes:
            from homepilot.providers import ProviderRegistry
            from homepilot.providers.truenas import TrueNASProvider
            from homepilot.services.truenas import TrueNASService

            registry = ProviderRegistry(config)
            provider = registry.get_provider(host_key)

            if provider and isinstance(provider, TrueNASProvider):
                try:
                    provider.connect()
                    truenas: TrueNASService = provider.truenas  # type: ignore[assignment]
                    host_cfg = config.hosts[host_key]
                    backup_dir = getattr(host_cfg, "backup_dir", "/tmp/homepilot-backups")

                    for vol in app_cfg.volumes:
                        if not vol.host:
                            continue
                        console.print(f"  ... backing up volume: {vol.host}")
                        remote_path = truenas.backup_container_data(
                            app_cfg.deploy.container_name,
                            vol.container,
                            backup_dir,
                        )
                        if remote_path:
                            local_vol = tmp / Path(remote_path).name
                            provider.ssh.download_file(remote_path, local_vol)
                            pulled_volumes.append(local_vol)
                            console.print(f"  [green][OK ][/green] Volume data pulled: {Path(remote_path).name}")
                        else:
                            console.print(f"  [yellow][---][/yellow] No data found for volume: {vol.host}")

                    provider.disconnect()
                except Exception as exc:
                    console.print(f"  [yellow][---][/yellow] Could not pull volumes: {exc}")
            else:
                console.print(
                    f"  [yellow][---][/yellow] Volume backup via SSH only supported for TrueNAS hosts. "
                    "Volume paths recorded in manifest."
                )
        else:
            console.print(f"  [dim][---][/dim] No volumes configured — config-only backup.")

        # Pack everything into a .tar.gz
        with tarfile.open(backup_tar, "w:gz") as tar:
            tar.add(manifest_file, arcname="manifest.json")
            for vol_file in pulled_volumes:
                tar.add(vol_file, arcname=vol_file.name)

    size_kb = backup_tar.stat().st_size / 1024
    console.print(f"\n[green]Backup saved:[/green] {backup_tar}")
    console.print(f"  Size: {size_kb:.1f} KB")
    if pulled_volumes:
        console.print(f"  Contains {len(pulled_volumes)} volume archive(s) + config manifest.")
    else:
        console.print(f"  Contains config manifest only.")


@cli.command(name="restore")
@click.argument("app_name")
@click.option(
    "--from",
    "backup_path",
    required=True,
    metavar="BACKUP_FILE",
    help="Path to the .tar.gz backup file created by 'homepilot backup'.",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
def restore_cmd(app_name: str, backup_path: str, yes: bool) -> None:
    """Restore an app from a backup archive.

    At minimum, this re-registers the app configuration in HomePilot.
    If the backup contains volume data archives, they are listed so you
    can manually extract them to the correct host paths.
    """
    import json
    import tarfile
    import tempfile
    from pathlib import Path

    from homepilot.config import load_config, save_config

    bp = Path(backup_path).expanduser().resolve()
    if not bp.exists():
        console.print(f"[red]Backup file not found: {bp}[/red]")
        sys.exit(1)

    if not tarfile.is_tarfile(bp):
        console.print(f"[red]'{bp}' does not appear to be a valid tar archive.[/red]")
        sys.exit(1)

    config = load_config()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Extract the archive
        with tarfile.open(bp, "r:gz") as tar:
            tar.extractall(tmp)

        manifest_file = tmp / "manifest.json"
        if not manifest_file.exists():
            console.print(f"[red]Backup archive does not contain a manifest.json — invalid backup file.[/red]")
            sys.exit(1)

        manifest = json.loads(manifest_file.read_text())
        backed_app_name = manifest.get("app_name", app_name)
        app_config_dict = manifest.get("app_config", {})
        volume_paths = manifest.get("volume_paths", [])
        created_at = manifest.get("created_at", "unknown")
        source_host = manifest.get("source_host", "unknown")

        console.print(f"\n[bold]Backup manifest:[/bold]")
        console.print(f"  App:          [cyan]{backed_app_name}[/cyan]")
        console.print(f"  Created:      {created_at}")
        console.print(f"  Source host:  {source_host}")
        if volume_paths:
            console.print(f"  Volume paths: {', '.join(volume_paths)}")
        else:
            console.print(f"  Volumes:      none")

        # Check for volume archives inside the tarball
        vol_archives = [f for f in tmp.iterdir() if f.suffix == ".gz" and f.name != "manifest.json"]
        if vol_archives:
            console.print(f"\n  [yellow]Note: {len(vol_archives)} volume archive(s) found in backup.[/yellow]")
            console.print(
                "  These must be manually extracted to the appropriate host paths.\n"
                "  Archives:\n" + "\n".join(f"    {f.name}" for f in vol_archives)
            )

        if backed_app_name in config.apps:
            console.print(f"\n[yellow]App '{backed_app_name}' already exists in config.[/yellow]")
            if not yes:
                if not click.confirm("Overwrite existing app config?", default=False):
                    console.print("[yellow]Aborted.[/yellow]")
                    return

        # Rebuild AppConfig from the manifest dict
        from homepilot.models import (
            AppConfig,
            BuildConfig,
            DeployConfig,
            HealthConfig,
            HealthProtocol,
            PortMode,
            AccessLevel,
            NetworkMode,
            SourceConfig,
            SourceType,
            VolumeMount,
            HistoryEventType,
            AppHistoryEvent,
        )
        from datetime import datetime, timezone

        deploy_d = app_config_dict.get("deploy", {})
        source_d = app_config_dict.get("source", {})
        health_d = app_config_dict.get("health", {})
        build_d = app_config_dict.get("build", {})
        volumes_d = app_config_dict.get("volumes", [])
        env_d = app_config_dict.get("env", {})

        restored_app = AppConfig(
            name=backed_app_name,
            host=app_config_dict.get("host", source_host),
            source=SourceConfig(
                type=SourceType(source_d.get("type", "local")),
                path=source_d.get("path", ""),
                git_url=source_d.get("git_url", ""),
                git_branch=source_d.get("git_branch", "main"),
            ),
            build=BuildConfig(
                dockerfile=build_d.get("dockerfile", "Dockerfile"),
                platform=build_d.get("platform", "linux/amd64"),
                context=build_d.get("context", "."),
            ),
            deploy=DeployConfig(
                image_name=deploy_d.get("image_name", ""),
                container_name=deploy_d.get("container_name", f"{backed_app_name}-app"),
                host_port=deploy_d.get("host_port", 0),
                container_port=deploy_d.get("container_port", 80),
                port_mode=PortMode(deploy_d.get("port_mode", "dynamic")),
                access_level=AccessLevel(deploy_d.get("access_level", "public")),
                network_mode=NetworkMode(deploy_d.get("network_mode", "bridge")),
            ),
            health=HealthConfig(
                protocol=HealthProtocol(health_d.get("protocol", "http")),
                endpoint=health_d.get("endpoint", "/"),
                expected_status=health_d.get("expected_status", 200),
                interval_seconds=health_d.get("interval_seconds", 30),
            ),
            volumes=[
                VolumeMount(
                    host=v.get("host", ""),
                    container=v.get("container", ""),
                    mode=v.get("mode", ""),
                )
                for v in volumes_d
                if v.get("host") or v.get("container")
            ],
            env=env_d,
            last_deployed=app_config_dict.get("last_deployed", ""),
        )
        restored_app.history.append(
            AppHistoryEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type=HistoryEventType.CREATED,
                message=f"Restored from backup: {bp.name} (original backup: {created_at})",
            )
        )

        if not yes and backed_app_name not in config.apps:
            console.print()
            if not click.confirm(f"Register '{backed_app_name}' in HomePilot config?", default=True):
                console.print("[yellow]Aborted.[/yellow]")
                return

        config.apps[backed_app_name] = restored_app
        save_config(config)
        has_vol_archives = len(vol_archives) > 0

    console.print(f"\n[green]App '{backed_app_name}' restored to HomePilot config.[/green]")
    console.print(f"  Host: {restored_app.host}")
    console.print(f"  Image: {restored_app.deploy.image_name}")
    if has_vol_archives:
        console.print(
            "\n[yellow]Remember to manually extract volume archives to the host before deploying.[/yellow]"
        )
    console.print(
        f"\nTo deploy the restored app: [bold]homepilot deploy {backed_app_name}[/bold]"
    )


if __name__ == "__main__":
    cli()
