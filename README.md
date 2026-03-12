# HomePilot

**Home Lab Manager TUI** — manage, monitor, and deploy apps across TrueNAS, Proxmox, and more from the terminal.

HomePilot unifies management of your home lab infrastructure behind a single Textual-based terminal UI and CLI. It supports multiple infrastructure providers through a pluggable provider architecture.

## Supported Providers

- **TrueNAS** — Docker container deployment, health monitoring, logs, backup
- **Proxmox VE** — VM and LXC container management via the PVE REST API

## Features

- **Dashboard** — see all resources across all hosts at a glance with live health and status indicators
- **Deploy** — build, transfer, and deploy Docker images to TrueNAS with a step-by-step progress view
- **Configure** — edit app settings (source, ports, volumes, environment) via a form-based editor
- **Add Apps** — register new apps with an auto-detecting wizard
- **Logs** — view container logs in real-time
- **Actions** — start, stop, restart, backup, and remove containers
- **Headless mode** — deploy from scripts or CI with `homepilot deploy <app>`

## Installation

```bash
# Clone or navigate to the HomePilot directory
cd HomePilot

# Install in editable mode (recommended for development)
pip install -e ".[dev]"
```

## Quick Start

```bash
# Launch the TUI
homepilot

# Or use headless commands
homepilot status          # Show status of all resources
homepilot hosts           # List hosts and test connectivity
homepilot deploy house-tracker  # Deploy an app
homepilot config          # Show current configuration
```

## Configuration

HomePilot stores its configuration at `~/.homepilot/config.yaml`. On first run, a default config is created.

### Multi-Host Configuration

```yaml
hosts:
  truenas:
    type: truenas
    host: truenas.lan
    user: neil
    ssh_key: ""
    docker_cmd: sudo docker
    midclt_cmd: sudo -i midclt call
    data_root: /mnt/tank/apps
  proxmox:
    type: proxmox
    host: 192.168.0.199
    token_id: user@pve!token-name
    token_source: keychain
    verify_ssl: false

apps:
  my-app:
    host: truenas
    source:
      type: local
      path: /path/to/project
    build:
      dockerfile: Dockerfile
      platform: linux/amd64
    deploy:
      image_name: my-app
      container_name: my-app-container
      host_port: 30200
      container_port: 5000
    health:
      endpoint: /api/health
    volumes:
      - host: /mnt/tank/apps/my-app/data
        container: /app/data
    env:
      NODE_ENV: production
```

## TUI Keyboard Shortcuts

| Key     | Action                 |
| ------- | ---------------------- |
| `d`     | Deploy selected app    |
| `c`     | Configure selected app |
| `l`     | View logs              |
| `a`     | Add new app            |
| `r`     | Refresh health status  |
| `Enter` | View app details       |
| `Esc`   | Go back                |
| `q`     | Quit                   |

## Deployment Pipeline (TrueNAS)

When deploying a Docker app to TrueNAS, HomePilot runs through these steps:

1. **Validate source** — check source path exists, Dockerfile present (or git clone/pull)
2. **Build image** — `docker build --platform linux/amd64`
3. **Export image** — `docker save` to a tar file
4. **Connect** — SSH to TrueNAS server
5. **Transfer** — SFTP upload with progress
6. **Load image** — `docker load` on TrueNAS
7. **Backup data** — backup existing container data
8. **Stop app** — stop via TrueNAS `midclt` or Docker
9. **Start app** — start the updated app
10. **Verify health** — HTTP health check with retries
11. **Cleanup** — remove temporary tar files

## Prerequisites

- Python 3.11+
- Docker (local, for building images)
- SSH access to your TrueNAS server
- Proxmox VE with API token configured (for Proxmox provider)

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run the TUI in dev mode (with hot reload)
textual run --dev src/homepilot/app.py
```

## Project Structure

```
src/homepilot/
├── __main__.py          # CLI entry point (Click)
├── app.py               # Textual App (main TUI)
├── config.py            # Config loading/saving
├── models.py            # Data models
├── providers/           # Infrastructure providers
│   ├── base.py          # Provider protocol & Resource model
│   ├── truenas.py       # TrueNAS provider
│   └── proxmox.py       # Proxmox VE provider
├── screens/             # TUI screens
│   ├── dashboard.py     # Main dashboard
│   ├── resource_detail.py
│   ├── deploy.py
│   ├── config_editor.py
│   └── add_resource.py
├── services/            # Backend services
│   ├── ssh.py           # Paramiko SSH wrapper
│   ├── docker.py        # Local Docker operations
│   ├── deployer.py      # Deploy pipeline orchestrator
│   ├── health.py        # Health monitoring
│   ├── truenas.py       # TrueNAS management
│   └── proxmox_api.py   # PVE REST API client
└── widgets/             # Reusable TUI widgets
    ├── resource_card.py
    ├── log_viewer.py
    └── status_bar.py
```

## Origin

HomePilot was created by merging:

- **DockPilot** — TrueNAS Docker deployment TUI
- **HomeLabTools** — Proxmox and home lab infrastructure automation scripts
