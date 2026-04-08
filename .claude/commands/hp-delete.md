---
description: Delete a HomePilot app (config-only, container, or full cleanup)
argument-hint: <app-name> [--level 1|2|3] [--yes]
allowed-tools: [Bash]
---

Delete a HomePilot managed app.

Levels:
- Level 1 (default): remove from HomePilot config only
- Level 2: stop and remove the container (keep volumes)
- Level 3: full cleanup — stop container and delete all volume data

If $ARGUMENTS is empty, run `venv/bin/homepilot config` to list apps, ask which to delete and at what level.

Otherwise run: `venv/bin/homepilot delete $ARGUMENTS`

Always confirm with the user before running level 2 or 3 unless `--yes` is already in $ARGUMENTS. After deletion, confirm success.
