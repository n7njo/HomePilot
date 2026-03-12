# HomePilot

**Home Lab Manager** — manage, monitor, and deploy apps across TrueNAS, Proxmox, and more from the terminal.

HomePilot provides both a Textual-based TUI and a headless CLI for managing home lab infrastructure. It uses a pluggable provider architecture so multiple backend systems (TrueNAS Docker hosts, Proxmox VE clusters, etc.) can be managed from a single interface.

## Architecture

```
HomePilotApp (Textual TUI)
    └── ProviderRegistry
            ├── TrueNASProvider  →  SSHService + TrueNASService
            └── ProxmoxProvider  →  ProxmoxAPI (httpx REST client)
```

Resources from all providers are displayed in a unified dashboard table. Each provider implements the `InfraProvider` protocol defined in `providers/base.py`, which standardizes operations like `list_resources`, `start`, `stop`, `restart`, `logs`, and `status`.

## Key Concepts

- **Host** — a configured infrastructure backend (e.g. a TrueNAS server or Proxmox node). Defined in `~/.homepilot/config.yaml` under `hosts:`.
- **Provider** — the code that connects to a host and exposes its resources. Each host type has a corresponding provider class.
- **Resource** — a VM, LXC container, Docker container, or app managed by a provider.
- **App** — a Docker application with full build/deploy config, tied to a TrueNAS host.

## Origin

HomePilot was created by merging two projects:

- **DockPilot** — TrueNAS Docker deployment TUI (Python/Textual)
- **HomeLabTools** — Proxmox and home lab infrastructure automation (shell scripts)
