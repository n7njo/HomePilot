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
    """HomePilot — Home Lab Manager TUI."""
    if ctx.invoked_subcommand is None:
        # Default action: launch the TUI.
        _launch_tui()


def _launch_tui() -> None:
    """Start the Textual TUI application."""
    from homepilot.app import HomePilotApp

    app = HomePilotApp()
    app.run()


@cli.command()
@click.argument("app_name")
def deploy(app_name: str) -> None:
    """Deploy an app to TrueNAS (headless mode)."""
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
    deployer = Deployer(config.server, app_config)

    console.print(f"[bold]Deploying {app_name}…[/bold]")
    try:
        for step_name, status, message in deployer.run_sync():
            icon = {"running": "⏳", "success": "✅", "failed": "❌", "skipped": "⏭️"}.get(
                status, "•"
            )
            console.print(f"  {icon} {step_name}: {message}")

        if deployer.state and deployer.state.succeeded:
            console.print(f"\n[green]✅ {app_name} deployed successfully![/green]")
        else:
            console.print(f"\n[red]❌ Deployment failed.[/red]")
            sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Deployment aborted by user.[/yellow]")
        sys.exit(130)


@cli.command()
def status() -> None:
    """Show status of all registered apps."""
    from homepilot.config import load_config
    from homepilot.services.health import check_health_sync
    from homepilot.services.truenas import TrueNASService
    from homepilot.services.ssh import SSHService

    config = load_config()

    table = Table(title="HomePilot — App Status")
    table.add_column("App", style="bold cyan")
    table.add_column("Status")
    table.add_column("Health")
    table.add_column("Image")
    table.add_column("Port")
    table.add_column("URL")

    try:
        ssh = SSHService(config.server)
        ssh.connect()
        truenas = TrueNASService(ssh, config.server)

        for name, app in config.apps.items():
            container_status = truenas.container_status(app.deploy.container_name)
            health = check_health_sync(
                config.server.host, app.deploy.host_port, app.health.endpoint
            )
            status_style = "green" if container_status == "running" else "red"
            health_style = "green" if health == "Healthy" else "red"
            url = f"http://{config.server.host}:{app.deploy.host_port}"
            table.add_row(
                name,
                f"[{status_style}]{container_status}[/{status_style}]",
                f"[{health_style}]{health}[/{health_style}]",
                f"{app.deploy.image_name}:latest",
                str(app.deploy.host_port),
                url,
            )

        ssh.close()
    except Exception as exc:
        console.print(f"[yellow]Could not connect to server: {exc}[/yellow]")
        for name, app in config.apps.items():
            table.add_row(
                name, "[dim]Unknown[/dim]", "[dim]Unknown[/dim]",
                f"{app.deploy.image_name}:latest",
                str(app.deploy.host_port),
                f"http://{config.server.host}:{app.deploy.host_port}",
            )

    console.print(table)


@cli.command(name="config")
def config_cmd() -> None:
    """Show current configuration."""
    from homepilot.config import load_config, CONFIG_FILE, validate_config

    config = load_config()
    errors = validate_config(config)

    console.print(f"[bold]Config file:[/bold] {CONFIG_FILE}")
    console.print(f"[bold]Server:[/bold] {config.server.user}@{config.server.host}")
    console.print(f"[bold]Apps:[/bold] {len(config.apps)}")

    for name, app in config.apps.items():
        console.print(f"\n  [cyan]{name}[/cyan]")
        console.print(f"    Source: {app.source.type.value} — {app.source.path or app.source.git_url}")
        console.print(f"    Image:  {app.deploy.image_name}")
        console.print(f"    Port:   {app.deploy.host_port} → {app.deploy.container_port}")

    if errors:
        console.print("\n[red]Validation errors:[/red]")
        for e in errors:
            console.print(f"  • {e}")


@cli.command()
@click.argument("app_name")
def add(app_name: str) -> None:
    """Register a new app interactively."""
    # For headless add — the TUI wizard is the primary path.
    _launch_tui()


if __name__ == "__main__":
    cli()
