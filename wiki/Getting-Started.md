# Getting Started

## Prerequisites

- Python 3.11+
- Docker (local, for building images to deploy to TrueNAS)
- SSH access to your TrueNAS server (for TrueNAS provider)
- Proxmox VE API token (for Proxmox provider)

## Installation

```bash
git clone https://github.com/n7njo/HomePilot.git
cd HomePilot
pip install -e ".[dev]"
```

## First Run

```bash
# Launch the TUI
homepilot
```

On first run, HomePilot creates a default config at `~/.homepilot/config.yaml` with a TrueNAS host and a sample app entry. Edit this file to match your environment.

## CLI Commands

```bash
homepilot              # Launch the TUI
homepilot status       # Show all resources across all hosts
homepilot status -h proxmox  # Filter to a specific host
homepilot hosts        # List hosts and test connectivity
homepilot deploy <app> # Deploy a Docker app to its host
homepilot config       # Show current configuration
homepilot --version    # Show version
```

## TUI Keyboard Shortcuts

| Key     | Action                |
| ------- | --------------------- |
| `d`     | Deploy selected app   |
| `m`     | Migrate selected app  |
| `c`     | Configure selected    |
| `l`     | View logs             |
| `s`     | Stop / Start resource |
| `a`     | Add new resource      |
| `n`     | Registry browser      |
| `x`     | Delete app / resource |
| `h`     | Manage servers        |
| `i`     | Import config         |
| `r`     | Refresh status        |
| `Enter` | View resource details |
| `Esc`   | Go back               |
| `t`     | Toggle dark/light     |
| `q`     | Quit                  |

## Next Steps

- [Multi-Host Configuration](Multi-Host-Configuration) — configure multiple hosts
- [TrueNAS Provider](TrueNAS-Provider) — deploy Docker apps to TrueNAS
- [Proxmox Provider](Proxmox-Provider) — manage VMs and LXC containers
