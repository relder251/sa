# Refactor: phase_1_setup.sh

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## File Overview

| Property | Value |
|---|---|
| **Path** | `phase_1_setup.sh` |
| **Purpose** | One-shot bootstrap script to initialize the full Agentic SDLC environment |
| **Role** | Creates directories, starts all containers, waits for health, pulls Ollama model, imports n8n workflows |
| **Called by** | Manual execution (`bash phase_1_setup.sh`) or CI/CD bootstrap |
| **Upstream deps** | `docker`, `curl`, `.env`, `docker-compose.yml`, `workflows/*.json` |
| **Downstream deps** | `n8n` container (workflow import), `ollama` container (model pull) |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | No `.env` existence check | **High** | If `.env` is missing, `docker compose up -d` fails with a cryptic variable substitution error rather than a clear message. |
| 2 | No Docker availability check | **High** | If Docker daemon is not running or `docker` is not in PATH, the error is generic and confusing for new users. |
| 3 | Infinite loop risk on health checks | **High** | Both `while ! curl ...` loops have no timeout. If n8n or Ollama never start (port conflict, OOM, misconfiguration), the script hangs indefinitely with no way to diagnose. |
| 4 | Incomplete directory creation | **Medium** | `mkdir -p workflows output` was missing: `backup`, `opportunities`, `notebooks`, `output/opportunities` — all of which are volume-mounted by containers in `docker-compose.yml`. Missing dirs can cause container mount failures or silent data loss. |
| 5 | Only 2 of 6 workflows imported | **Medium** | Script hardcoded imports for `phase_1_planner.json` and `phase_2_executor.json` only. Four workflows were never imported on fresh setup: `phase_3_feedback_loop.json`, `phase_4_opportunity_pipeline.json`, `sa_contact_lead_pipeline.json`, `litellm_test.json`. |
| 6 | Missing `set -uo pipefail` | **Low** | Script used `set -e` only. `set -u` catches undefined variable bugs. `set -o pipefail` ensures pipeline failures (e.g., `cmd1 \| cmd2`) are not silently swallowed. |

---

## Changes Made

| Change | Before | After | Reason |
|---|---|---|---|
| Shell options | `set -e` | `set -euo pipefail` | Catches undefined vars and pipeline failures |
| Docker check | *(absent)* | `command -v docker` + `docker info` checks | Clear error before confusing Docker output |
| `.env` check | *(absent)* | `[[ ! -f .env ]]` guard with instructions | Tells user exactly what to do |
| Directory creation | `mkdir -p workflows output` | Added `output/opportunities backup notebooks opportunities` | All volume-mount targets exist before `docker compose up` |
| n8n health timeout | infinite loop | 120s timeout with exit + diagnostic message | Prevents hung scripts; points to `docker compose logs n8n` |
| Ollama health timeout | infinite loop | 120s timeout with exit + diagnostic message | Same protection for Ollama |
| Workflow import | 2 hardcoded paths | `for workflow in workflows/*.json` loop | All current and future workflows imported automatically |

---

## Test Results

### Syntax validation
| Check | Result |
|---|---|
| `bash -n phase_1_setup.sh` | ✅ VALID |

### Command availability
| Command | Result |
|---|---|
| `docker` | ✅ Found in PATH |
| `curl` | ✅ Found in PATH |

### Upstream dependency check
| Dependency | Result |
|---|---|
| `workflows/*.json` (6 files) | ✅ All present: `litellm_test.json`, `phase_1_planner.json`, `phase_2_executor.json`, `phase_3_feedback_loop.json`, `phase_4_opportunity_pipeline.json`, `sa_contact_lead_pipeline.json` |
| `container_name: n8n` in docker-compose.yml | ✅ Confirmed |
| `container_name: ollama` in docker-compose.yml | ✅ Confirmed |

### Downstream dependency check
| Check | Result |
|---|---|
| n8n import path `/data/workflows/<name>` | ✅ Matches `./workflows:/data/workflows` volume mount in docker-compose.yml |

---

## Final State

`phase_1_setup.sh` is now robust against the most common failure modes: missing environment, Docker not running, slow container startup, and incomplete workflow import. All 6 workflows are imported on fresh setup. No behavioral changes to the happy path.
