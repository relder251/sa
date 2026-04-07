# Refactor: litellm_config.yaml

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## File Overview

| Property | Value |
|---|---|
| **Path** | `litellm_config.yaml` |
| **Purpose** | LiteLLM proxy configuration — model registry, tier groups, router settings, and global settings |
| **Role** | Loaded by LiteLLM at startup; defines all static model registrations. Dynamic `free/*` models are managed separately by `free_model_sync.py` via the management API. |
| **Loaded by** | `litellm` container via `--config /app/config.yaml` (mounted from `./litellm_config.yaml`) |
| **Upstream deps** | All provider API keys in `.env` (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, etc.) |
| **Downstream deps** | n8n workflows, JupyterLab notebooks, `free_model_sync.py` (calls `/model/info`, `/model/new`, `/model/delete`) |

### Architecture: Two-section pattern
| Section | Purpose |
|---|---|
| Individual `_` models | Named building blocks callable directly via master key using `_<name>` |
| Tier groups | `local/*`, `hybrid/*`, `cloud/*` — load-balanced pools; `free/*` managed by `free_model_sync.py` |

### Tier summary
| Tier | Groups | Use case |
|---|---|---|
| `local/*` | `chat`, `code`, `reason`, `fast` | Ollama only — zero cost, on-device |
| `hybrid/*` | `chat`, `code`, `reason`, `fast` | Ollama → Groq → Gemini → DeepSeek |
| `cloud/*` | `chat`, `smart`, `code`, `reason`, `fast`, `search` | Premium providers |
| `free/*` | `chat`, `code`, `reason`, `fast` | Dynamically managed by `free_model_sync.py` |

### Individual models NOT in any tier group (accessible via `_` name with master key only)
| Model | Provider | Notes |
|---|---|---|
| `_gemini-flash-25` | `gemini/gemini-2.5-flash-preview-05-20` | Preview model — may require ID update when GA |
| `_mistral-small` | `mistral/mistral-small-latest` | Available for direct use |
| `_o4-mini` | `openai/o4-mini` | Reasoning/fast cloud; candidate for `cloud/fast` if needed |
| `_mistral-large` | `mistral/mistral-large-latest` | Available for direct use |
| `_sambanova-405b` | `sambanova/Meta-Llama-3.1-405B-Instruct` | Large model for specialist use |
| `_openrouter-qwen3` | `openrouter/qwen/qwen3-235b-a22b` | MoE reasoning via OpenRouter |
| `_venice-llama3` | `openai/llama-3.3-70b` via Venice | Privacy-focused inference |
| `_venice-mistral` | `openai/mistral-31-24b` via Venice | Privacy-focused inference |
| `_venice-admin` | `openai/llama-3.3-70b` via Venice Admin key | Admin key variant |
| `_hf-zephyr` | `huggingface/HuggingFaceH4/zephyr-7b-beta` | HuggingFace inference (2023 model) |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | `claude-sonnet-4-5-20251001` does not exist | **High** | The model ID `anthropic/claude-sonnet-4-5-20251001` is not in LiteLLM 1.82.4's registry (`get_model_info` raises "This model isn't mapped yet") and does not correspond to any real Anthropic release date. The correct date-pinned ID is `claude-sonnet-4-5-20250929` (Anthropic released Claude Sonnet 4.5 on 2025-09-29). This affected `_claude-sonnet`, `cloud/chat`, and `cloud/code` — all three had broken model specs. |
| 2 | `cloud/search` had no fallback defined | **Medium** | All 14 static tier groups were in the fallback chain except `cloud/search`. If Perplexity is unavailable, requests to `cloud/search` would fail with no escalation path. |
| 3 | `_gemini-flash-25` uses a preview model ID | **Low** | `gemini/gemini-2.5-flash-preview-05-20` has a preview date suffix (`05-20`). Preview model IDs frequently change when models go GA. Monitor for deprecation. No change made — it's accessible via `_gemini-flash-25` and is not in any tier group. |
| 4 | `timeout: 900` is permissive | **Low** | 900s (15 minutes) is high for a router-level timeout. It accommodates slow local Ollama inference (cold starts, large prompts) but means a hung model holds connections for up to 15 minutes. Acceptable given the use of local models; no change made. |
| 5 | `sync_tier_groups` duplicates not reflected here | **Low** | `free_model_sync.py` adds models to `free/chat` etc. on every run without pruning existing group entries (tracked in `refactor/free_model_sync.py.md`). This file defines the static tiers; the dynamic `free/*` growth is a separate concern. |
| 6 | `_o4-mini` defined but not in any tier group | **Info** | `openai/o4-mini` is a capable fast/reasoning model but only accessible via its `_` name. Candidate for `cloud/fast` or `cloud/reason`. Not added in this pass — functional gap, not a bug. |

---

## Changes Made

| Change | Before | After | Reason |
|---|---|---|---|
| Fixed Claude Sonnet model ID | `anthropic/claude-sonnet-4-5-20251001` | `anthropic/claude-sonnet-4-5-20250929` | Wrong date — model ID doesn't exist in LiteLLM registry or Anthropic API. Fixed in `_claude-sonnet`, `cloud/chat`, and `cloud/code` (3 occurrences). |
| Added `cloud/search` fallback | *(absent)* | `- cloud/search: ["hybrid/chat", "free/chat"]` | Only tier group missing a fallback; ensures Perplexity outage degrades gracefully |

---

## Test Results

### Syntax validation
| Check | Result |
|---|---|
| `python3 -c "import yaml; yaml.safe_load(open('litellm_config.yaml'))"` | ✅ VALID |
| `docker compose config --quiet` | ✅ VALID |

### Model ID validation (via LiteLLM 1.82.4 registry)
| Model ID | Before | After | Registry check |
|---|---|---|---|
| `anthropic/claude-sonnet-4-5-20251001` | In config | Replaced | ❌ `get_model_info` → "This model isn't mapped yet" |
| `anthropic/claude-sonnet-4-5-20250929` | — | In config | ✅ KNOWN — max_tokens: 64000 |
| `anthropic/claude-opus-4-5-20251101` | In config | Unchanged | ✅ KNOWN — max_tokens: 64000 |
| `anthropic/claude-haiku-4-5-20251001` | In config | Unchanged | ✅ KNOWN — max_tokens: 64000 |
| `routing_strategy: least-busy` | In config | Unchanged | ✅ `Router(routing_strategy='least-busy')` accepted |

### Fallback coverage
| Check | Result |
|---|---|
| All tier groups have a fallback | ✅ 14/14 — `cloud/search` gap fixed |
| Tier groups cross-check | ✅ `['cloud/chat','cloud/code','cloud/fast','cloud/reason','cloud/search','cloud/smart','hybrid/chat','hybrid/code','hybrid/fast','hybrid/reason','local/chat','local/code','local/fast','local/reason']` |

### Live validation (post-restart)
| Check | Result |
|---|---|
| `docker compose restart litellm` | ✅ Restarted, healthy within 30s |
| LiteLLM `/health` | ✅ 200 OK |
| `claude-sonnet-4-5-20250929` entries in `/model/info` | ✅ 3 entries (`_claude-sonnet`, `cloud/chat`, `cloud/code`) |
| `cloud/search` entries in `/model/info` | ✅ 2 entries (`sonar-pro`, `sonar-reasoning-pro`) |
| Total registered model entries | ✅ 1045 (unchanged — static + dynamic free) |

### Upstream dependency check
| Provider key | Status |
|---|---|
| `ANTHROPIC_API_KEY` | ✅ Present in `.env` |
| `OPENAI_API_KEY` | ✅ Present in `.env` |
| `GEMINI_API_KEY` | ✅ Present in `.env` |
| `GROQ_API_KEY` | ✅ Present in `.env` |
| `DEEPSEEK_API_KEY` | ✅ Present in `.env` |
| `MISTRAL_API_KEY` | ✅ Present in `.env` |
| `PERPLEXITY_API_KEY` | ✅ Present in `.env` |
| `OPENROUTER_API_KEY` | ✅ Present in `.env` |
| `VENICE_API_KEY` | ✅ Present in `.env` |
| `VENICE_ADMIN_API_KEY` | ✅ Present in `.env` |
| `SAMBANOVA_API_KEY` | ✅ Present in `.env` |
| `HUGGINGFACE_API_KEY` | ✅ Present in `.env` |
| `OLLAMA_CLOUD_API_BASE` | ✅ Present in `.env` |
| `OLLAMA_CLOUD_API_KEY` | ✅ Present in `.env` |

---

## Final State

`litellm_config.yaml` is now fully valid: the broken Claude Sonnet model ID is corrected (the only model ID that was unmapped in LiteLLM's registry), and all 14 static tier groups have fallback chains. All individual `_` models remain intact and accessible via master key. No models were removed. LiteLLM restarted cleanly with the updated config.
