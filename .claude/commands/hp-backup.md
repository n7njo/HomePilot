---
description: Backup a HomePilot app's config and volumes
argument-hint: <app-name> [--output <dir>]
allowed-tools: [Bash]
---

Backup a managed HomePilot app to a local archive.

If $ARGUMENTS is empty, run `venv/bin/homepilot config` to list apps, then ask which to back up and where to save it (default: current directory).

Run: `venv/bin/homepilot backup $ARGUMENTS`

Report the output archive path on success. Remind the user that volume data transfer is supported for TrueNAS hosts only; other hosts get a config-only backup.
