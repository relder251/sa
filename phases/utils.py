"""
Shared utilities for pipeline phases.

Imported by phase5_quality_gate and phase6_documentation to avoid duplicating
LiteLLM call logic and source-file reading across phase modules.
pipeline_server.py uses its own _llm() (different interface: fallback, retry).
"""
import os
from pathlib import Path


LITELLM_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-vibe-coding-key-123")


def read_source_files(project_dir: Path) -> dict:
    """Collect all non-venv, non-cache project files as {rel_path: text}."""
    skip_dirs = {".venv", "__pycache__", ".pytest_cache", ".git", "node_modules"}
    files = {}
    for f in sorted(project_dir.rglob("*")):
        if f.is_dir():
            continue
        parts = set(f.relative_to(project_dir).parts)
        if parts & skip_dirs:
            continue
        if f.suffix in (".pyc", ".pyo", ".egg-info"):
            continue
        rel = str(f.relative_to(project_dir))
        try:
            files[rel] = f.read_text(errors="replace")
        except Exception:
            pass
    return files


def call_llm(model: str, system: str, user: str, max_tokens: int = 1024, timeout: int = 180) -> str:
    """Call LiteLLM synchronously. Raises on HTTP error or null content."""
    import requests as http
    resp = http.post(
        f"{LITELLM_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {LITELLM_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "timeout": max(timeout - 30, 60),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    if content is None:
        raise ValueError(f"LiteLLM returned null content for model={model}")
    return content
