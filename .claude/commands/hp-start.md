---
description: Start a container on a HomePilot host
argument-hint: <name> [--host <host>]
allowed-tools: [Bash]
---

Run `venv/bin/homepilot start $ARGUMENTS` and display the result.

If $ARGUMENTS is empty, run `venv/bin/homepilot status` first to show stopped containers, then ask which one to start.

On success, confirm the container is running. On failure, report the error.
