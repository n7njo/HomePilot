---
description: Register a new app in HomePilot (non-interactive)
argument-hint: <name> --host <host> --image <image> [--port HOST:CONTAINER] [--source-path PATH] [--health-protocol http|tcp]
allowed-tools: [Bash]
---

Register a new managed app in HomePilot without opening the TUI.

If $ARGUMENTS is empty or missing required flags (--host, --image), gather them by asking the user:
1. App name
2. Host (run `venv/bin/homepilot hosts` to list options)
3. Docker image name
4. Port mapping (optional, e.g. 8080:80)
5. Source path (optional, for local builds)
6. Health check protocol (http or tcp, default http)

Then run: `venv/bin/homepilot add $ARGUMENTS`

Confirm what was saved. Offer to deploy immediately with `/hp-deploy`.
