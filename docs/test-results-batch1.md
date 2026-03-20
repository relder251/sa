# Test Results — Batch 1 (T-13/T-14/T-15/T-19/T-06/T-07)
**Date:** 2026-03-20
**Tester:** Agent

| Task | Static Check | Runtime Check | Status |
|------|-------------|---------------|--------|
| T-13 | ✅ `sync_tier_groups(all_free, current, dry_run)` accepts `current` dict; deregisters stale entries before re-registering; `current` passed from caller | ❌ VPS still runs old image (no `current` param); 370 duplicate `free/chat` entries in LiteLLM — dedup not active | FAIL |
| T-14 | ✅ `slugify()` produces `{provider}-{base}` format; OpenRouter call passes `f"openrouter/{model_id}"`; slug capped at 64 chars; `_clean()` removes colons/slashes | ❌ VPS container runs old code: `slugify("openrouter/google/gemma-3-27b-it:free")` returns `gemma-3-27b-it:free` (no provider prefix, colon not cleaned) | FAIL |
| T-15 | ✅ `import tempfile` present (line 9); `mypy_report_dir = tempfile.mkdtemp(prefix=f"mypy_{name}_")` at line 132 (before `try`); `finally: shutil.rmtree(mypy_report_dir, ignore_errors=True)` at line 170–171; no duplicate `import shutil`; no `/tmp/mypy_report_` hardcoded strings | N/A (local-only check) | PASS |
| T-19 | ✅ `pipeline_server.py` writes only to `phase4_report.md` (lines 84, 610); `test_runner_server.py` writes only to `phase4_report.md` (lines 360, 484); `phase9_monitoring.py` reads `phase4_report.md` first, `phase3_report.md` as fallback (line 29); no `open(..., 'w')` to `phase3_report.md` in scripts/ or phases/ | N/A (static only) | PASS |
| T-06 | ✅ `DEPLOY_PORT = os.environ.get("DEPLOY_PORT", "8000").strip()` at line 62; docker run uses `-p 0:{DEPLOY_PORT}` (line 115); SSH endpoint uses `http://{DEPLOY_SSH_HOST}:{DEPLOY_PORT}` (line 250); `docker-compose.prod.yml` has `- DEPLOY_PORT=${DEPLOY_PORT:-8000}` (line 383); only remaining `8000` in phase8 is the default string and a comment | N/A (static only) | PASS |
| T-07 | ✅ `DOCKER_REGISTRY` read at SSH block scope (line 202) with comment stripping; log line announces which path (lines 206–208); `if DOCKER_REGISTRY:` uses `up -d`, `else:` uses `up -d --build`; `--build` path has timeout=300s (≥180s); condition is NOT inverted (registry set → no build, unset → build) | N/A (static only) | PASS |

## Issues Found

### T-13 + T-14: VPS container not updated — fixes not deployed

The local repo commits (`65b20fa` for T-13, `4f8d688` for T-14) are correct and verified. However the VPS `free_model_sync` container is still running the old Docker image built from commit `d6db990` (pre-fix). Evidence:

1. **T-14 VPS slug test:** `slugify("openrouter/google/gemma-3-27b-it:free")` returns `gemma-3-27b-it:free` instead of the expected `openrouter-gemma-3-27b-it-free`. The old code is `slug = parts[-1] if len(parts) > 1 else model_id` (no provider prefix, no colon cleaning).

2. **T-13 VPS duplicate count:** `free/chat` has **370** entries in LiteLLM. The `sync_tier_groups` function in the deployed container has signature `sync_tier_groups(all_free, dry_run)` — no `current` parameter — so dedup never runs. The dry-run output shows correct NEW behavior (deregisters before re-registering), but this is the local script talking to the remote LiteLLM, not the container's own scheduled sync.

3. **Root cause:** The VPS git repo is at commit `d6db990`; the fix commits (`65b20fa`, `4f8d688`) have not been pulled and the container has not been rebuilt. `docker compose pull && docker compose up -d --build free-model-sync` is required on the VPS.

### T-13: Residual architectural gap (present in fixed code too)

Even in the fixed local code, `get_current_free_models()` returns `dict[str, str]` keyed by `model_name`. Since multiple LiteLLM entries share the same `model_name` (e.g. 370 × `free/chat`), the dict collapses them to one entry. `sync_tier_groups` therefore can only deregister at most 1 stale entry per group alias per run. The fix prevents new duplicates from accumulating going forward (once deployed), but cannot clear existing accumulated duplicates in a single run. A one-time manual purge of the 370/354/74/37 existing group-alias duplicates is needed on the VPS.

## Verdict

**PARTIAL** — 4 of 6 tasks pass fully (T-15, T-19, T-06, T-07). T-13 and T-14 pass static/local checks but FAIL runtime checks because the VPS container has not been rebuilt from the fixed commits.

**Required actions before closing T-13/T-14:**
1. On VPS: `git pull && docker compose build free-model-sync && docker compose up -d free-model-sync`
2. On VPS: manually purge the ~835 accumulated group-alias duplicate entries from LiteLLM DB (or flush and re-sync), then verify `free/chat` count drops to ≤10.
