---
description: Show all HomePilot resources across connected hosts
argument-hint: [--host <name>]
allowed-tools: [Bash]
---

Run `venv/bin/homepilot status $ARGUMENTS` and display the output.

If `--host` is provided in $ARGUMENTS, pass it through. Otherwise show all hosts.

Report back the table of resources. If there are unhealthy or stopped resources, highlight them and ask if the user wants to take action (start, deploy, or inspect logs).
