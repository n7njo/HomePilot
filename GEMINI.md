# HomePilot — Home Lab Manager

HomePilot is a Textual-based terminal UI (TUI) and CLI tool designed to manage, monitor, and deploy applications across diverse home lab infrastructure, specifically targeting TrueNAS and Proxmox VE.

## Project Overview

- **Purpose:** Centralized management of home lab resources (Docker containers, VMs, LXC containers).
- **Core Technologies:**
    - **Language:** Python 3.11+
    - **TUI Framework:** [Textual](https://textual.textualize.io/)
    - **CLI Framework:** [Click](https://click.palletsprojects.com/)
    - **Infrastructure Interaction:** [Paramiko](https://www.paramiko.org/) (SSH/SFTP), [httpx](https://www.python-httpx.org/) (REST APIs/Health checks).
    - **Configuration:** YAML (managed via `PyYAML`).
    - **UI Rendering:** [Rich](https://rich.readthedocs.io/).

## Core Architectural Patterns

- **Provider Abstraction:** All infrastructure types (TrueNAS, Proxmox) must implement the `InfraProvider` protocol (`src/homepilot/providers/base.py`). 
- **Registry Management:** `ProviderRegistry` (`src/homepilot/providers/__init__.py`) is the central manager for all provider instances, handling their lifecycle and resource aggregation.
- **Generator-Based Deployment & Migration:** Pipelines for deployment (`deployer.py`) and migration (`migrator.py`) use a generator-based step pattern. This allows real-time progress reporting to the UI.
- **Audit & History:** Persistent app events are recorded in `AppConfig.history`, including git commit hashes, config changes, and migration history.
- **Real-time Metrics:** Integrated Netdata support provides high-resolution metrics (CPU, RAM, Disk) with sparkline rendering in the TUI, falling back to SSH/API metrics when Netdata is unavailable.
- **Managed vs Discovered:** Resources are categorized as Managed (defined in config) or Discovered (detected on host). Managed resources support full lifecycle management (deploy/migrate/delete), while Discovered resources offer basic actions and a cleanup workflow.
- **Deployment Strategies:**
    - **TrueNAS:** Uses a "Build-Transfer-Deploy" strategy (local build, SFTP transfer, remote `docker load`).
    - **Proxmox:** Primarily uses a "Registry-Pull" strategy over SSH.
- **Service-Oriented Logic:** Core logic (SSH, Docker, Proxmox API, Netdata, Migrator) is decoupled from the UI and providers into specialized services.

## Sources of Truth

- **Application Configuration:** `HomePilotConfig` dataclass in `src/homepilot/models.py`.
- **Resource Definition:** `Resource` dataclass in `src/homepilot/providers/base.py`.
- **Infrastructure Providers:** Implementations of `InfraProvider` (e.g., `TrueNASProvider`, `ProxmoxProvider`).

## Building and Running

### Installation
```bash
# Install in editable mode with development dependencies
pip install -e ".[dev]"
```

### Running the Application
- **TUI (Standard):** `homepilot`
- **TUI (Development Mode with Hot-Reload):** `textual run --dev src/homepilot/app.py`
- **CLI Commands:**
    - `homepilot status` — View all resources and their status.
    - `homepilot hosts` — List configured hosts and test connectivity.
    - `homepilot deploy <app_name>` — Deploy a specific application.
    - `homepilot config` — Display current configuration.

### Testing
```bash
# Run all tests
pytest
```

## Development Conventions

- **Type Safety:** Strict use of type hints throughout the codebase. `from __future__ import annotations` is used for forward references.
- **Asynchronous Operations:** Textual workers are used for all background tasks (connecting to hosts, fetching logs, running deployments) to keep the UI responsive.
- **Adding a Provider:**
    1. Define the provider in `src/homepilot/providers/<type>.py`.
    2. Implement the `InfraProvider` protocol.
    3. Update `src/homepilot/models.py` with a corresponding `HostConfig` subclass.
    4. Register the new provider in `ProviderRegistry._build_providers()`.
- **Configuration Management:**
    - Config is stored at `~/.homepilot/config.yaml`.
    - Logic for loading, saving, and migrating config is in `src/homepilot/config.py`.
- **Testing Style:**
    - Tests are located in the `tests/` directory.
    - Use `unittest.mock` and `@patch` to isolate services (like SSH or API calls) during testing.
    - CLI testing uses `click.testing.CliRunner`.

## Key Directories

- `src/homepilot/providers/`: Infrastructure-specific implementations.
- `src/homepilot/services/`: Backend logic (SSH, Docker, Deployer, Health).
- `src/homepilot/screens/`: Individual TUI views (Dashboard, Deploy, Config Editor).
- `src/homepilot/widgets/`: Reusable TUI components (AppCard, LogViewer).
- `tests/`: Comprehensive test suite covering CLI, models, and providers.

## Task & Context Management (Beads)

This project uses **Beads** (`bd` CLI) for structured task tracking and persistent memory.
- **Initialization:** The project is already initialized with a `.beads` directory.
- **Usage:** You MUST use the `bd` command via `run_shell_command` to manage the task graph:
    - `bd tasks` — List all tasks and their status.
    - `bd ready` — Show tasks that have no unmet dependencies.
    - `bd claim <task_id>` — Mark a task as in-progress.
    - `bd finish <task_id>` — Mark a task as complete.
    - `bd add "<description>"` — Add a new task to the graph.
- **Workflow:** Before starting any significant feature or refactor, run `bd ready` to see what needs to be done next. Update the task status as you progress to ensure a shared source of truth for all agents.
