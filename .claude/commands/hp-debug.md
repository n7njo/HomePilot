---
description: Diagnose a failed HomePilot deploy or build failure
argument-hint: <app-name>
allowed-tools: [Bash, Read, Grep]
---

The user wants to diagnose why an app failed to deploy or won't start. This skill is for cases where the container never launches (build failure, config error, image pull failure) — use `/hp-logs` instead if the container starts but misbehaves at runtime.

## Step 1 — Re-run deploy with verbose output

Run: `venv/bin/homepilot deploy --verbose $ARGUMENTS`

Capture the full output and identify where it failed (look for `ERR` or `❌` lines and the raw log lines beneath them).

## Step 2 — Triage by failure type

**Docker build failure** (e.g. pnpm/npm/pip install error, COPY path not found, RUN command failed):
- Read the Dockerfile and build context to understand the build steps
- Check for: wrong base image architecture (amd64 vs arm64), missing files in build context, lock file / dependency resolution issues, layer cache problems
- Look at the exact failing RUN command in the verbose output

**Image pull failure** (registry timeout, auth error, tag not found):
- Verify the image name and tag in `venv/bin/homepilot config`
- Try `venv/bin/homepilot registry search <image>` to confirm the image exists

**SSH / connectivity failure**:
- Run `venv/bin/homepilot host test` to check host reachability

**Container start failure** (exits immediately after start):
- Run `venv/bin/homepilot logs $ARGUMENTS` to see what the container printed before dying
- Check port conflicts, missing env vars, or volume mount errors in the verbose deploy output

## Step 3 — Report findings

Summarise the root cause clearly and suggest the specific fix. Don't just re-run the deploy — explain what needs to change first.
