# Project Context

## Repository
- **Remote**: git@github.com:relder251/sa.git
- **Branch**: master
- **Local path**: /home/user/vibe_coding/Agentic_SDLC

## Stack
This is an Agentic SDLC environment — a Docker-based stack that orchestrates local and cloud LLMs through a unified proxy.

| Service | URL | Purpose |
|---|---|---|
| LiteLLM proxy | http://localhost:4000 | Unified LLM API (local + cloud) |
| n8n | http://localhost:5678 | Workflow automation (Phase 1 planner, Phase 2 executor) |
| JupyterLab | http://localhost:8888 | Interactive notebooks (token: $JUPYTER_TOKEN) |
| Ollama | http://localhost:11434 | Local LLM inference (RTX 3070) |
| PostgreSQL | localhost:5432 | LiteLLM spend/key tracking |

## LiteLLM Model Tiers
- `local/*` — Ollama only (zero cost)
- `hybrid/*` — Ollama first, falls back to Groq/Gemini/DeepSeek
- `cloud/*` — Premium providers (Anthropic, OpenAI, Gemini, Perplexity)
- `free/*` — Dynamically populated by free_model_sync.py

## Key Files
- `docker-compose.yml` — Full stack definition
- `litellm_config.yaml` — Model list and router config
- `free_model_sync.py` — Discovers and syncs free models every 6h
- `.env` — All API keys and secrets (never commit)
- `workflows/phase_1_planner.json` — n8n Phase 1 workflow
- `workflows/phase_2_executor.json` — n8n Phase 2 workflow

## Common Commands
```bash
docker compose up -d          # Start stack
docker compose down           # Stop stack
docker compose logs -f n8n    # Tail n8n logs
docker exec ollama ollama list # List loaded models
```

## Rules
- Never commit `.env` or any file containing API keys
- Always restart LiteLLM after editing `litellm_config.yaml`
- The `free/*` tier is managed dynamically — do not add it to litellm_config.yaml
