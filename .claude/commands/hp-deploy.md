---
description: Deploy a HomePilot app to its configured host
argument-hint: <app-name> [--verbose]
allowed-tools: [Bash]
---

The user wants to deploy a HomePilot app.

If $ARGUMENTS is empty, run `venv/bin/homepilot config` to list available apps and ask which one to deploy.

Always run with `--verbose` so raw Docker/SSH build output is visible:
`venv/bin/homepilot deploy --verbose $ARGUMENTS`

Stream and display all deployment step output including raw build lines.

On success, confirm the app is deployed.

On failure:
- Show the exact error from the verbose output
- If it's a Docker build failure (image won't build), the container will never start — check the Dockerfile, build context, and dependency resolution in the verbose output
- If it's a connectivity issue, suggest checking with `venv/bin/homepilot host test`
- If it's a config issue, suggest reviewing with `venv/bin/homepilot config`
