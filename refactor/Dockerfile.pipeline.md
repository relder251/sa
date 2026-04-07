# Refactor: Dockerfile.pipeline

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## File Overview

| Property | Value |
|---|---|
| **Path** | `Dockerfile.pipeline` |
| **Purpose** | Build image for `pipeline-server` — phases 1–10 orchestrator |
| **Role** | Provides system tools (git, ssh, curl) and Python deps (FastAPI, ruff, bandit, mypy) for pipeline execution |
| **Built by** | `docker compose up -d --build pipeline-server` |
| **Upstream deps** | `python:3.12-slim` base image; `phases/` directory; `scripts/pipeline_server.py` |
| **Downstream deps** | `pipeline-server` container (port 5002); volumes shadow `/data/phases/` and `/data/scripts/` at runtime |

### Layer structure

| Layer | Content | Notes |
|---|---|---|
| Base | `python:3.12-slim` | Pinned to major.minor (3.12), not patch |
| System | `git`, `openssh-client`, `curl` | Required for phases 5–10: push, SSH deploy, HTTP |
| git config | `safe.directory '*'` | Required for git ops inside Docker |
| Python | fastapi, uvicorn, requests, httpx, ruff, bandit, mypy | All pinned to exact versions |
| COPY | `phases/` → `/data/phases/`, `pipeline_server.py` → `/data/scripts/` | Shadowed by volume mounts at runtime; provide baked-in fallback |
| EXPOSE | 5002 | Matches compose healthcheck (`http://localhost:5002/health`) |
| CMD | `python /data/scripts/pipeline_server.py` | Starts FastAPI server via uvicorn |

### Python dependency audit

| Package | Version | Role |
|---|---|---|
| `fastapi` | 0.115.0 | REST API framework for pipeline server |
| `uvicorn[standard]` | 0.30.0 | ASGI server (started by FastAPI) |
| `requests` | 2.32.3 | Sync HTTP client (LiteLLM calls, webhooks) |
| `httpx` | 0.27.0 | Async HTTP client |
| `ruff` | 0.4.4 | Linter for Phase quality gates |
| `bandit` | 1.7.9 | Security scanner for Phase quality gates |
| `mypy` | 1.10.0 | Type checker for Phase quality gates |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | Base image not patch-pinned | **Info** | `python:3.12-slim` floats on patch releases. Rebuilds may use different Python 3.12.x. Acceptable for homelab; for strict reproducibility, pin to `python:3.12.9-slim` (or latest). |
| 2 | Runs as root | **Info** | No `USER` directive — container runs as root. Acceptable given the pipeline server needs Docker socket access and git operations. The Docker socket mount (`/var/run/docker.sock`) already grants root-equivalent access regardless. |
| 3 | `COPY phases/` and `COPY scripts/pipeline_server.py` shadowed by volume mounts | **Info** | At runtime, `./phases:/data/phases:ro` and `./scripts:/data/scripts:ro` override the COPYed files. This is intentional: COPYs provide a baked-in fallback for standalone runs; volumes provide live development iteration. Not a bug. |
| 4 | Python package versions from mid-2024 | **Info** | All packages are pinned (good for reproducibility) but versions are ~6 months old. Periodic review for security patches recommended. No CVEs identified at refactor time. |

---

## Changes Made

None. Dockerfile.pipeline is correct and minimal.

---

## Test Results

| Check | Result |
|---|---|
| Image builds successfully | ✅ Confirmed via `docker compose up -d --build` in prior deploy |
| `pipeline_server` container healthy | ✅ `Up ... (healthy)` on both homelab and VPS |
| Port 5002 accessible | ✅ Healthcheck passes (`http://localhost:5002/health`) |
| Dependency consistency | ✅ All volumes match COPY destinations |

---

## Final State

`Dockerfile.pipeline` is correct and requires no changes.
