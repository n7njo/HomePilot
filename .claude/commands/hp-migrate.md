---
description: Migrate a HomePilot app from one host to another
argument-hint: <app-name> --to <dest-host> [--remove-source] [--yes]
allowed-tools: [Bash]
---

Migrate a managed HomePilot app between hosts.

If $ARGUMENTS is empty, run `venv/bin/homepilot config` to show apps and `venv/bin/homepilot hosts` to show available destination hosts. Ask the user for the app name and destination host.

Otherwise run: `venv/bin/homepilot migrate $ARGUMENTS`

Stream and display migration progress. After success, confirm the app is running on the destination host. Ask if the user wants to remove the source copy if `--remove-source` was not specified.
