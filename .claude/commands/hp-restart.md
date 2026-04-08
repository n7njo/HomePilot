---
description: Restart a container on a HomePilot host
argument-hint: <name> [--host <host>]
allowed-tools: [Bash]
---

Run `venv/bin/homepilot restart $ARGUMENTS` and display the result.

If $ARGUMENTS is empty, run `venv/bin/homepilot status` first to show running containers, then ask which one to restart.

On success, confirm the container has restarted. On failure, report the error.
