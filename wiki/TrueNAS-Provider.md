# TrueNAS Provider

The TrueNAS provider manages Docker containers and TrueNAS Custom Apps over SSH.

## How It Works

```
TrueNASProvider
    ├── SSHService     → paramiko SSH connection
    └── TrueNASService → docker + midclt commands over SSH
```

The provider connects to TrueNAS via SSH using `paramiko`, then runs Docker and `midclt` commands remotely to list containers, start/stop apps, stream logs, and deploy images.

## Config Fields

```yaml
hosts:
  truenas:
    type: truenas
    host: truenas.local # hostname or IP
    user: neil # SSH user
    ssh_key: "" # path to SSH key (empty = default)
    docker_cmd: sudo docker # Docker CLI prefix
    midclt_cmd: sudo -i midclt call # TrueNAS middleware CLI
    data_root: /mnt/tank/apps # root path for app data
    backup_dir: /tmp/homepilot-backups # container backup location
    dynamic_port_range_start: 30200 # auto-assigned port range
    dynamic_port_range_end: 30299
```

## Resources

The TrueNAS provider exposes `docker_container` resources. Each running Docker container appears in the dashboard with:

- **Status** — derived from `docker ps` output (Running / Stopped)
- **Port** — host port extracted from the port mapping string
- **Image** — Docker image name

## Deploy Pipeline

The deploy workflow (triggered by `homepilot deploy <app>` or the `d` key in the TUI):

1. Builds the Docker image locally (`docker buildx build --platform linux/amd64`)
2. Saves the image to a tarball (`docker save`)
3. Transfers the tarball to TrueNAS via SCP
4. Loads the image on TrueNAS (`docker load`)
5. Stops the existing container, backs up data, and starts the new one
6. Runs a health check against the configured endpoint

## Supported Actions

- **start** — starts a TrueNAS Custom App (via `midclt`) or Docker container
- **stop** — stops the app or container
- **restart** — stop + start
- **remove** — stops and removes the Docker container
- **logs** — fetches the last N lines of container logs
- **status** — queries current container state
