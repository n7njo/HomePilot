# Proxmox Provider

The Proxmox provider manages VMs and LXC containers via the Proxmox VE REST API.
## How It Works

```
ProxmoxProvider
    ├── ProxmoxAPI → httpx REST client → PVE API (port 8006)
    └── NetdataService → host metrics (CPU, RAM, Disk)
```

The provider authenticates using a PVE API token and communicates over HTTPS. It discovers all VMs and LXC containers across all cluster nodes. Real-time metrics are fetched via Netdata (if enabled) or fallback PVE API metrics.

## Config Fields

```yaml
hosts:
  proxmox:
    type: proxmox
    host: 192.168.1.10:8006 # PVE host:port
    enable_netdata: true # enable sparklines in TUI
    netdata_port: 19999
    token_id: homepilot@pam!homepilot # PVE API token ID
...
```
    token_source: env # "env" or "config"
    verify_ssl: false # set true if using valid certs
    ssh_user: root # for SSH-based operations
    ssh_key: "" # path to SSH key
```

## Token Authentication

The provider supports two ways to supply the API token secret:

- **`token_source: env`** (recommended) — reads the secret from the `PROXMOX_TOKEN_SECRET` environment variable. This avoids storing secrets in the config file.
- **`token_source: config`** — reads `token_secret` directly from the config. Use this only in secure environments.

### Creating a PVE API Token

1. Log into the Proxmox web UI → Datacenter → Permissions → API Tokens
2. Select the user (e.g. `homepilot@pam`) and create a new token
3. Uncheck "Privilege Separation" if you want the token to inherit the user's permissions
4. Copy the token ID and secret

## Resources

The Proxmox provider lists two resource types:

- **`vm`** — QEMU virtual machines
- **`lxc_container`** — LXC containers

Each resource shows:

- **Status** — Running / Stopped (mapped from PVE status strings)
- **VMID** — displayed in the Port column for quick reference
- **Uptime** — formatted as `Xd Yh Zm`
- **Metadata** — node name, maxmem, maxcpu, template flag

## Supported Actions

- **start** — powers on a VM (`POST /nodes/{node}/qemu/{vmid}/status/start`) or starts an LXC container
- **stop** — graceful shutdown via ACPI for VMs, shutdown for LXC
- **restart** — stop + start
- **remove** — deletes the VM or container from the node
- **logs** — reads syslog from the node for the given VMID
- **status** — queries current resource status via the API
