#!/usr/bin/env python3
"""
free_model_sync.py — Discovers free models from providers and syncs
them into LiteLLM as a "free/*" tier group via the management API.

Providers checked:
  - OpenRouter  (queries /api/v1/models, filters prompt+completion cost == 0)
  - Groq        (static list — free at rate-limited tier)
  - Gemini      (static list — free at rate-limited tier)

Scheduling:
  Run via cron, n8n schedule trigger, or the companion docker service.
  Recommended: every 6 hours.

Usage:
  python3 free_model_sync.py
  python3 free_model_sync.py --dry-run   # print changes without applying
  python3 free_model_sync.py --verbose
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import requests

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("free_model_sync")

# ── Config (all overridable via env vars) ─────────────────────────────────────
LITELLM_BASE_URL   = os.getenv("LITELLM_BASE_URL",   "http://localhost:4000")
LITELLM_API_KEY    = os.getenv("LITELLM_API_KEY",    "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY",       "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY",     "")

# Tier prefix used in LiteLLM model_name
FREE_TIER_PREFIX = "free"

# Cap on how many free models to register per provider (avoid bloat)
MAX_OPENROUTER_MODELS = 30

# ── Data model ────────────────────────────────────────────────────────────────
@dataclass
class FreeModel:
    model_name: str        # LiteLLM alias, e.g. "free/chat"
    litellm_model: str     # provider/model string, e.g. "openrouter/meta-llama/..."
    api_key_env: str       # env var name holding the key
    api_base: Optional[str] = None
    context_length: int = 0
    description: str = ""
    tags: list = field(default_factory=list)


# ── Provider: OpenRouter ──────────────────────────────────────────────────────
def fetch_openrouter_free_models() -> list[FreeModel]:
    """Query OpenRouter API and return models with $0 prompt+completion cost."""
    if not OPENROUTER_API_KEY:
        log.warning("OPENROUTER_API_KEY not set — skipping OpenRouter")
        return []

    log.info("Fetching OpenRouter model catalog...")
    try:
        resp = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"OpenRouter API error: {e}")
        return []

    models = resp.json().get("data", [])
    free = []

    for m in models:
        pricing = m.get("pricing", {})
        try:
            prompt_cost     = float(pricing.get("prompt",     1))
            completion_cost = float(pricing.get("completion", 1))
        except (ValueError, TypeError):
            continue

        if prompt_cost == 0 and completion_cost == 0:
            model_id = m.get("id", "")
            if not model_id:
                continue

            ctx = m.get("context_length", 0)
            name = m.get("name", model_id)

            # Classify into task buckets based on model name keywords
            tags = classify_model_tags(model_id, name)

            free.append(FreeModel(
                model_name=f"{FREE_TIER_PREFIX}/{slugify(model_id)}",
                litellm_model=f"openrouter/{model_id}",
                api_key_env="OPENROUTER_API_KEY",
                context_length=ctx,
                description=f"{name} (free via OpenRouter, ctx={ctx})",
                tags=tags,
            ))

    # Sort by context length descending (bigger = better), cap list
    free.sort(key=lambda m: m.context_length, reverse=True)
    free = free[:MAX_OPENROUTER_MODELS]

    log.info(f"OpenRouter: found {len(free)} free models")
    return free


# ── Provider: Groq (free rate-limited tier) ───────────────────────────────────
def fetch_groq_free_models() -> list[FreeModel]:
    """Groq's free tier is static — these models are always free with rate limits."""
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set — skipping Groq")
        return []

    # Verify the key works and get the actual available models
    log.info("Verifying Groq free models...")
    try:
        resp = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        available = {m["id"] for m in resp.json().get("data", [])}
    except requests.RequestException as e:
        log.error(f"Groq API error: {e}")
        return []

    # Known free Groq models (rate-limited but always $0)
    candidates = [
        ("groq/llama-3.3-70b-versatile",     ["chat", "reason"],  128000),
        ("groq/llama-3.1-8b-instant",         ["chat", "fast"],     128000),
        ("groq/llama-3.2-3b-preview",         ["fast"],              8192),
        ("groq/llama-3.2-1b-preview",         ["fast"],              8192),
        ("groq/deepseek-r1-distill-llama-70b",["reason", "code"],   128000),
        ("groq/gemma2-9b-it",                 ["chat"],              8192),
        ("groq/mixtral-8x7b-32768",           ["chat"],             32768),
    ]

    free = []
    for model_str, tags, ctx in candidates:
        model_id = model_str.split("/", 1)[1]
        if model_id in available:
            free.append(FreeModel(
                model_name=f"{FREE_TIER_PREFIX}/{slugify(model_str)}",
                litellm_model=model_str,
                api_key_env="GROQ_API_KEY",
                context_length=ctx,
                description=f"{model_id} (free via Groq, rate-limited)",
                tags=tags,
            ))

    log.info(f"Groq: found {len(free)} free models")
    return free


# ── Provider: Gemini (free tier) ──────────────────────────────────────────────
def fetch_gemini_free_models() -> list[FreeModel]:
    """Gemini free tier models — verified via the models API."""
    if not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set — skipping Gemini")
        return []

    log.info("Fetching Gemini model list...")
    try:
        resp = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}",
            timeout=10,
        )
        resp.raise_for_status()
        available = {m["name"].replace("models/", "") for m in resp.json().get("models", [])}
    except requests.RequestException as e:
        log.error(f"Gemini API error: {e}")
        return []

    # Known free Gemini models (free under the Gemini API free tier)
    candidates = [
        ("gemini/gemini-2.0-flash",                    ["chat", "fast"],    1048576),
        ("gemini/gemini-2.0-flash-lite",               ["fast"],            1048576),
        ("gemini/gemini-1.5-flash",                    ["chat", "fast"],    1048576),
        ("gemini/gemini-1.5-flash-8b",                 ["fast"],            1048576),
        ("gemini/gemini-2.5-flash-preview-05-20",      ["chat", "reason"],  1048576),
    ]

    free = []
    for model_str, tags, ctx in candidates:
        model_id = model_str.split("/", 1)[1]
        if model_id in available:
            free.append(FreeModel(
                model_name=f"{FREE_TIER_PREFIX}/{slugify(model_str)}",
                litellm_model=model_str,
                api_key_env="GEMINI_API_KEY",
                context_length=ctx,
                description=f"{model_id} (free via Gemini API free tier)",
                tags=tags,
            ))

    log.info(f"Gemini: found {len(free)} free models")
    return free


# ── Task classification ────────────────────────────────────────────────────────
def classify_model_tags(model_id: str, name: str) -> list[str]:
    """Infer task tags from model name keywords."""
    combined = (model_id + " " + name).lower()
    tags = []
    if any(k in combined for k in ["code", "coder", "codestral", "starcoder", "deepseek-coder"]):
        tags.append("code")
    if any(k in combined for k in ["instruct", "chat", "hermes", "mistral", "llama", "gemma", "qwen"]):
        tags.append("chat")
    if any(k in combined for k in ["reason", "r1", "think", "o1", "qwq"]):
        tags.append("reason")
    if any(k in combined for k in ["8b", "7b", "3b", "1b", "mini", "flash", "lite", "tiny"]):
        tags.append("fast")
    if not tags:
        tags.append("chat")
    return list(set(tags))


def slugify(model_id: str) -> str:
    """Turn openrouter/meta-llama/llama-3-8b into a clean alias."""
    # Strip provider prefix, keep the model part
    parts = model_id.split("/")
    slug = parts[-1] if len(parts) > 1 else model_id
    return slug[:64]  # LiteLLM model_name length limit


# ── LiteLLM management API ────────────────────────────────────────────────────
def get_current_free_models() -> dict[str, str]:
    """
    Returns {model_name: model_id} for all models currently registered
    in LiteLLM with the free/* prefix.
    """
    try:
        resp = requests.get(
            f"{LITELLM_BASE_URL}/model/info",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Failed to fetch current LiteLLM models: {e}")
        return {}

    current = {}
    for m in resp.json().get("data", []):
        name = m.get("model_name", "")
        if name.startswith(f"{FREE_TIER_PREFIX}/"):
            current[name] = m.get("model_info", {}).get("id", "")
    return current


def register_model(model: FreeModel, dry_run: bool = False) -> bool:
    """Add a free model to LiteLLM via the management API."""
    payload = {
        "model_name": model.model_name,
        "litellm_params": {
            "model": model.litellm_model,
            "api_key": f"os.environ/{model.api_key_env}",
            **({"api_base": model.api_base} if model.api_base else {}),
        },
        "model_info": {
            "description": model.description,
            "context_length": model.context_length,
            "tags": model.tags,
            "tier": "free",
        },
    }

    if dry_run:
        log.info(f"[DRY RUN] Would ADD: {model.model_name} → {model.litellm_model}")
        return True

    try:
        resp = requests.post(
            f"{LITELLM_BASE_URL}/model/new",
            headers={
                "Authorization": f"Bearer {LITELLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        log.info(f"  ✅ Added: {model.model_name} → {model.litellm_model}")
        return True
    except requests.RequestException as e:
        log.error(f"  ❌ Failed to add {model.model_name}: {e}")
        return False


def deregister_model(model_name: str, model_id: str = "", dry_run: bool = False) -> bool:
    """Remove a stale free model from LiteLLM by its internal DB id."""
    if dry_run:
        log.info(f"[DRY RUN] Would REMOVE: {model_name} (id={model_id})")
        return True

    delete_id = model_id if model_id else model_name
    try:
        resp = requests.post(
            f"{LITELLM_BASE_URL}/model/delete",
            headers={
                "Authorization": f"Bearer {LITELLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"id": delete_id},
            timeout=10,
        )
        resp.raise_for_status()
        log.info(f"  🗑️  Removed stale: {model_name} (id={delete_id})")
        return True
    except requests.RequestException as e:
        log.error(f"  ❌ Failed to remove {model_name} (id={delete_id}): {e}")
        return False

# ── Free tier group aliases ───────────────────────────────────────────────────
FREE_TIER_GROUPS = {
    "free/chat":   "chat",
    "free/code":   "code",
    "free/reason": "reason",
    "free/fast":   "fast",
}

def sync_tier_groups(all_free: list[FreeModel], dry_run: bool = False):
    """
    Register/update the convenience group aliases (free/chat, free/code, etc.)
    that round-robin across all free models with matching tags.
    These are separate model_name entries that all point to tagged models.
    """
    for group_name, tag in FREE_TIER_GROUPS.items():
        matching = [m for m in all_free if tag in m.tags]
        if not matching:
            log.warning(f"No free models found for group {group_name}")
            continue

        # Register each matching model under the group alias too
        for m in matching[:10]:  # cap group size at 10
            group_model = FreeModel(
                model_name=group_name,
                litellm_model=m.litellm_model,
                api_key_env=m.api_key_env,
                api_base=m.api_base,
                context_length=m.context_length,
                description=m.description,
                tags=m.tags,
            )
            register_model(group_model, dry_run=dry_run)

        log.info(f"Group {group_name}: registered {min(len(matching), 10)} models")


# ── Main sync logic ───────────────────────────────────────────────────────────
def sync(dry_run: bool = False, verbose: bool = False):
    if verbose:
        log.setLevel(logging.DEBUG)

    if not LITELLM_API_KEY:
        log.error("LITELLM_API_KEY not set — cannot call management API")
        sys.exit(1)

    log.info("=" * 60)
    log.info("Starting free model sync")
    log.info("=" * 60)

    # 1. Discover free models from all providers
    discovered: list[FreeModel] = []
    discovered += fetch_openrouter_free_models()
    discovered += fetch_groq_free_models()
    discovered += fetch_gemini_free_models()

    if not discovered:
        log.warning("No free models discovered — check API keys and connectivity")
        return

    log.info(f"Total free models discovered: {len(discovered)}")

    # 2. Get what's currently in LiteLLM
    current = get_current_free_models()
    log.info(f"Currently registered free models: {len(current)}")

    # 3. Compute diff
    discovered_names = {m.model_name for m in discovered}
    current_names    = set(current.keys())

    to_add    = [m for m in discovered if m.model_name not in current_names]
    to_remove = current_names - discovered_names - set(FREE_TIER_GROUPS.keys())

    log.info(f"To add:    {len(to_add)}")
    log.info(f"To remove: {len(to_remove)}")

    # 4. Apply removals first
    removed = 0
    for name in to_remove:
        if deregister_model(name, model_id=current.get(name, ""), dry_run=dry_run):
            removed += 1

    # 5. Apply additions
    added = 0
    for model in to_add:
        if register_model(model, dry_run=dry_run):
            added += 1

    # 6. Sync convenience tier groups (free/chat, free/code, etc.)
    log.info("Syncing free tier group aliases...")
    sync_tier_groups(discovered, dry_run=dry_run)

    # 7. Summary
    log.info("=" * 60)
    log.info(f"Sync complete — added: {added}, removed: {removed}")
    if dry_run:
        log.info("DRY RUN — no changes were applied")
    log.info("=" * 60)

    # 8. Print a summary of available free groups for easy reference
    log.info("Available free tier model groups:")
    for group in FREE_TIER_GROUPS:
        log.info(f"  {group}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync free LLM models into LiteLLM")
    parser.add_argument("--dry-run",  action="store_true", help="Print changes without applying")
    parser.add_argument("--verbose",  action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    sync(dry_run=args.dry_run, verbose=args.verbose)
