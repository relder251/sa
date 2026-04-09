#!/usr/bin/env python3
"""
Agentic SDLC — Full Pipeline Server (Phases 1-10)

POST /run-opportunity  → full pipeline: plan → generate → validate → extract → test/fix → quality → docs → approval → git → deploy → monitor
POST /run              → Phase 3 test & fix loop only
GET  /health           → {"status": "ok"}
POST /approvals/{run_id}/signal → unblocks phase 10 approval wait

Runs on port 5002.
"""
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

import requests as http
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Phase module imports ──────────────────────────────────────────────────────
# Phases live in /data/phases/ in Docker, or relative to repo root otherwise
_PHASES_SEARCH = [
    "/data",
    str(Path(__file__).parent.parent),  # repo root when running locally
]
for _p in _PHASES_SEARCH:
    if Path(_p).exists() and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from phases.phase5_quality_gate import run_phase5
    from phases.phase6_documentation import run_phase6
    from phases.phase7_git_push import run_phase7
    from phases.phase8_deployment import run_phase8
    from phases.phase9_monitoring import run_phase9
    from phases.phase10_approval_gate import run_phase10

    _PHASES_AVAILABLE = True
except ImportError as _e:
    print(f"WARNING: Could not import phase modules: {_e}", flush=True)
    _PHASES_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────────
LITELLM_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-sa-prod-ce5d031e2a50ffa45d3a200c037971f81853e27ed19b894bc3630625cba0b71a")
MAX_ATTEMPTS = int(os.environ.get("PHASE3_MAX_ATTEMPTS", "3"))
WEBUI_BASE_URL = os.environ.get("WEBUI_BASE_URL", "http://localhost:3000")

PLAN_MODEL = "hybrid/chat"
CODE_MODEL = "free/code"
CODE_FALLBACK = "cloud/code"
FIX_MODEL = "free/code"

# ── Approval events registry ──────────────────────────────────────────────────
# Maps run_id → asyncio.Event; set by POST /approvals/{run_id}/signal
approval_events: dict[str, asyncio.Event] = {}

app = FastAPI(title="Agentic SDLC Pipeline Server", version="1.0.0")


# ── Request/response models ───────────────────────────────────────────────────

class RunOpportunityRequest(BaseModel):
    name: str = "unnamed"
    prompt: str = ""
    project_base: str = ""
    project_dir: str = ""
    plan_path: str = ""
    code_output_path: str = ""
    report_path: str = ""


class RunRequest(BaseModel):
    project_dir: str = "/data/output/project"
    report_path: str = "/data/output/phase3_report.md"


class ApprovalSignalRequest(BaseModel):
    status: str = "approved"   # "approved" or "rejected"
    approved_by: str = ""
    comment: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def update_state(project_base: Path, updates: dict) -> None:
    """Read run_state.json, merge updates, write atomically."""
    state_path = project_base / "run_state.json"
    try:
        if state_path.exists():
            state = json.loads(state_path.read_text())
        else:
            state = {}
        _deep_merge(state, updates)
        tmp_path = state_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(state, indent=2))
        tmp_path.replace(state_path)
    except Exception as e:
        print(f"[state] Could not update run_state.json: {e}", flush=True)


def _deep_merge(base: dict, updates: dict) -> None:
    """Recursively merge updates into base in-place."""
    for k, v in updates.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _phase_state(status: str, started_at: str = None, completed_at: str = None, result: str = None) -> dict:
    return {
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "result": result,
    }


def _init_run_state(
    run_id: str,
    name: str,
    prompt: str,
    project_base: Path,
    log_path: str,
) -> dict:
    """Create the initial run_state.json."""
    now = _now_iso()
    state = {
        "run_id": run_id,
        "name": name,
        "prompt": prompt,
        "status": "running",
        "created_at": now,
        "started_at": now,
        "completed_at": None,
        "phases": {
            str(i): _phase_state("pending") for i in range(1, 11)
        },
        "approval": {
            "status": "not_required",
            "requested_at": None,
            "approved_by": None,
            "approved_at": None,
            "comment": None,
        },
        "log_path": log_path,
        "project_base": str(project_base),
    }
    state_path = project_base / "run_state.json"
    state_path.write_text(json.dumps(state, indent=2))
    return state


def L(msg: str, project_base: Path = None, phase: int = None) -> None:
    """Log a message: print to stdout AND append to pipeline.log if project_base given."""
    timestamp = _now_iso()
    phase_tag = f"PHASE:{phase}" if phase is not None else "PIPELINE"
    formatted = f"[{timestamp}] [{phase_tag}] {msg}"
    print(formatted, flush=True)
    if project_base:
        try:
            log_path = Path(project_base) / "pipeline.log"
            with open(log_path, "a") as f:
                f.write(formatted + "\n")
        except Exception:
            pass


def _make_log_fn(project_base: Path):
    """Return a phase-aware log function bound to project_base."""
    def log_fn(msg: str, phase: int = None):
        L(msg, project_base=project_base, phase=phase)
    return log_fn


# ── venv management ───────────────────────────────────────────────────────────

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


def _pip(venv_dir: Path, args: list):
    pip = venv_dir / "bin" / "pip"
    env = os.environ.copy()
    env["HOME"] = "/tmp"
    r = subprocess.run([str(pip)] + args, capture_output=True, text=True, env=env)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


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
        return None, output, []
    if r.returncode == 2:
        failures = [
            l for l in output.splitlines()
            if "ERROR" in l or "ImportError" in l or "SyntaxError" in l
        ]
    return r.returncode == 0, output, failures


# ── LLM integration ───────────────────────────────────────────────────────────

def read_source_files(project_dir: Path) -> dict:
    """Collect all non-venv, non-cache project files."""
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


def call_llm_fix(test_output: str, source_files: dict, attempt: int) -> str:
    file_blocks = "\n\n".join(
        f"===FILE: {path}===\n{content}\n===END FILE==="
        for path, content in source_files.items()
    )
    user_msg = (
        f"FAILING TEST OUTPUT (attempt {attempt} of {MAX_ATTEMPTS}):\n"
        f"{test_output}\n\n"
        f"CURRENT PROJECT FILES:\n{file_blocks}"
    )
    payload = {
        "model": FIX_MODEL,
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
            f"{LITELLM_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {LITELLM_KEY}",
                "Content-Type": "application/json",
            },
            json=p,
            timeout=300,
        )

    resp = _call(FIX_MODEL)
    if resp.status_code == 429:
        print(f"  [{FIX_MODEL}] rate-limited, falling back to {CODE_FALLBACK}", flush=True)
        resp = _call(CODE_FALLBACK)
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


# ── LLM helper ────────────────────────────────────────────────────────────────

def _llm(model, system, user, max_tokens=8192, timeout=300, fallback=None) -> str:
    """Call LiteLLM; on 429 retry with `fallback` model if provided."""
    def _call(m):
        resp = http.post(
            f"{LITELLM_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {LITELLM_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": m,
                "max_tokens": max_tokens,
                "timeout": max(timeout - 30, 60),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=timeout,
        )
        return resp

    resp = _call(model)
    if resp.status_code == 429 and fallback:
        print(f"  [{model}] rate-limited, falling back to {fallback}", flush=True)
        resp = _call(fallback)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def validate_format_inline(content: str) -> dict:
    """Returns {valid, issues, file_count}."""
    issues = []
    blocks = re.findall(r"===FILE:\s*.+?===", content)
    if not blocks:
        issues.append("No ===FILE:=== blocks found")
    diff_markers = ["<<<<<<< SEARCH", "<<<<<<< HEAD", ">>>>>>> REPLACE"]
    if any(m in content for m in diff_markers):
        issues.append("Diff/merge conflict markers detected")
    trunc = [
        r"# rest of (the )?(implementation|code|file)",
        r"# TODO: implement",
        r"\.\.\. (existing|rest of) (code|implementation)",
    ]
    if any(re.search(p, content, re.I) for p in trunc):
        issues.append("Truncated content detected")
    return {"valid": len(issues) == 0, "issues": issues, "file_count": len(blocks)}


def extract_files(content: str, project_dir: Path) -> list:
    """Parse ===FILE:=== blocks and write to project_dir."""
    pattern = re.compile(r"===FILE:\s*(.+?)===\n([\s\S]*?)(?====FILE:|===END FILE===|$)")
    written = []
    for m in pattern.finditer(content):
        rel = m.group(1).strip().lstrip("/")
        body = re.sub(r"^```[^\n]*\n", "", m.group(2))
        body = re.sub(r"\n```\s*$", "", body)
        if not body.strip():
            continue
        dest = project_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body)
        written.append(rel)
    return written


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return JSONResponse({
        "service": "Agentic SDLC — Pipeline Server",
        "version": "1.0.0",
        "status": "ok",
        "ui": "http://localhost:3000",
        "endpoints": {
            "GET  /health":                   "Health check",
            "POST /run-opportunity":          "Full 10-phase pipeline (plan → code → test → quality → docs → git → deploy → monitor)",
            "POST /run":                      "Phase 3 test & fix loop only",
            "POST /approvals/{run_id}/signal":"Unblock Phase 10 approval gate",
        },
    })

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.post("/approvals/{run_id}/signal")
async def signal_approval(run_id: str, body: ApprovalSignalRequest):
    """
    Unblock a phase 10 approval wait.
    Body: { status: "approved"|"rejected", approved_by: str, comment: str }
    """
    # Update run_state.json
    # Find project_base by looking for run_state files — we scan the projects output dir
    project_base = None
    projects_root = Path("/data/output/projects")
    if projects_root.exists():
        for candidate in projects_root.iterdir():
            state_file = candidate / "run_state.json"
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text())
                    if state.get("run_id") == run_id:
                        project_base = candidate
                        break
                except Exception:
                    pass

    approval_update = {
        "status": body.status,
        "approved_by": body.approved_by or "api",
        "comment": body.comment,
    }
    if body.status == "approved":
        approval_update["approved_at"] = _now_iso()

    if project_base:
        update_state(project_base, {"approval": approval_update})

    # Set event to unblock waiting coroutine
    if run_id in approval_events:
        approval_events[run_id].set()
        return JSONResponse({"ok": True, "run_id": run_id, "status": body.status})
    else:
        return JSONResponse(
            {"ok": False, "run_id": run_id, "error": "No active wait for this run_id"},
            status_code=404,
        )


@app.post("/run-opportunity")
async def run_opportunity(body: RunOpportunityRequest):
    """Full pipeline: plan → generate → validate → extract → test/fix → quality → docs → approval → git → deploy → monitor."""
    name = body.name
    prompt = body.prompt
    project_base = Path(body.project_base or f"/data/output/projects/{name}")
    project_dir = Path(body.project_dir or str(project_base / "project"))
    plan_path = Path(body.plan_path or str(project_base / "project_plan.md"))
    code_path = Path(body.code_output_path or str(project_base / "execution_output.md"))
    report_path = Path(body.report_path or str(project_base / "phase3_report.md"))

    project_base.mkdir(parents=True, exist_ok=True)
    project_dir.mkdir(parents=True, exist_ok=True)

    log_path = str(project_base / "pipeline.log")

    # Generate run_id
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"{name}-{ts}"

    # Create approval event for this run
    approval_events[run_id] = asyncio.Event()

    # Initialize run_state.json
    _init_run_state(run_id, name, prompt, project_base, log_path)

    log = []

    def Lx(msg, phase=None):
        """Log to both in-memory list and pipeline.log."""
        log.append(msg)
        L(msg, project_base=project_base, phase=phase)

    Lx(f"=== Pipeline — {name} (run_id={run_id}) ===")

    # ── Phase 1: Plan ─────────────────────────────────────────────────────────
    update_state(project_base, {
        "phases": {"1": _phase_state("running", started_at=_now_iso())}
    })
    Lx("Step 1: Generating project plan ...", phase=1)
    try:
        plan = _llm(
            PLAN_MODEL,
            (
                "You are an expert software architect. Create a detailed project plan.\n\n"
                "Include: 1) Project overview, 2) Technical stack, 3) File structure, "
                "4) Core components, 5) Implementation steps, 6) Testing approach.\n"
                "Be specific and actionable."
            ),
            prompt,
            max_tokens=4096,
        )
        plan_path.write_text(plan)
        Lx(f"  Plan saved ({len(plan)} chars)", phase=1)
        update_state(project_base, {
            "phases": {"1": _phase_state("done", completed_at=_now_iso(), result="ok")}
        })
    except Exception as e:
        Lx(f"  Phase 1 failed: {e}", phase=1)
        update_state(project_base, {
            "phases": {"1": _phase_state("failed", completed_at=_now_iso(), result=str(e))},
            "status": "failed",
            "completed_at": _now_iso(),
        })
        _cleanup_approval_event(run_id)
        return JSONResponse({"passed": False, "error": f"Phase 1 failed: {e}", "log": log, "run_id": run_id})

    # ── Phase 2: Generate code ────────────────────────────────────────────────
    update_state(project_base, {
        "phases": {"2": _phase_state("running", started_at=_now_iso())}
    })
    Lx("Step 2: Generating code ...", phase=2)
    try:
        code_system = (
            "You are the Lead Executor of an automated solution builder. "
            "Take the provided project plan and generate a complete, production-ready codebase.\n\n"
            "Output every file using EXACTLY this format:\n"
            "===FILE: path/to/filename.ext===\n[full file content]\n===END FILE===\n\n"
            "REQUIRED: main app files, tests/ directory, requirements.txt, Dockerfile, .gitignore, README.md\n\n"
            "CRITICAL PROHIBITIONS:\n"
            "- NEVER use diff/patch/merge-conflict markers\n"
            "- NEVER truncate or use placeholder comments\n"
            "- Output COMPLETE files only, every line top to bottom"
        )
        code_output = _llm(CODE_MODEL, code_system, plan, fallback=CODE_FALLBACK, timeout=600)
        code_path.write_text(code_output)
        Lx(f"  Code output saved ({len(code_output)} chars)", phase=2)
        update_state(project_base, {
            "phases": {"2": _phase_state("done", completed_at=_now_iso(), result="ok")}
        })
    except Exception as e:
        Lx(f"  Phase 2 failed: {e}", phase=2)
        update_state(project_base, {
            "phases": {"2": _phase_state("failed", completed_at=_now_iso(), result=str(e))},
            "status": "failed",
            "completed_at": _now_iso(),
        })
        _cleanup_approval_event(run_id)
        return JSONResponse({"passed": False, "error": f"Phase 2 failed: {e}", "log": log, "run_id": run_id})

    # ── Phase 3: Validate & fix format ───────────────────────────────────────
    update_state(project_base, {
        "phases": {"3": _phase_state("running", started_at=_now_iso())}
    })
    Lx("Step 3: Validating format ...", phase=3)
    try:
        val = validate_format_inline(code_output)
        Lx(f"  valid={val['valid']} files={val['file_count']} issues={val['issues']}", phase=3)

        if not val["valid"]:
            Lx("  Format invalid — requesting LLM fix ...", phase=3)
            fix_prompt = f"VALIDATION ISSUES:\n{val['issues']}\n\nORIGINAL OUTPUT:\n{code_output}"
            code_output = _llm(
                FIX_MODEL,
                (
                    "Fix the output so every file uses ===FILE: path===\\n[content]\\n===END FILE=== format. "
                    "Output COMPLETE corrected files only."
                ),
                fix_prompt,
                fallback=CODE_FALLBACK,
            )
            code_path.write_text(code_output)
            val2 = validate_format_inline(code_output)
            Lx(f"  After fix: valid={val2['valid']} files={val2['file_count']}", phase=3)

        update_state(project_base, {
            "phases": {"3": _phase_state("done", completed_at=_now_iso(), result="ok")}
        })
    except Exception as e:
        Lx(f"  Phase 3 failed: {e}", phase=3)
        update_state(project_base, {
            "phases": {"3": _phase_state("failed", completed_at=_now_iso(), result=str(e))},
            "status": "failed",
            "completed_at": _now_iso(),
        })
        _cleanup_approval_event(run_id)
        return JSONResponse({"passed": False, "error": f"Phase 3 failed: {e}", "log": log, "run_id": run_id})

    # ── Phase 4: Extract + test/fix loop ─────────────────────────────────────
    update_state(project_base, {
        "phases": {"4": _phase_state("running", started_at=_now_iso())}
    })
    Lx("Step 4: Extracting files ...", phase=4)
    written = extract_files(code_output, project_dir)
    Lx(f"  Wrote {len(written)} files: {written[:6]}", phase=4)

    Lx("Step 5: Running postprocess ...", phase=4)
    for fix in run_postprocess(project_dir):
        Lx(f"  {fix}", phase=4)

    Lx("Step 6: Running test & fix loop ...", phase=4)
    ok, venv_dir, pip_msg = ensure_venv(project_dir)
    Lx(f"  venv: {pip_msg or 'ready'}", phase=4)

    iterations = []
    for attempt in range(1, MAX_ATTEMPTS + 2):
        passed, test_output, failures = run_pytest(project_dir, venv_dir)
        iter_entry = {"attempt": attempt, "passed": passed, "output": test_output}
        iterations.append(iter_entry)
        if passed is None:
            Lx(f"  Structural pytest error — stopping", phase=4)
            break
        if passed:
            Lx(f"  All tests passed on attempt {attempt}!", phase=4)
            break
        Lx(f"  {len(failures)} failure(s) on attempt {attempt}", phase=4)
        if attempt > MAX_ATTEMPTS:
            Lx("  Max attempts reached", phase=4)
            break
        try:
            src = read_source_files(project_dir)
            fixed = call_llm_fix(test_output, src, attempt)
            applied = parse_and_apply_fixes(project_dir, fixed)
            Lx(f"  Applied fixes: {applied}", phase=4)
            if not applied:
                break
            for fix in run_postprocess(project_dir):
                Lx(f"  postprocess: {fix}", phase=4)
            if "requirements.txt" in applied:
                h = venv_dir / ".req_hash"
                if h.exists():
                    h.unlink()
                ok, venv_dir, pip_msg = ensure_venv(project_dir)
        except Exception as e:
            Lx(f"  LLM fix failed: {e}", phase=4)
            break

    final_passed = bool(iterations and iterations[-1].get("passed"))
    icon = "PASSED" if final_passed else "FAILED"
    lines = [
        f"# Phase 4 Report — {icon}\n",
        f"**Project**: `{name}`  \n**Result**: {icon}  \n**Iterations**: {len(iterations)}\n",
    ]
    for it in iterations:
        ic = "PASS" if it.get("passed") else "FAIL"
        lines += [f"## Attempt {it['attempt']} — {ic}", f"```\n{it['output'][:2000]}\n```\n"]
    report_path.write_text("\n".join(lines))
    Lx(f"  Report → {report_path}", phase=4)

    phase4_result = "ok" if final_passed else "tests_failed"
    update_state(project_base, {
        "phases": {"4": _phase_state("done", completed_at=_now_iso(), result=phase4_result)}
    })

    # ── Phases 5-10 ───────────────────────────────────────────────────────────
    if not _PHASES_AVAILABLE:
        Lx("WARNING: Phase modules not available — skipping phases 5-10", phase=5)
        update_state(project_base, {"status": "done", "completed_at": _now_iso()})
        _cleanup_approval_event(run_id)
        return JSONResponse({
            "passed": final_passed,
            "iterations": len(iterations),
            "project_dir": str(project_dir),
            "run_id": run_id,
            "log": log,
        })

    log_fn = _make_log_fn(project_base)

    # ── Phase 5: Quality Gate ─────────────────────────────────────────────────
    update_state(project_base, {
        "phases": {"5": _phase_state("running", started_at=_now_iso())}
    })
    try:
        p5 = run_phase5(name, project_dir, project_base, log_fn=log_fn)
        p5_result = "ok" if p5.get("passed") else ("blocked" if p5.get("blocked") else "warned")
        update_state(project_base, {
            "phases": {"5": _phase_state("done", completed_at=_now_iso(), result=p5_result)}
        })
        if p5.get("blocked"):
            reason = p5.get("block_reason", "Quality gate blocked")
            Lx(f"  Phase 5 BLOCKED: {reason}", phase=5)
            update_state(project_base, {
                "status": "blocked",
                "completed_at": _now_iso(),
            })
            # Mark remaining phases as skipped
            for ph in range(6, 11):
                update_state(project_base, {
                    "phases": {str(ph): _phase_state("skipped", completed_at=_now_iso(), result="blocked_by_phase5")}
                })
            _cleanup_approval_event(run_id)
            return JSONResponse({
                "passed": final_passed,
                "iterations": len(iterations),
                "project_dir": str(project_dir),
                "run_id": run_id,
                "status": "blocked",
                "block_reason": reason,
                "log": log,
            })
    except Exception as e:
        Lx(f"  Phase 5 exception: {e}", phase=5)
        update_state(project_base, {
            "phases": {"5": _phase_state("failed", completed_at=_now_iso(), result=str(e))}
        })
        # Phase 5 failure is non-blocking — continue

    # ── Phase 6: Documentation ────────────────────────────────────────────────
    update_state(project_base, {
        "phases": {"6": _phase_state("running", started_at=_now_iso())}
    })
    try:
        p6 = run_phase6(name, project_dir, project_base, log_fn=log_fn)
        p6_result = "ok" if p6.get("success") else "failed"
        update_state(project_base, {
            "phases": {"6": _phase_state("done", completed_at=_now_iso(), result=p6_result)}
        })
    except Exception as e:
        Lx(f"  Phase 6 exception: {e}", phase=6)
        update_state(project_base, {
            "phases": {"6": _phase_state("failed", completed_at=_now_iso(), result=str(e))}
        })
        # Non-blocking — continue

    # ── Phase 10: Approval Gate (between 6 and 7) ─────────────────────────────
    enable_approval = os.environ.get("ENABLE_APPROVAL_GATE", "false").lower() == "true"
    if enable_approval:
        update_state(project_base, {
            "phases": {"10": _phase_state("running", started_at=_now_iso())},
            "approval": {"status": "pending_approval"},
        })
        try:
            p10 = await run_phase10(
                name=name,
                run_id=run_id,
                project_base=project_base,
                approval_event=approval_events[run_id],
                litellm_url=LITELLM_URL,
                litellm_key=LITELLM_KEY,
                webui_base_url=WEBUI_BASE_URL,
                log_fn=log_fn,
            )
            p10_result = "approved" if p10.get("approved") else "rejected"
            update_state(project_base, {
                "phases": {"10": _phase_state("done", completed_at=_now_iso(), result=p10_result)},
                "approval": {
                    "status": p10_result,
                    "approved_by": p10.get("approved_by"),
                    "approved_at": _now_iso() if p10.get("approved") else None,
                    "comment": p10.get("comment"),
                },
            })
            if not p10.get("approved"):
                reason = p10.get("reason", "rejected")
                Lx(f"  Phase 10 approval not granted: {reason}", phase=10)
                update_state(project_base, {
                    "status": "rejected",
                    "completed_at": _now_iso(),
                })
                for ph in [7, 8, 9]:
                    update_state(project_base, {
                        "phases": {str(ph): _phase_state("skipped", completed_at=_now_iso(), result="not_approved")}
                    })
                _cleanup_approval_event(run_id)
                return JSONResponse({
                    "passed": final_passed,
                    "iterations": len(iterations),
                    "project_dir": str(project_dir),
                    "run_id": run_id,
                    "status": "rejected",
                    "rejection_reason": reason,
                    "log": log,
                })
        except Exception as e:
            Lx(f"  Phase 10 exception: {e}", phase=10)
            update_state(project_base, {
                "phases": {"10": _phase_state("failed", completed_at=_now_iso(), result=str(e))}
            })
            # Non-blocking — continue to deployment
    else:
        update_state(project_base, {
            "phases": {"10": _phase_state("skipped", completed_at=_now_iso(), result="approval_gate_disabled")}
        })

    # ── Phase 7: Git Push ─────────────────────────────────────────────────────
    update_state(project_base, {
        "phases": {"7": _phase_state("running", started_at=_now_iso())}
    })
    try:
        p7 = run_phase7(name, project_dir, project_base, log_fn=log_fn)
        p7_result = "ok" if p7.get("success") else "failed"
        update_state(project_base, {
            "phases": {"7": _phase_state("done", completed_at=_now_iso(), result=p7_result)}
        })
    except Exception as e:
        Lx(f"  Phase 7 exception: {e}", phase=7)
        update_state(project_base, {
            "phases": {"7": _phase_state("failed", completed_at=_now_iso(), result=str(e))}
        })

    # ── Phase 8: Deployment ───────────────────────────────────────────────────
    update_state(project_base, {
        "phases": {"8": _phase_state("running", started_at=_now_iso())}
    })
    deploy_endpoint = None
    try:
        p8 = run_phase8(name, project_dir, project_base, log_fn=log_fn)
        p8_result = "ok" if p8.get("success") else "failed"
        deploy_endpoint = p8.get("endpoint")
        update_state(project_base, {
            "phases": {"8": _phase_state("done", completed_at=_now_iso(), result=p8_result)}
        })
    except Exception as e:
        Lx(f"  Phase 8 exception: {e}", phase=8)
        update_state(project_base, {
            "phases": {"8": _phase_state("failed", completed_at=_now_iso(), result=str(e))}
        })

    # ── Phase 9: Monitoring ───────────────────────────────────────────────────
    update_state(project_base, {
        "phases": {"9": _phase_state("running", started_at=_now_iso())}
    })
    try:
        p9 = run_phase9(
            name, project_dir, project_base, endpoint=deploy_endpoint, log_fn=log_fn
        )
        p9_result = "ok" if p9.get("success") else "failed"
        update_state(project_base, {
            "phases": {"9": _phase_state("done", completed_at=_now_iso(), result=p9_result)}
        })
    except Exception as e:
        Lx(f"  Phase 9 exception: {e}", phase=9)
        update_state(project_base, {
            "phases": {"9": _phase_state("failed", completed_at=_now_iso(), result=str(e))}
        })

    # ── Finalize ──────────────────────────────────────────────────────────────
    update_state(project_base, {"status": "done", "completed_at": _now_iso()})
    Lx(f"=== Pipeline complete — {name} ===")

    _cleanup_approval_event(run_id)

    return JSONResponse({
        "passed": final_passed,
        "iterations": len(iterations),
        "project_dir": str(project_dir),
        "run_id": run_id,
        "log": log,
    })


def _cleanup_approval_event(run_id: str) -> None:
    """Remove approval event from registry after run completes."""
    approval_events.pop(run_id, None)


@app.post("/run")
async def run(body: RunRequest):
    """Phase 3 test & fix loop only (backward compat with test_runner_server.py)."""
    project_dir = Path(body.project_dir)
    report_path = Path(body.report_path)

    log = []
    iterations = []

    def Lx(msg):
        log.append(msg)
        print(msg, flush=True)

    Lx(f"=== Phase 3 starting — {project_dir} ===")

    for fix in run_postprocess(project_dir):
        Lx(f"  postprocess: {fix}")

    ok, venv_dir, pip_msg = ensure_venv(project_dir)
    Lx(f"  venv: {pip_msg or 'ready'}")
    if not ok:
        report = f"# Phase 3 Report\n\n**FAILED** — pip install error:\n```\n{pip_msg}\n```\n"
        report_path.write_text(report)
        return JSONResponse({"passed": False, "error": "pip install failed", "log": log})

    for attempt in range(1, MAX_ATTEMPTS + 2):
        Lx(f"\n── Test run {attempt} {'(final check)' if attempt == MAX_ATTEMPTS + 1 else ''} ──")
        passed, test_output, failures = run_pytest(project_dir, venv_dir)

        iter_entry = {"attempt": attempt, "passed": passed, "output": test_output}
        iterations.append(iter_entry)

        if passed is None:
            Lx("  pytest structural error (exit >=2) — cannot fix by patching source")
            break
        if passed:
            Lx("  All tests passed!")
            break

        Lx(f"  {len(failures)} failure(s)")
        for f in failures[:5]:
            Lx(f"     {f}")

        if attempt > MAX_ATTEMPTS:
            Lx(f"  Max attempts ({MAX_ATTEMPTS}) reached")
            break

        Lx(f"  Requesting LLM fix ({FIX_MODEL}) ...")
        try:
            source_files = read_source_files(project_dir)
            llm_response = call_llm_fix(test_output, source_files, attempt)
            applied = parse_and_apply_fixes(project_dir, llm_response)
            Lx(f"  Fixed files: {applied or '(none)'}")

            if not applied:
                Lx("  LLM returned no file changes — stopping")
                break

            for fix in run_postprocess(project_dir):
                Lx(f"  postprocess: {fix}")

            if "requirements.txt" in applied:
                hash_file = venv_dir / ".req_hash"
                if hash_file.exists():
                    hash_file.unlink()
                ok, venv_dir, pip_msg = ensure_venv(project_dir)
                Lx(f"  deps refreshed: {pip_msg or 'ready'}")

        except Exception as e:
            Lx(f"  LLM call failed: {e}")
            break

    final_passed = bool(iterations and iterations[-1].get("passed"))
    status_icon = "PASSED" if final_passed else "FAILED"

    lines = [
        f"# Phase 3 Report — {status_icon}\n",
        f"**Project**: `{project_dir}`  ",
        f"**Iterations**: {len(iterations)}  ",
        f"**Result**: {status_icon}\n",
    ]
    for it in iterations:
        icon = (
            "PASS"
            if it.get("passed")
            else ("ERROR" if it.get("passed") is None else "FAIL")
        )
        lines.append(f"## Attempt {it['attempt']} — {icon}")
        lines.append(f"```\n{it['output'][:3000]}\n```\n")

    report_path.write_text("\n".join(lines))
    Lx(f"\nReport written → {report_path}")

    return JSONResponse({
        "passed": final_passed,
        "iterations": len(iterations),
        "log": log,
    })


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5002, log_level="info")
