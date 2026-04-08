---
description: Manage HomePilot hosts (list, add, delete, test, bootstrap)
argument-hint: [list|add|delete|test|bootstrap] [args...]
allowed-tools: [Bash]
---

Manage HomePilot host configuration.

Parse $ARGUMENTS to determine the subcommand:

- No argument or "list": run `venv/bin/homepilot host list`
- "add": run `venv/bin/homepilot host add` (interactive prompts)
- "delete <name>": run `venv/bin/homepilot host delete <name>`
- "test [name]": run `venv/bin/homepilot host test [name]`
- "bootstrap <name>": run `venv/bin/homepilot host bootstrap <name>`

If subcommand is unclear, show the options above and ask.

Display results and highlight any connectivity failures. For bootstrap, stream progress output and confirm when complete.
