---
description: Restore a HomePilot app from a backup archive
argument-hint: <app-name> --from <backup-path> [--yes]
allowed-tools: [Bash]
---

Restore a HomePilot app from a backup archive created by `/hp-backup`.

If $ARGUMENTS is empty, ask for the app name and the path to the backup `.tar.gz` file.

Run: `venv/bin/homepilot restore $ARGUMENTS`

After restoring the config, remind the user that volume data archives inside the backup must be manually extracted to the server. Offer to deploy the restored app with `/hp-deploy`.
