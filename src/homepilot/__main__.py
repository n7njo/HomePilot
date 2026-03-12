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
# homepilot hosts
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
# homepilot deploy <app>
# ------------------------------------------------------------------


@cli.command()
@click.argument("app_name")
def deploy(app_name: str) -> None:
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

    deployer = Deployer(server_config, app_config)

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
def add(app_name: str) -> None:
    """Register a new app interactively (opens TUI)."""
    _launch_tui()


if __name__ == "__main__":
    cli()
