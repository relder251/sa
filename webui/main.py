"""
Agentic SDLC Web UI — FastAPI + Jinja2 + HTMX
No JavaScript framework, no npm/node build step.
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

import aiofiles
import httpx
import mistune
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTPUT_BASE = Path(os.getenv("OUTPUT_BASE", "/data/output"))
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
N8N_BASE_URL = os.getenv("N8N_BASE_URL", "http://n8n:5678")
PIPELINE_SERVER_URL = os.getenv("PIPELINE_SERVER_URL", "http://pipeline-server:5002")
TEST_RUNNER_URL = os.getenv("TEST_RUNNER_URL", "http://test-runner:5001")
WEBUI_BASE_URL = os.getenv("WEBUI_BASE_URL", "http://localhost:3000")

OPPORTUNITIES_DIR = OUTPUT_BASE / "opportunities"
PROJECTS_DIR = OUTPUT_BASE / "projects"


def _project_name(name: str) -> str:
    """Normalise a run name to the filesystem directory name used by opportunity_intake.js.
    Must match: name.toLowerCase().replace(/[^a-z0-9-_]/g,'-').replace(/-+/g,'-').replace(/^-|-$/g,'')
    """
    s = name.lower()
    s = re.sub(r"[^a-z0-9\-_]", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


# Order matters for deduplication: done/failed take priority over running/pending
# so stale running/ entries are superseded by completed done/ entries
STATUS_DIRS = ["done", "failed", "running", "pending"]

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Agentic SDLC Web UI")

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

_md = mistune.create_markdown(
    plugins=["strikethrough", "table", "task_lists"],
)


def render_markdown(text: str) -> str:
    """Render markdown to HTML using mistune."""
    return _md(text)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

PHASE_NAMES = {
    "1": "Plan",
    "2": "Code Gen",
    "3": "Test & Fix",
    "4": "Full Pipeline",
    "5": "Quality Gate",
    "6": "Docs",
    "7": "Git Push",
    "8": "Deploy",
    "9": "Monitor",
    "10": "Approval Gate",
}


def _default_phases(status: str = "pending") -> dict:
    """Return a default phases dict where all phases share the given status."""
    phases = {}
    for i in range(1, 11):
        phases[str(i)] = {
            "name": PHASE_NAMES[str(i)],
            "status": status,
            "started_at": None,
            "completed_at": None,
        }
    return phases


def load_run(name: str) -> dict:
    """Load run info from opportunity files + run_state.json.

    Checks all 4 status dirs for the opportunity file, loads run_state.json
    when it exists, and returns a merged dict.
    """
    opp_data: Optional[dict] = None
    found_status: Optional[str] = None

    for status in STATUS_DIRS:
        opp_file = OPPORTUNITIES_DIR / status / f"{name}.json"
        if opp_file.exists():
            try:
                with open(opp_file, "r", encoding="utf-8") as fh:
                    opp_data = json.load(fh)
                found_status = status
            except (json.JSONDecodeError, OSError):
                opp_data = {"name": name}
                found_status = status
            break

    if opp_data is None:
        return {}

    # Status aliases — normalize pipeline_server values to board buckets
    _STATUS_ALIASES = {"completed": "done", "blocked": "failed", "cancelled": "failed", "rejected": "failed"}

    # Directory location is the authoritative status source — the file content
    # may contain a stale intermediate value (e.g. "running" stamped by intake.js)
    authoritative_status = _STATUS_ALIASES.get(found_status, found_status)

    # Defaults
    run: dict = {
        "name": opp_data.get("name", name),
        "prompt": opp_data.get("prompt", ""),
        "status": authoritative_status,
        "created_at": opp_data.get("created_at", None),
        "completed_at": None,
        "phases": _default_phases("done" if authoritative_status in ("done", "failed") else "pending"),
        "approval": None,
        "log_path": None,
        "project_base": None,
    }

    # Try run_state.json
    run_state_path = PROJECTS_DIR / _project_name(name) / "run_state.json"
    if run_state_path.exists():
        try:
            with open(run_state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            # Merge fields from state, normalizing aliases
            raw_status = state.get("status", run["status"])
            run["status"] = _STATUS_ALIASES.get(raw_status, raw_status)
            run["completed_at"] = state.get("completed_at", None)
            run["approval"] = state.get("approval", None)

            # Build phases dict from state
            raw_phases = state.get("phases", {})
            if raw_phases:
                merged_phases: dict = {}
                for i in range(1, 11):
                    key = str(i)
                    phase_info = raw_phases.get(key, raw_phases.get(i, {}))
                    merged_phases[key] = {
                        "name": phase_info.get("name", PHASE_NAMES.get(key, f"Phase {i}")),
                        "status": phase_info.get("status", "pending"),
                        "started_at": phase_info.get("started_at", None),
                        "completed_at": phase_info.get("completed_at", None),
                    }
                run["phases"] = merged_phases
        except (json.JSONDecodeError, OSError):
            pass

    log_path = PROJECTS_DIR / _project_name(name) / "pipeline.log"
    run["log_path"] = str(log_path) if log_path.exists() else None
    project_base = PROJECTS_DIR / _project_name(name)
    run["project_base"] = str(project_base) if project_base.exists() else None

    return run


def list_all_runs() -> list:
    """Return all runs from all 4 status dirs, sorted by created_at desc."""
    runs = []
    seen: set = set()

    for status in STATUS_DIRS:
        status_dir = OPPORTUNITIES_DIR / status
        if not status_dir.exists():
            continue
        for opp_file in status_dir.glob("*.json"):
            name = opp_file.stem
            if name in seen:
                continue
            seen.add(name)
            run = load_run(name)
            if run:
                runs.append(run)

    def sort_key(r: dict):
        created = r.get("created_at") or ""
        return created

    runs.sort(key=sort_key, reverse=True)
    return runs


def _elapsed_str(created_at: Optional[str]) -> str:
    """Return human-readable elapsed time since created_at ISO string."""
    if not created_at:
        return ""
    try:
        then = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        secs = int((now - then).total_seconds())
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        h = secs // 3600
        m = (secs % 3600) // 60
        return f"{h}h {m}m"
    except (ValueError, TypeError):
        return ""


def _board_context() -> dict:
    """Build context for the board partial."""
    all_runs = list_all_runs()
    grouped: dict = {s: [] for s in STATUS_DIRS}
    # Normalize status aliases from pipeline_server into board buckets
    STATUS_ALIASES = {"completed": "done", "blocked": "failed", "cancelled": "failed", "rejected": "failed"}
    for run in all_runs:
        bucket = run.get("status", "pending")
        bucket = STATUS_ALIASES.get(bucket, bucket)
        if bucket not in grouped:
            bucket = "pending"
        grouped[bucket].append(run)

    counts = {s: len(grouped[s]) for s in STATUS_DIRS}
    return {"grouped": grouped, "counts": counts}


def _sanitize_path(project_base: str, rel_path: str) -> Optional[Path]:
    """Sanitize and validate that a path stays within project_base."""
    base = Path(project_base).resolve()
    requested = (base / rel_path).resolve()
    if not str(requested).startswith(str(base)):
        return None
    if not requested.exists() or not requested.is_file():
        return None
    return requested


def _list_project_files(project_base: str) -> list:
    """List all files in project_base, returning relative path metadata."""
    base = Path(project_base)
    if not base.exists():
        return []

    files = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            try:
                size = p.stat().st_size
                rel = str(p.relative_to(base))
                files.append({"path": rel, "size": size, "name": p.name})
            except OSError:
                pass
    return files


def _group_files_by_dir(files: list) -> dict:
    """Group a list of file dicts by their directory."""
    grouped: dict = {}
    for f in files:
        parts = Path(f["path"]).parts
        if len(parts) == 1:
            dirname = "."
        else:
            dirname = str(Path(*parts[:-1]))
        grouped.setdefault(dirname, []).append(f)
    return grouped


async def _check_service(client: httpx.AsyncClient, name: str, url: str, path: str = "/health") -> dict:
    """Probe a service and return health dict."""
    start = time.monotonic()
    try:
        r = await client.get(f"{url}{path}", timeout=3.0)
        latency_ms = int((time.monotonic() - start) * 1000)
        status = "ok" if r.status_code < 500 else "error"
    except Exception:
        latency_ms = int((time.monotonic() - start) * 1000)
        status = "error"
    return {"name": name, "status": status, "latency_ms": latency_ms}


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    ctx = _board_context()
    ctx["request"] = request
    return templates.TemplateResponse("index.html", ctx)


@app.get("/runs/{name}", response_class=HTMLResponse)
async def run_detail(request: Request, name: str):
    run = load_run(name)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{name}' not found")
    return templates.TemplateResponse(
        "run_detail.html",
        {
            "request": request,
            "run": run,
            "phases": run.get("phases", _default_phases()),
            "elapsed": _elapsed_str(run.get("created_at")),
        },
    )


@app.get("/system", response_class=HTMLResponse)
async def system_health(request: Request):
    return templates.TemplateResponse("system_health.html", {"request": request})


# ---------------------------------------------------------------------------
# HTMX partial routes
# ---------------------------------------------------------------------------


@app.get("/partials/stats", response_class=HTMLResponse)
async def partial_stats(request: Request):
    ctx = _board_context()
    ctx["request"] = request
    return templates.TemplateResponse("partials/stats.html", ctx)


@app.get("/partials/board", response_class=HTMLResponse)
async def partial_board(request: Request):
    ctx = _board_context()
    ctx["request"] = request
    return templates.TemplateResponse("partials/board.html", ctx)


@app.get("/partials/run/{name}/phases", response_class=HTMLResponse)
async def partial_phases(request: Request, name: str):
    run = load_run(name)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{name}' not found")
    return templates.TemplateResponse(
        "partials/phase_badges.html",
        {"request": request, "run": run, "phases": run.get("phases", _default_phases())},
    )


@app.get("/partials/run/{name}/files", response_class=HTMLResponse)
async def partial_files(request: Request, name: str):
    run = load_run(name)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{name}' not found")
    project_base = run.get("project_base", "")
    files = _list_project_files(project_base) if project_base else []
    grouped = _group_files_by_dir(files)
    return templates.TemplateResponse(
        "partials/file_tree.html",
        {"request": request, "run": run, "files": files, "grouped": grouped},
    )


@app.get("/partials/run/{name}/file", response_class=HTMLResponse)
async def partial_file_content(request: Request, name: str, path: str = Query(...)):
    run = load_run(name)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{name}' not found")
    project_base = run.get("project_base", "")
    if not project_base:
        raise HTTPException(status_code=404, detail="Project directory not found")

    safe_path = _sanitize_path(project_base, path)
    if not safe_path:
        raise HTTPException(status_code=403, detail="Path not allowed or not found")

    try:
        async with aiofiles.open(safe_path, "r", encoding="utf-8", errors="replace") as fh:
            content = await fh.read()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    ext = safe_path.suffix.lower()
    is_markdown = ext in (".md", ".markdown")
    rendered_html = render_markdown(content) if is_markdown else None

    return templates.TemplateResponse(
        "partials/file_content.html",
        {
            "request": request,
            "filename": safe_path.name,
            "ext": ext,
            "content": content,
            "rendered_html": rendered_html,
            "is_markdown": is_markdown,
        },
    )


@app.get("/partials/system/health", response_class=HTMLResponse)
async def partial_system_health(request: Request):
    services_to_check = [
        ("LiteLLM", LITELLM_BASE_URL, "/health"),
        ("n8n", N8N_BASE_URL, "/healthz"),
        ("Pipeline Server", PIPELINE_SERVER_URL, "/health"),
        ("Test Runner", TEST_RUNNER_URL, "/health"),
    ]

    async with httpx.AsyncClient() as client:
        tasks = [_check_service(client, name, url, path) for name, url, path in services_to_check]
        services = await asyncio.gather(*tasks)

    # Fetch LiteLLM models
    models: list = []
    try:
        async with httpx.AsyncClient() as client:
            headers = {}
            if LITELLM_API_KEY:
                headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"
            r = await client.get(f"{LITELLM_BASE_URL}/models", headers=headers, timeout=5.0)
            if r.status_code == 200:
                data = r.json()
                models = data.get("data", [])
    except Exception:
        models = []

    return templates.TemplateResponse(
        "partials/system_health_panel.html",
        {"request": request, "services": services, "models": models},
    )


@app.get("/partials/approvals", response_class=HTMLResponse)
async def partial_approvals(request: Request):
    all_runs = list_all_runs()
    pending_approvals = []
    for run in all_runs:
        approval = run.get("approval")
        if approval and approval.get("status") == "pending":
            pending_approvals.append(run)
    return templates.TemplateResponse(
        "partials/approvals_list.html",
        {"request": request, "approvals": pending_approvals},
    )


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.post("/runs")
async def create_run(
    request: Request,
    name: str = Form(...),
    prompt: str = Form(...),
):
    """Write a new opportunity JSON to pending/ and redirect to detail page."""
    # Sanitize name
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_-")
    if not safe_name:
        return RedirectResponse("/", status_code=303)

    pending_dir = OPPORTUNITIES_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    opp = {
        "name": safe_name,
        "prompt": prompt,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    opp_file = pending_dir / f"{safe_name}.json"
    with open(opp_file, "w", encoding="utf-8") as fh:
        json.dump(opp, fh, indent=2)

    return RedirectResponse(f"/runs/{safe_name}", status_code=303)


@app.post("/runs/{name}/cancel")
async def cancel_run(name: str):
    """Move run from running/ to failed/ with status=cancelled."""
    running_file = OPPORTUNITIES_DIR / "running" / f"{name}.json"
    if running_file.exists():
        failed_dir = OPPORTUNITIES_DIR / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(running_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            data = {"name": name}
        data["status"] = "cancelled"
        data["completed_at"] = datetime.now(timezone.utc).isoformat()
        failed_file = failed_dir / f"{name}.json"
        with open(failed_file, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        running_file.unlink(missing_ok=True)

    # Also update run_state.json if it exists
    run_state_path = PROJECTS_DIR / _project_name(name) / "run_state.json"
    if run_state_path.exists():
        try:
            with open(run_state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            state["status"] = "cancelled"
            state["completed_at"] = datetime.now(timezone.utc).isoformat()
            with open(run_state_path, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
        except (json.JSONDecodeError, OSError):
            pass

    from fastapi.responses import Response
    response = Response(status_code=200)
    response.headers["HX-Redirect"] = f"/runs/{name}"
    return response


@app.post("/runs/{name}/retry")
async def retry_run(name: str):
    """Move from failed/ or done/ back to pending/ and reset state for a fresh run."""
    source_file = None
    for status in ("failed", "done"):
        candidate = OPPORTUNITIES_DIR / status / f"{name}.json"
        if candidate.exists():
            source_file = candidate
            break

    if source_file is None:
        raise HTTPException(status_code=404, detail=f"Run '{name}' not found in done/ or failed/")

    try:
        with open(source_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        data = {"name": name}

    data["status"] = "pending"
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    data.pop("completed_at", None)
    data.pop("started_at", None)
    data.pop("startedAt", None)

    pending_dir = OPPORTUNITIES_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    # Write fresh pending file then remove the old one so STATUS_DIRS
    # deduplication shows it as pending and n8n's trigger fires cleanly.
    with open(pending_dir / f"{name}.json", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    source_file.unlink(missing_ok=True)

    # Also remove any stale running/ copy to avoid phantom state
    (OPPORTUNITIES_DIR / "running" / f"{name}.json").unlink(missing_ok=True)

    from fastapi.responses import Response
    response = Response(status_code=200)
    response.headers["HX-Redirect"] = f"/runs/{name}"
    return response


@app.post("/approvals/{name}/approve", response_class=HTMLResponse)
async def approve_run(
    request: Request,
    name: str,
    approver: str = Form(...),
    comment: str = Form(""),
):
    """Approve a run at the approval gate."""
    await _update_approval(name, "approved", approver, comment)
    # Signal pipeline server
    await _signal_pipeline(name, "approved", approver, comment)

    run = load_run(name)
    pending_approvals = []
    if run:
        approval = run.get("approval")
        if approval and approval.get("status") == "pending":
            pending_approvals.append(run)

    return templates.TemplateResponse(
        "partials/approvals_list.html",
        {"request": request, "approvals": pending_approvals},
    )


@app.post("/approvals/{name}/reject", response_class=HTMLResponse)
async def reject_run(
    request: Request,
    name: str,
    approver: str = Form(...),
    comment: str = Form(""),
    reason: str = Form(""),
):
    """Reject a run at the approval gate."""
    full_comment = f"{comment}\nReason: {reason}".strip() if reason else comment
    await _update_approval(name, "rejected", approver, full_comment)
    await _signal_pipeline(name, "rejected", approver, full_comment)

    run = load_run(name)
    pending_approvals = []
    if run:
        approval = run.get("approval")
        if approval and approval.get("status") == "pending":
            pending_approvals.append(run)

    return templates.TemplateResponse(
        "partials/approvals_list.html",
        {"request": request, "approvals": pending_approvals},
    )


async def _update_approval(name: str, decision: str, approver: str, comment: str):
    """Update run_state.json with approval decision."""
    run_state_path = PROJECTS_DIR / _project_name(name) / "run_state.json"
    state: dict = {}
    if run_state_path.exists():
        try:
            with open(run_state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            state = {}

    if "approval" not in state or state["approval"] is None:
        state["approval"] = {}
    state["approval"]["status"] = decision
    state["approval"]["approver"] = approver
    state["approval"]["comment"] = comment
    state["approval"]["decided_at"] = datetime.now(timezone.utc).isoformat()

    run_state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(run_state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


async def _signal_pipeline(name: str, decision: str, approver: str, comment: str):
    """Call pipeline server signal endpoint (best-effort)."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{PIPELINE_SERVER_URL}/runs/{name}/approval-signal",
                json={"decision": decision, "approver": approver, "comment": comment},
                timeout=5.0,
            )
    except Exception:
        pass  # Non-critical


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# SSE log stream
# ---------------------------------------------------------------------------

MAX_STREAM_SECONDS = 30 * 60  # 30 minutes


async def _log_stream_generator(name: str) -> AsyncGenerator[str, None]:
    """Tail pipeline.log and yield SSE events."""
    log_path = PROJECTS_DIR / _project_name(name) / "pipeline.log"
    run_state_path = PROJECTS_DIR / _project_name(name) / "run_state.json"
    start_time = time.monotonic()
    position = 0

    while True:
        # Check timeout
        if time.monotonic() - start_time > MAX_STREAM_SECONDS:
            yield "data: [Stream timeout — max duration reached]\n\n"
            break

        # Check if run is completed
        if run_state_path.exists():
            try:
                with open(run_state_path, "r", encoding="utf-8") as fh:
                    state = json.load(fh)
                if state.get("completed_at"):
                    # Drain remaining log lines then stop
                    if log_path.exists():
                        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                            fh.seek(position)
                            remaining = fh.read()
                            position = fh.tell()
                        if remaining:
                            for line in remaining.splitlines():
                                yield f"data: {line}\n\n"
                    yield "data: [Pipeline completed]\n\n"
                    break
            except (json.JSONDecodeError, OSError):
                pass

        if not log_path.exists():
            yield "data: Waiting for pipeline to start...\n\n"
            await asyncio.sleep(2)
            continue

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(position)
                chunk = fh.read()
                position = fh.tell()

            if chunk:
                for line in chunk.splitlines():
                    yield f"data: {line}\n\n"
        except OSError:
            pass

        await asyncio.sleep(1)


@app.get("/runs/{name}/stream")
async def run_stream(name: str):
    return StreamingResponse(
        _log_stream_generator(name),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
