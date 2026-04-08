---
description: Search Docker Hub or deploy an image from a registry
argument-hint: [search <query>] | [deploy <image> --host <host>]
allowed-tools: [Bash]
---

Search Docker Hub or deploy a registry image directly to a host.

Parse $ARGUMENTS:

- "search <query>": run `venv/bin/homepilot registry search <query>` and display results table
- "deploy <image> --host <host>": run `venv/bin/homepilot registry deploy <image> --host <host> [--port HOST:CONTAINER] [--name NAME]`
- No argument: ask whether the user wants to search or deploy

For search: display results and ask if the user wants to deploy any of the listed images.
For deploy: confirm the image, host, and port mapping before running. After success, offer to check status with `/hp-status`.
