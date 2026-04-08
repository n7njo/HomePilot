---
description: Stop a container on a HomePilot host
argument-hint: <name> [--host <host>]
allowed-tools: [Bash]
---

Run `venv/bin/homepilot stop $ARGUMENTS` and display the result.

If $ARGUMENTS is empty, run `venv/bin/homepilot status` first to show running containers, then ask which one to stop.

On success, confirm the container is stopped. On failure, report the error.
