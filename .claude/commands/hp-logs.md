---
description: View container logs from a HomePilot host
argument-hint: <name> [--host <host>] [--follow] [--tail <n>]
allowed-tools: [Bash]
---

Run `venv/bin/homepilot logs $ARGUMENTS` and display the output.

If $ARGUMENTS is empty, run `venv/bin/homepilot status` first to show running containers, then ask which one to tail logs from.

Default behaviour (no --follow): shows last 100 lines. If the user asked to stream live logs, include `--follow`.

After showing logs, ask if the user wants to take action based on what they see (e.g. restart the container, redeploy).
