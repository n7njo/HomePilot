# Multi-Host Configuration

HomePilot manages multiple infrastructure hosts from a single config file at `~/.homepilot/config.yaml`.

## Config Structure

```yaml
hosts:
  truenas:
    type: truenas
    host: truenas.local
    user: neil
    ssh_key: ""
    docker_cmd: sudo docker
    midclt_cmd: sudo -i midclt call
    data_root: /mnt/tank/apps
    backup_dir: /tmp/homepilot-backups
    dynamic_port_range_start: 30200
    dynamic_port_range_end: 30299

  proxmox:
    type: proxmox
    host: 192.168.1.10:8006
    token_id: homepilot@pam!homepilot
    token_secret: "" # or set via env var
    token_source: env # "env" | "config"
    verify_ssl: false
    ssh_user: root
    ssh_key: ""

apps:
  house-tracker:
    host: truenas # must match a key in hosts:
    source:
      type: local
      path: /path/to/project
    build:
      dockerfile: Dockerfile
      platform: linux/amd64
      context: "."
    deploy:
      image_name: house-tracker
      container_name: house-tracker-app
      compose_file: truenas-app.yaml
      host_port: 30213
      container_port: 5000
      port_mode: fixed
    health:
      endpoint: /api/health
      expected_status: 200
      interval_seconds: 30
    volumes:
      - host: /mnt/tank/apps/house-tracker/data
        container: /app/data
    env:
      NODE_ENV: production
      PORT: "5000"

theme: dark
```

## Adding a New Host

1. Add an entry under `hosts:` with a unique key (e.g. `proxmox-lab`).
2. Set the `type` field to the provider type (`truenas` or `proxmox`).
3. Fill in the provider-specific settings (see [TrueNAS Provider](TrueNAS-Provider) or [Proxmox Provider](Proxmox-Provider)).
4. Run `homepilot hosts` to verify connectivity.

## Linking Apps to Hosts

Each app has a `host` field that must match a key in `hosts:`. This tells HomePilot which provider to use when deploying or managing that app.

## Legacy Config Migration

If you're upgrading from DockPilot, HomePilot automatically migrates the old single-server `server:` key into a `hosts:` dict with a `truenas` entry. All existing apps are assigned to that host.
