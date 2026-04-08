---
description: Import config from a running container into HomePilot
argument-hint: <container-name> --host <host> [--save]
allowed-tools: [Bash]
---

Extract configuration from an existing running Docker container and optionally register it as a managed HomePilot app.

If $ARGUMENTS is empty, run `venv/bin/homepilot hosts` to list available hosts, then ask for the container name and host.

Run: `venv/bin/homepilot import-config $ARGUMENTS`

This prints the extracted YAML config. Review it with the user. If they want to save it, re-run with `--save` appended. Confirm the app name that was registered.
