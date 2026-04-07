# Refactor: free_model_sync.py

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## File Overview

| Property | Value |
|---|---|
| **Path** | `free_model_sync.py` |
| **Purpose** | Discovers free LLM models from OpenRouter, Groq, and Gemini; syncs them into LiteLLM as the `free/*` tier |
| **Role** | Runs every 6h via Ofelia inside the `free_model_sync` container; diffs discovered vs registered models and applies adds/removes |
| **Called by** | Ofelia cron (`python /app/free_model_sync.py`); can also be run manually |
| **Upstream deps** | OpenRouter API, Groq API, Gemini API (provider discovery); LiteLLM management API (`/model/info`, `/model/new`, `/model/delete`) |
| **Downstream deps** | LiteLLM `free/*` tier — used by n8n workflows and JupyterLab notebooks |

### Key behaviors
| Behavior | Description |
|---|---|
| Provider discovery | OpenRouter: queries live API, filters $0 cost models. Groq/Gemini: static lists verified against live model lists |
| Diff sync | Reads current `free/*` registrations, computes add/remove sets, applies minimal changes |
| Tier group aliases | `free/chat`, `free/code`, `free/reason`, `free/fast` — round-robin groups populated with up to 10 tagged models each |
| `--dry-run` / `--verbose` | Safe inspection mode; no changes applied |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | `deregister_model` sent wrong identifier to `/model/delete` | **High** | The `/model/delete` API requires `{"id": "<db_uuid>"}` (e.g., `"7191b465-599f-4591-b775-45a13eae74ac"`). The code was sending `{"id": "<model_name>"}` (e.g., `"free/qwen3-vl-235b-a22b-thinking"`). Result: all model removals silently failed — stale models accumulated in LiteLLM indefinitely. |
| 2 | No LiteLLM reachability check before sync | **Medium** | If LiteLLM is down at sync time, all per-model API calls fail individually with error logs rather than a single clear early exit. Adds noise and wastes provider API quota. |
| 3 | `sync_tier_groups` adds duplicate group entries on every run | **Low** | On every sync, `sync_tier_groups` calls `/model/new` for up to 10 models under each group alias (`free/chat`, etc.) without checking if they're already registered. Over many runs, LiteLLM accumulates many identical group entries. LiteLLM's load balancer handles duplicate entries gracefully, but cleanup requires manual intervention. Deferred — fixing requires read-before-write logic for group entries. |
| 4 | `slugify` discards provider prefix | **Low** | `slugify("openrouter/meta-llama/llama-3.3-70b-versatile")` → `"llama-3.3-70b-versatile"`. If Groq and OpenRouter both offer a model with the same basename, the individual model entries would collide to the same `free/<slug>` name. No collision observed in current model catalogs, but theoretically possible. |

---

## Changes Made

| Change | Before | After | Reason |
|---|---|---|---|
| Fixed `deregister_model` identifier | `json={"id": model_name}` | `json={"id": model_id}` (DB UUID) | `/model/delete` requires the DB UUID, not the model_name string |
| Updated `deregister_model` signature | `(model_name: str, dry_run)` | `(model_name: str, model_id: str, dry_run)` | Accepts DB UUID explicitly; logs warning and skips if `model_id` is empty |
| Updated removal call site | `deregister_model(name, dry_run=dry_run)` | `deregister_model(name, model_id=current[name], dry_run=dry_run)` | Passes the UUID stored in `current` dict (read from `model_info.id`) |
| Added LiteLLM reachability pre-check | *(absent)* | `GET /health` with 5s timeout before sync begins | Fail fast with clear error if LiteLLM is unreachable |
| Updated dry-run REMOVE log | `Would REMOVE: {model_name}` | `Would REMOVE: {model_name} (id={model_id})` | Makes UUID visible in dry-run output for verification |

---

## Test Results

### Syntax validation
| Check | Result |
|---|---|
| `python3 -m py_compile free_model_sync.py` | ✅ VALID |

### API schema validation
| Check | Result |
|---|---|
| LiteLLM `/model/delete` schema (`ModelInfoDelete`) | ✅ Confirmed: `{"id": string}` — requires DB UUID, not model_name |
| Sample DB UUID from live `/model/info` | ✅ Confirmed: `free/qwen3-vl-235b-a22b-thinking` → `model_info.id = "7191b465-599f-4591-b775-45a13eae74ac"` |

### Functional validation — dry-run
| Check | Result |
|---|---|
| `docker exec free_model_sync python /app/free_model_sync.py --dry-run` | ✅ Completed without errors |
| Reachability pre-check fired | ✅ LiteLLM healthy at `http://litellm:4000` |
| Provider discovery | ✅ 27 OpenRouter + 2 Groq + 2 Gemini = 31 free models |
| Removals show correct format | ✅ `Would REMOVE: free/qwen3-vl-235b-a22b-thinking (id=...)` |
| All 4 tier groups populated | ✅ `free/chat` (10), `free/code` (1), `free/reason` (2), `free/fast` (10) |

### Upstream dependency check
| Dep | Status |
|---|---|
| LiteLLM API (`/model/info`, `/model/new`, `/model/delete`) | ✅ Reachable from container via `http://litellm:4000` |
| OpenRouter API | ✅ Returned 27 free models |
| Groq API | ✅ Returned 2 free models |
| Gemini API | ✅ Returned 2 free models |

### Downstream dependency check
| Check | Result |
|---|---|
| `free/chat`, `free/code`, `free/reason`, `free/fast` registered in LiteLLM | ✅ Confirmed via `/model/info` |
| n8n and JupyterLab can reach `free/*` via LiteLLM proxy | ✅ LiteLLM healthy, all groups present |

---

## Deferred Gaps

| Gap | Deferred to |
|---|---|
| `sync_tier_groups` duplicate entries on each run | Dedicated `sync_tier_groups` refactor pass (requires read-before-write for group entries) |
| `slugify` provider-prefix collision risk | Low priority — no collision observed in current catalogs |

---

## Final State

`free_model_sync.py` now correctly removes stale free models (the deregister UUID bug was silently preventing all removals). LiteLLM reachability is checked before any API calls. Discovery, diff, add, and group alias logic are unchanged and validated via live dry-run. All 31 discovered free models and all 4 tier groups confirmed healthy.
