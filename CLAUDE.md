# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

An **Agentic SDLC** environment: a Docker-based stack that orchestrates local and cloud LLMs through a unified proxy (LiteLLM), uses n8n for multi-phase AI workflow automation, and provides JupyterLab for interactive development. The system is designed to take a raw idea through automated planning (Phase 1) and execution (Phase 2) phases.

## Stack Architecture

```
n8n (port 5678)  ──▶  LiteLLM proxy (port 4000)  ──▶  Ollama (local, port 11434)
                                                   ──▶  Cloud providers (Anthropic, OpenAI, Gemini, etc.)
JupyterLab (port 8888)  ──▶  same LiteLLM proxy
free-model-sync  ──▶  LiteLLM /model/new API  (runs every 6h via Ofelia)
Watchtower  ──▶  updates all labelled containers nightly at 03:00
PostgreSQL (port 5432)  ──▶  LiteLLM spend/key tracking
```

**Shared volumes between services:**
- `./workflows` → mounted into n8n at `/data/workflows`
- `./output` → mounted into both n8n (`/data/output`) and JupyterLab (`~/output`)
- `./notebooks` → JupyterLab workspace

## Key Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Full stack definition |
| `litellm_config.yaml` | LiteLLM model list and router config (tiered model groups) |
| `free_model_sync.py` | Discovers free models from OpenRouter/Groq/Gemini and syncs into LiteLLM |
| `ofelia.ini` | Cron schedule: runs `free_model_sync.py` every 6 hours |
| `phase_1_setup.sh` | Bootstrap script: starts stack, pulls Ollama models, imports n8n workflows |
| `workflows/phase_1_planner.json` | n8n workflow: POST `/webhook/generate-plan` → LiteLLM → save to `output/project_plan.md` |
| `workflows/phase_2_executor.json` | n8n workflow: executes phases from the plan |
| `.env` | All API keys and secrets (never commit) |

## Model Tier System

LiteLLM exposes four named tiers to n8n and notebooks. Call any tier with the LiteLLM base URL:

| Tier | Model groups | Use case |
|------|-------------|----------|
| `local/*` | `local/chat`, `local/code`, `local/reason`, `local/fast` | Zero cost, on-device only (Ollama) |
| `hybrid/*` | same suffixes | Ollama first, falls back to Groq/Gemini/DeepSeek |
| `cloud/*` | `cloud/chat`, `cloud/smart`, `cloud/code`, `cloud/reason`, `cloud/fast`, `cloud/search` | Premium providers (Anthropic, OpenAI, Gemini, Perplexity) |
| `free/*` | `free/chat`, `free/code`, `free/reason`, `free/fast` | Dynamically populated by `free_model_sync.py` |

Router strategy: `least-busy` within a group; cross-tier fallbacks defined in `litellm_config.yaml` under `router_settings.fallbacks`.

## Common Commands

**Start the full stack:**
```bash
bash phase_1_setup.sh
# or just:
docker compose up -d
```

**Stop the stack:**
```bash
docker compose down
```

**View logs for a specific service:**
```bash
docker compose logs -f litellm
docker compose logs -f n8n
docker compose logs -f free-model-sync
```

**Trigger Phase 1 planning workflow (after stack is up):**
```bash
curl -X POST http://localhost:5678/webhook/agentic-planner-002/webhook/generate-plan \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Build a REST API for task management"}'
```

**Run free model sync manually:**
```bash
docker exec free_model_sync python /app/free_model_sync.py
# Dry-run (no changes):
docker exec free_model_sync python /app/free_model_sync.py --dry-run --verbose
```

**Pull a new Ollama model:**
```bash
docker exec ollama ollama pull <model-name>
docker exec ollama ollama list
```

**Import/update an n8n workflow:**
```bash
docker exec n8n n8n import:workflow --input=/data/workflows/phase_1_planner.json
```

**Generate a LiteLLM tier key (after stack is healthy):**
```bash
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"models": ["hybrid/chat","hybrid/code"], "metadata": {"tier":"hybrid"}}'
```

**Access services:**
- n8n UI: http://localhost:5678
- LiteLLM UI / Swagger: http://localhost:4000
- JupyterLab: http://localhost:8888 (token: `vibe-dev-token` by default)

## Environment Setup

Copy `.env` and fill in your own keys before starting:
```bash
cp .env .env.local  # keep a backup
```

Required variables: `LITELLM_API_KEY`, plus at minimum one of the cloud provider keys. `OLLAMA_*` keys are optional (local Ollama needs no key by default).

## Modifying the Model Config

`litellm_config.yaml` uses a two-section pattern:
1. **Individual models** (prefixed `_`) — building blocks not directly callable by users.
2. **Tier groups** — multiple entries with the same `model_name` form a load-balanced pool.

After editing `litellm_config.yaml`, restart LiteLLM to pick up changes:
```bash
docker compose restart litellm
```

The `free/*` tier is managed dynamically by `free_model_sync.py` and does **not** live in `litellm_config.yaml` — it is written to LiteLLM's database via the management API.

## GPU / Hardware Notes

Ollama is configured for an RTX 3070 (8GB VRAM):
- `OLLAMA_MAX_LOADED_MODELS=1` — only one model in VRAM at a time
- `OLLAMA_KEEP_ALIVE=10m` — unload model after 10 min idle

If running without a GPU, remove the `deploy.resources.reservations` block from the `ollama` service in `docker-compose.yml`.
