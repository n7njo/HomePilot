---
description: Deploy a HomePilot app to its configured host
argument-hint: <app-name>
allowed-tools: [Bash]
---

The user wants to deploy a HomePilot app.

If $ARGUMENTS is empty, run `venv/bin/homepilot config` to list available apps and ask which one to deploy.

Otherwise run: `venv/bin/homepilot deploy $ARGUMENTS`

Stream and display all deployment step output. On success, confirm the app is deployed. On failure, summarise the error and suggest fixes (e.g. check host connectivity with `/hp-hosts`, review config with `/hp-status`).
