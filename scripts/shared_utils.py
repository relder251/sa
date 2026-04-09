#!/usr/bin/env python3
"""
Shared utilities for pipeline_server.py and test_runner_server.py.

Extracted to eliminate duplication between the two server scripts.
Functions here are framework-agnostic (no Flask/FastAPI imports).

Do NOT put LLM routing helpers (_llm with fallback), route handlers, or
startup logic here — those live in the individual server files.
"""
import hashlib
import os
import re
import subprocess
import venv
from pathlib import Path

import requests as http


# ── LiteLLM connection defaults ───────────────────────────────────────────────

LITELLM_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-sa-prod-ce5d031e2a50ffa45d3a200c037971f81853e27ed19b894bc3630625cba0b71a")


# ── venv management ───────────────────────────────────────────────────────────

def _pip(venv_dir: Path, args: list):
    pip = venv_dir / "bin" / "pip"
    env = os.environ.copy()
    env["HOME"] = "/tmp"
    r = subprocess.run([str(pip)] + args, capture_output=True, text=True, env=env)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def ensure_venv(project_dir: Path):
    """Create/update a .venv in the project dir, caching on requirements.txt hash."""
    req_file = project_dir / "requirements.txt"
    venv_dir = project_dir / ".venv"
    hash_file = venv_dir / ".req_hash"

    if not req_file.exists():
        if not (venv_dir / "bin" / "pip").exists():
            venv.create(str(venv_dir), with_pip=True)
            _pip(venv_dir, ["install", "pytest", "-q"])
        return True, venv_dir, "No requirements.txt — using minimal venv"

    current_hash = hashlib.md5(req_file.read_bytes()).hexdigest()

    if (
        hash_file.exists()
        and hash_file.read_text().strip() == current_hash
        and (venv_dir / "bin" / "pytest").exists()
    ):
        return True, venv_dir, "Deps unchanged — skipped install"

    if not (venv_dir / "bin" / "pip").exists():
        venv.create(str(venv_dir), with_pip=True)

    ok, out = _pip(venv_dir, ["install", "-r", str(req_file), "pytest", "-q"])
    if ok:
        hash_file.write_text(current_hash)
    return ok, venv_dir, out


# ── test execution ────────────────────────────────────────────────────────────

def run_pytest(project_dir: Path, venv_dir: Path):
    """
    Returns: (passed, output, failures)
      passed=True  → all tests green
      passed=False → test failures (fixable)
      passed=None  → structural error exit>=2 (not fixable by patching source)
    """
    tests_dir = project_dir / "tests"
    if not tests_dir.exists():
        return True, "No tests/ directory — skipped", []

    pytest_bin = venv_dir / "bin" / "pytest"
    if not pytest_bin.exists():
        return False, "pytest binary missing — pip install likely failed", ["pip install failed: pytest not installed"]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_dir)
    env["HOME"] = "/tmp"

    r = subprocess.run(
        [str(pytest_bin), "tests/", "-v", "--tb=short", "--no-header"],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
    )
    output = r.stdout + r.stderr
    failures = [
        l for l in output.splitlines()
        if "FAILED" in l or ("ERROR" in l and "test_" in l)
    ]

    if r.returncode >= 3:
        # exit code ≥ 3 = pytest internal error (not fixable by source changes)
        return None, output, []
    if r.returncode == 2:
        # exit code 2 = collection errors (ImportError, SyntaxError) — LLM can fix these
        failures = [
            l for l in output.splitlines()
            if "ERROR" in l or "ImportError" in l or "SyntaxError" in l
        ]
    return r.returncode == 0, output, failures


# ── LLM integration ───────────────────────────────────────────────────────────

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


def call_llm_fix(
    test_output: str,
    source_files: dict,
    attempt: int,
    max_attempts: int,
    fix_model: str,
    code_fallback: str,
    litellm_url: str = None,
    litellm_key: str = None,
) -> str:
    """Call LiteLLM to fix failing tests; falls back to code_fallback on HTTP 429."""
    url = litellm_url or LITELLM_URL
    key = litellm_key or LITELLM_KEY

    file_blocks = "\n\n".join(
        f"===FILE: {path}===\n{content}\n===END FILE==="
        for path, content in source_files.items()
    )
    user_msg = (
        f"FAILING TEST OUTPUT (attempt {attempt} of {max_attempts}):\n"
        f"{test_output}\n\n"
        f"CURRENT PROJECT FILES:\n{file_blocks}"
    )
    payload = {
        "model": fix_model,
        "max_tokens": 8192,
        "timeout": 120,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a debugging assistant. Python tests are failing. "
                    "Fix the source files so all tests pass.\n\n"
                    "Output ONLY files that need changes using EXACTLY this format:\n"
                    "===FILE: path/to/file.py===\n[complete corrected content]\n===END FILE===\n\n"
                    "RULES:\n"
                    "- Include ONLY changed files — omit files that need no changes\n"
                    "- Output COMPLETE file contents — never diffs, never truncated\n"
                    "- Fix the root cause shown in the traceback\n"
                    "- If a missing package causes an ImportError, also fix requirements.txt"
                ),
            },
            {"role": "user", "content": user_msg},
        ],
    }

    def _call(model):
        p = dict(payload, model=model)
        return http.post(
            f"{url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=p,
            timeout=300,
        )

    resp = _call(fix_model)
    if resp.status_code == 429:
        print(f"  [{fix_model}] rate-limited, falling back to {code_fallback}", flush=True)
        resp = _call(code_fallback)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_and_apply_fixes(project_dir: Path, llm_response: str) -> list:
    """Parse ===FILE:=== blocks and write changed files."""
    pattern = re.compile(r"===FILE:\s*(.+?)===\n([\s\S]*?)(?====FILE:|===END FILE===|$)")
    applied = []
    for m in pattern.finditer(llm_response):
        rel_path = m.group(1).strip().lstrip("/")
        content = m.group(2)
        content = re.sub(r"^```[^\n]*\n", "", content)
        content = re.sub(r"\n```\s*$", "", content)
        if not content.strip():
            continue
        abs_path = project_dir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content)
        applied.append(rel_path)
    return applied


# ── post-process ──────────────────────────────────────────────────────────────

_STDLIB = {
    "unittest", "os", "sys", "json", "re", "math", "io", "abc", "ast", "builtins",
    "collections", "datetime", "functools", "hashlib", "http", "itertools",
    "logging", "pathlib", "random", "shutil", "socket", "sqlite3", "string",
    "subprocess", "tempfile", "threading", "time", "traceback", "typing",
    "urllib", "uuid", "warnings", "csv", "copy", "enum", "dataclasses",
    "contextlib", "base64", "struct", "queue", "signal", "argparse", "configparser",
    "pickle", "pprint", "textwrap", "glob", "fnmatch", "heapq", "bisect",
    "statistics", "decimal", "fractions", "operator", "inspect", "types",
    "gc", "platform", "locale", "codecs", "html", "xml", "email", "zipfile",
    "tarfile", "gzip", "bz2", "lzma", "zlib", "atexit", "dis", "tokenize",
    "calendar", "array", "mmap", "ctypes", "select", "selectors", "asyncio",
    "concurrent", "multiprocessing",
}


def run_postprocess(project_dir: Path) -> list:
    """
    Clean up a generated project's requirements.txt and test setup.

    Actions:
    - Remove stdlib package names from requirements.txt
    - Loosen exact version pins (pkg==X.Y.Z → pkg>=X.Y) to prevent pip
      failures when LLMs hallucinate non-existent patch versions
    - Pin flask>=3.0 if flask has no version constraint (werkzeug 3.x compat)
    - Create tests/conftest.py if missing and tests import from parent package
    """
    fixes = []

    req_file = project_dir / "requirements.txt"
    if req_file.exists():
        lines = req_file.read_text().split("\n")
        filtered = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                filtered.append(line)
                continue
            pkg = re.split(r"[=><!\[;~]", stripped)[0].strip().lower().replace("-", "_")
            if pkg in _STDLIB:
                fixes.append(f'requirements.txt: removed stdlib "{stripped}"')
            else:
                filtered.append(line)
        if len(filtered) != len(lines):
            req_file.write_text("\n".join(filtered))

    # Loosen exact-pinned versions (pkg==X.Y.Z → pkg>=X.Y) so pip can resolve
    # non-existent LLM-hallucinated patch versions (e.g. fastapi==2.0.6).
    if req_file.exists():
        lines = req_file.read_text().split("\n")
        new_lines = []
        changed = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            m = re.match(r"^([A-Za-z0-9_\-\.]+)==(\d+\.\d+)(\.\d+.*)?$", stripped)
            if m:
                pkg_name, major_minor = m.group(1), m.group(2)
                new_lines.append(f"{pkg_name}>={major_minor}")
                fixes.append(f"requirements.txt: loosened {stripped} → {pkg_name}>={major_minor}")
                changed = True
            else:
                new_lines.append(line)
        if changed:
            req_file.write_text("\n".join(new_lines))

    if req_file.exists():
        content = req_file.read_text()
        lines = content.split("\n")
        new_lines = []
        changed = False
        for line in lines:
            stripped = line.strip()
            pkg = (
                re.split(r"[=><!\[;~]", stripped)[0].strip().lower().replace("-", "_")
                if stripped else ""
            )
            if pkg == "flask" and not re.search(r"flask[>=!~]", stripped, re.I):
                new_lines.append("flask>=3.0")
                fixes.append("requirements.txt: pinned flask>=3.0 (werkzeug 3.x compat)")
                changed = True
            else:
                new_lines.append(line)
        if changed:
            req_file.write_text("\n".join(new_lines))

    tests_dir = project_dir / "tests"
    conftest = tests_dir / "conftest.py"
    if tests_dir.exists() and not conftest.exists():
        needs = any(
            re.search(
                r"^from (?!tests\.|\.)\w+ import|^import (?!tests\.)\w+", f.read_text(), re.M
            )
            for f in tests_dir.glob("*.py")
        )
        if needs:
            conftest.write_text(
                "import sys, os\n"
                "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
            )
            fixes.append("tests/conftest.py: created")

    return fixes
