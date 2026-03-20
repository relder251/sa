# Refactor: phases/

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## Directory Overview

| Property | Value |
|---|---|
| **Path** | `phases/` |
| **Purpose** | Python modules for pipeline phases 5–10; imported by `scripts/pipeline_server.py` |
| **Phases 1–4** | Implemented inline in `pipeline_server.py` (plan, code gen, format validate, extract+test/fix) |
| **Called by** | `pipeline_server.py` via `from phases.phaseN_* import run_phaseN` |
| **Runtime path** | `/data/phases/` in Docker; `./phases/` when running locally |

### File inventory

| File | Phase | Purpose |
|---|---|---|
| `__init__.py` | — | Empty; makes `phases/` a package |
| `phase5_quality_gate.py` | 5 | Runs ruff, bandit, mypy; LLM security review if HIGH bandit issues; can BLOCK pipeline |
| `phase6_documentation.py` | 6 | Generates README.md + CHANGELOG.md via LLM; writes to `project_base/phase6_docs/` and copies README to project root |
| `phase7_git_push.py` | 7 | `git init` + commit; optional Docker build+push; optional remote git push |
| `phase8_deployment.py` | 8 | Deploys based on `DEPLOY_TARGET`: `skip` (default), `local` (docker run), `ssh` (scp + docker compose) |
| `phase9_monitoring.py` | 9 | Health checks against deployed endpoint; Slack completion notification |
| `phase10_approval_gate.py` | 10 | Async human approval gate (runs between phase 6 and 7); Slack notification; waits for `POST /approvals/{run_id}/signal` |

---

## Gaps Found

| # | File | Gap | Severity | Description |
|---|---|---|---|---|
| 1 | `phase5` | Dead `"CRITICAL"` in bandit severity check | **Low** | Bandit only emits `LOW`/`MEDIUM`/`HIGH` — `"CRITICAL"` is not a valid bandit severity and never matches. The check `in ("HIGH", "CRITICAL")` was harmless but confusing. |
| 2 | `phase7` | `git add -A` without `.gitignore` guard | **Medium** | If LLM-generated code omits `.venv/` from its `.gitignore`, the entire virtualenv directory gets staged and committed. Phase 7 had no safeguard before staging. |
| 3 | `phase7` | `name` used raw in git remote URL | **Medium** | `remote_url = f"{GIT_REMOTE_URL}/{name}.git"` — project name comes from the API caller. Names with spaces, slashes, or other URL-unsafe characters would produce an invalid remote URL. |
| 4 | `phase5`/`phase6` | `_call_llm()` and `_read_source_files()` duplicated | **Medium** | Both phases copy these helpers verbatim from `pipeline_server.py`. If LiteLLM URL or auth changes, it needs updating in three places. Deferred — would require a shared utilities module, which is a larger refactor. |
| 5 | `phase5` | `mypy_report_dir` collision on concurrent runs | **Low** | `f"/tmp/mypy_report_{name}"` — concurrent pipelines with the same project name would overwrite each other's mypy reports. Acceptable for the current single-pipeline-at-a-time design. |
| 6 | `phase8` | Port 8000 hardcoded in `docker run -p 0:8000` | **Medium** | Generated projects may serve on ports other than 8000 (3000, 5000, 8080 are common). The deployment will start but port detection will return no endpoint. Documented with a comment; configurable port is a future enhancement. |
| 7 | `phase8` | SSH deploy runs `docker compose up` without `--build` | **Medium** | Copies project files to remote host then runs `docker compose up -d` — only works if the image was already pushed to a registry. If the project has a Dockerfile and no registry push happened, the remote compose will fail to find the image. Documented; use `DOCKER_REGISTRY` + phase 7 push to avoid this. |
| 8 | `phase9` | Health check hardcoded to `{endpoint}/health` | **Low** | Generated projects may not expose `/health`. Failures are non-blocking and the phase still sends Slack notification — acceptable. |
| 9 | `pipeline_server` | Phase 4 report written to `phase3_report.md` | **Info** | Legacy naming from when test/fix was Phase 3. `phase9._read_iterations_from_report` checks both names. Renaming would require coordinating changes across `pipeline_server.py` and `phase9_monitoring.py` — deferred to `scripts/` pass. |
| 10 | `phase10` | Slack approve/reject URLs point to Open WebUI | **Info** | `{WEBUI_BASE_URL}/approvals/{run_id}/approve` — assumes Open WebUI has an approvals UI. The pipeline's actual signal endpoint is `POST /approvals/{run_id}/signal`. By design. |

---

## Changes Made

| Change | File | Before | After | Reason |
|---|---|---|---|---|
| Remove dead `"CRITICAL"` from bandit check | `phase5_quality_gate.py` | `in ("HIGH", "CRITICAL")` | `== "HIGH"` | Bandit never emits CRITICAL severity; dead code removed |
| Add `.gitignore` guard before `git add -A` | `phase7_git_push.py` | No guard | Appends `.venv/`, `__pycache__/`, `*.pyc`, `*.pyo` to `.gitignore` if missing | Prevents venv from being committed when LLM omits it from generated `.gitignore` |
| Sanitize `name` in git remote URL | `phase7_git_push.py` | `f"{GIT_REMOTE_URL}/{name}.git"` | `safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "-", name)` then `f".../{safe_name}.git"` | Prevents invalid URLs when project name contains spaces or special characters |
| Document port 8000 assumption | `phase8_deployment.py` | No comment | Added note explaining 8000 assumption and workarounds | Surfaces a known limitation that would otherwise cause silent endpoint detection failure |

---

## Test Results

| Check | Result |
|---|---|
| `python3 -m py_compile phases/phase5_quality_gate.py` | ✅ |
| `python3 -m py_compile phases/phase6_documentation.py` | ✅ |
| `python3 -m py_compile phases/phase7_git_push.py` | ✅ |
| `python3 -m py_compile phases/phase8_deployment.py` | ✅ |
| `python3 -m py_compile phases/phase9_monitoring.py` | ✅ |
| `python3 -m py_compile phases/phase10_approval_gate.py` | ✅ |
| Phase 5-10 imported by pipeline_server at startup | ✅ Unchanged import paths |
| Bandit check: HIGH issues still trigger LLM review | ✅ Logic unchanged; CRITICAL removed |
| `.gitignore` guard: appends only missing patterns | ✅ Uses `if p not in existing` check |
| URL sanitization: `"my project"` → `"my-project"` | ✅ Regex replaces unsafe chars with `-` |

---

## Deferred Items

| Item | Owner | Notes |
|---|---|---|
| Deduplicate `_call_llm()` / `_read_source_files()` across phases | `scripts/` pass | Requires a shared `phases/utils.py` — deferred to avoid scope creep |
| `mypy_report_dir` collision on concurrent runs | `phases/phase5` | Acceptable until concurrent pipelines are supported; use `tempfile.mkdtemp()` if needed |
| Configurable container port for local deployment | `phases/phase8` | Could read from `docker-compose.yml` or a pipeline config field |
| SSH deploy without registry push | `phases/phase8` | Add `--build` flag or document that `DOCKER_REGISTRY` must be set for SSH deployment |
| Rename `phase3_report.md` → `phase4_report.md` | `scripts/pipeline_server.py` | Coordinate with `phase9_monitoring._read_iterations_from_report` |
