# Refactor Backlog — Agentic SDLC

**Compiled:** 2026-03-20
**Source:** All documents in `refactor/`
**Purpose:** Actionable, ordered backlog of all deferred gaps. Dependencies are protected — no item breaks a prerequisite.

---

## Priority Legend

| Label | Meaning |
|---|---|
| **HIGH** | Security risk or data integrity issue; do first |
| **MEDIUM** | Functional gap or correctness issue that causes silent failure |
| **LOW** | Quality, hygiene, or resilience improvement |
| **INFO** | Runbook / documentation task; no code change |

---

## Task List

### HIGH — Security & Data Integrity

**T-01 · Scrub N8N_API_KEY JWT from git history**
Source: `scripts.md`
A long-lived JWT (exp ~2027) granting full n8n API access was committed to the repository. Anyone with read access to git history has the credential. The in-flight fix removed it from the working tree, but the commit history still contains it.
**Action:** Run `git filter-repo --path scripts/deploy_workflow.sh --invert-paths` or BFG Repo Cleaner to rewrite history. Force-push to remote. Rotate the N8N_API_KEY (generate a new Personal API Token in n8n Settings > API).
**Must do before:** Nothing blocks this. Do immediately.
**Dependency for:** None.

---

**T-02 · Replace hardcoded LITELLM_API_KEY in nginx portal config with envsubst**
Source: `deferred.md`, `nginx.md`
`nginx/conf.d/portal.conf` contains `proxy_set_header Authorization "Bearer sk-vibe-coding-key-123"`. This is also the default key used in `pipeline_server.py` and `smoke_test.sh`. The root issue is that nginx's config does not support env var substitution natively — the fix requires converting to a `.template` file and using the nginx Docker image's built-in `envsubst` entrypoint.
**Action:**
1. Rename `nginx/conf.d/portal.conf` → `nginx/conf.d/portal.conf.template`
2. Replace `Bearer sk-vibe-coding-key-123` with `Bearer ${LITELLM_API_KEY}`
3. In `docker-compose.yml`, add `LITELLM_API_KEY: ${LITELLM_API_KEY}` to the `portal` nginx service environment
4. Verify `envsubst` is active in the nginx:alpine entrypoint (it is by default in the official image)
5. Rebuild and test: `docker compose up -d --build portal`
**Must do after:** Nothing.
**Dependency for:** T-20 (workflow ID runbook) — both touch the portal nginx config.

---

### MEDIUM — Functional Correctness

**T-03 · Fix lead-review KEYCLOAK_ISSUER in docker-compose.prod.yml**
Source: `docker-compose.prod.yml.md`
`lead-review` service has `KEYCLOAK_ISSUER: http://keycloak:8080/realms/agentic-sdlc` in the prod compose. There is no `keycloak` service in `docker-compose.prod.yml` — Keycloak is external at `kc.sovereignadvisory.ai`. Token validation in the lead-review OIDC flow will fail if the issuer URL cannot be resolved from inside the container.
**Action:** On VPS, verify whether `http://keycloak:8080` resolves via Docker DNS or host entry. If not, change to `https://kc.sovereignadvisory.ai/realms/agentic-sdlc`. Test: `docker exec sa_lead_review curl -s http://keycloak:8080/realms/agentic-sdlc/.well-known/openid-configuration` — if it returns JSON, no change needed; if it fails, update the URL.
**Must do after:** Nothing.
**Dependency for:** T-22 (client secret rotation doc) — both concern lead-review OIDC.

---

**T-04 · Strip inline .env comments from DOCKER_REGISTRY in pipeline_server.py**
Source: `deferred.md`
`DOCKER_REGISTRY=# optional: ...` in `.env` sets the variable to the comment text, not an empty string. `pipeline_server.py` reads `os.getenv("DOCKER_REGISTRY", "")` — if the user leaves the commented default, the docker push command in phase 7 uses `# optional: ...` as the registry URL, silently constructing an invalid image tag.
**Action:** In `scripts/pipeline_server.py`, after reading `DOCKER_REGISTRY`, strip anything from `#` onwards and whitespace:
```python
DOCKER_REGISTRY = re.sub(r'\s*#.*', '', os.getenv("DOCKER_REGISTRY", "")).strip()
```
Also add `DOCKER_REGISTRY=` (empty, no comment on same line) to `.env.example` with the comment on the line above.
**Must do after:** Nothing.
**Dependency for:** T-06 (phase 8 port), T-07 (phase 8 SSH) — all touch pipeline phases and may be batched.

---

**T-05 · Add timeout wrapper to backup.sh**
Source: `deferred.md`, `scripts.md`
`backup.sh` runs `pg_dumpall` which can hang silently on a locked database or network issue. The backup container runs unmonitored; a hung backup blocks the next scheduled run and gives no signal to the operator.
**Action:** Wrap the `pg_dumpall` call with `timeout 300` (5 minutes):
```bash
timeout 300 pg_dumpall -U "$POSTGRES_USER" > "$tmp_file" || { echo "pg_dumpall timed out or failed"; exit 1; }
```
Test: `docker exec backup bash scripts/backup.sh` — verify atomic write and exit code.
**Must do after:** Nothing.
**Dependency for:** Nothing.

---

**T-06 · Make container port configurable in phase8_deployment.py**
Source: `phases.md`
`phase8_deployment.py` hardcodes `docker run -p 0:8000` for local deployment. Generated projects may serve on port 3000, 5000, or 8080. The container starts but endpoint detection returns no URL because the exposed port doesn't match the actual listener.
**Action:** Read port from a pipeline config field (e.g., `DEPLOY_PORT` env var or a `port` field in the pipeline request body). Default to 8000 for backward compat. Update the `docker run` call and endpoint construction.
**Must do after:** T-04 (both touch pipeline execution path — batch if possible).
**Dependency for:** T-07 (both are phase 8 changes — fix together).

---

**T-07 · Fix/document Phase 8 SSH deploy registry requirement**
Source: `phases.md`
`phase8_deployment.py` SSH mode copies project files to the remote host then runs `docker compose up -d` without `--build`. If the project image hasn't been pushed to a registry (i.e., phase 7 skipped or `DOCKER_REGISTRY` unset), the remote compose fails with "image not found".
**Action:** Two options: (a) add `--build` to the remote `docker compose up -d --build` call (requires Docker build on the remote host, which may not have the source files), or (b) add a pre-flight check that `DOCKER_REGISTRY` is set when `DEPLOY_TARGET=ssh` and fail-fast with a clear message. Option (b) is safer. Also update `.env.example` comment to document the `ssh` target requirement.
**Must do after:** T-06 (both are phase 8 changes).
**Dependency for:** Nothing.

---

**T-08 · Add PKCE S256 to homelab oauth2-proxy services**
Source: `docker-compose.yml.md`
`docker-compose.prod.yml` has `--code-challenge-method=S256` on all oauth2-proxy services (added in commit `c8f6a47`). `docker-compose.yml` (homelab) only has it on `oauth2-proxy-portal`. The gap means homelab and prod Keycloak client configs diverge, making parity testing unreliable.
**Action:** First verify the Keycloak client for each service in the homelab realm is configured to accept PKCE (set `pkceCodeChallengeMethod: S256` in each client). Then add `--code-challenge-method=S256` to `oauth2-proxy-n8n`, `oauth2-proxy-litellm`, `oauth2-proxy-jupyter`, `oauth2-proxy-webui` in `docker-compose.yml`. Restart each proxy and verify SSO login still works.
**Must do after:** Keycloak client config change must precede the compose change.
**Dependency for:** Nothing.

---

**T-09 · Verify and fix SSL cert path on VPS**
Source: `docker-compose.prod.yml.md`
`certbot` in prod compose writes renewed certs to `./ssl` (relative to the compose working directory). Both `nginx` and `nginx-private` mount `/opt/sovereignadvisory/ssl`. If `./ssl` does not resolve to `/opt/sovereignadvisory/ssl` on the VPS (e.g., via symlink), cert renewals will succeed but nginx will serve stale/expired certs silently.
**Action:** On the VPS: `ls -la /opt/sovereignadvisory/ssl` and `ls -la $(pwd)/ssl`. If they differ, add a symlink: `ln -s /opt/sovereignadvisory/ssl ssl`. Or standardize: update the `certbot` volume mount to use the absolute path.
**Must do after:** Nothing (VPS-side verification).
**Dependency for:** T-12 (ssl_stapling is only meaningful if certs are correctly served).

---

**T-10 · Deduplicate test_runner_server.py and pipeline_server.py shared utilities**
Source: `scripts.md`
`test_runner_server.py` (Flask, port 5001) and `pipeline_server.py` (FastAPI, port 5002) share multiple helpers copy-pasted verbatim: `read_source_files`, `run_postprocess`, `call_llm_fix`, etc. Changes to shared behavior (e.g., the version-loosening fix this session) must be applied in both files manually.
**Action:** Extract shared functions into `scripts/shared_utils.py`. Update both servers to import from it. Keep framework-specific code (Flask routes vs FastAPI routes) in each server. This is a refactor that requires testing both services after the change.
**Must do after:** T-04, T-06, T-07 (finish the phase changes first; this refactor touches the same codebase).
**Dependency for:** Nothing — but reduces ongoing maintenance burden.

---

### LOW — Quality & Resilience

**T-11 · Convert bare proxy_pass to late-binding for n8n webhook paths in nginx**
Source: `nginx-public.md`, `nginx-private.md`
Both `nginx-public/conf.d/10-ssl.conf` and `nginx-private/conf.d/private.conf` have `/n8n/webhook/` location blocks using direct `proxy_pass http://n8n:5678/webhook/` without the `resolver + set $upstream` pattern used everywhere else. If n8n is restarting when nginx reloads, nginx fails to start because it cannot resolve `n8n` at config load time.
**Action:** In both files, convert the n8n webhook `proxy_pass` to:
```nginx
set $up_n8n http://n8n:5678;
proxy_pass $up_n8n/webhook/;
```
Ensure `resolver 127.0.0.11 valid=30s; resolver_timeout 5s;` is present in each server block (already added to private.conf this session).
**Must do after:** T-02 (if touching nginx files, batch the changes).
**Dependency for:** Nothing.

---

**T-12 · Add ssl_stapling to nginx-private server blocks**
Source: `nginx-private.md`
The `nginx-private` server blocks do not have `ssl_stapling on; ssl_stapling_verify on;`. OCSP stapling reduces TLS handshake latency by bundling the certificate status response. Already added to `nginx-public` in this refactor pass.
**Action:** Add to each HTTPS server block in `nginx-private/conf.d/private.conf`:
```nginx
ssl_stapling on;
ssl_stapling_verify on;
```
Test: `docker exec sa_nginx_private nginx -t && docker exec sa_nginx_private nginx -s reload`.
**Must do after:** T-09 (certs must be correctly served first for stapling to work).
**Dependency for:** Nothing.

---

**T-13 · Fix sync_tier_groups to skip already-registered entries**
Source: `free_model_sync.py.md`
`sync_tier_groups()` in `free_model_sync.py` calls `/model/new` for all tier group entries (`free/chat`, `free/code`, etc.) on every sync run without checking whether the entry already exists. Over many runs, LiteLLM accumulates duplicate group entries in its database.
**Action:** Before calling `/model/new` for a group entry, fetch `/model/info` and check if a model with that `model_name` and `litellm_params.model` already exists. Only add if absent.
**Must do after:** Nothing.
**Dependency for:** Nothing.

---

**T-14 · Add provider prefix to slugify to prevent model name collisions**
Source: `free_model_sync.py.md`
`slugify()` discards the provider prefix: `openrouter/meta-llama/llama-3.3-70b-versatile` becomes `llama-3.3-70b-versatile`. If Groq and OpenRouter both offer a model with the same base name, their individual entries collide to the same `free/<slug>` key, and one overwrites the other silently.
**Action:** Extract the provider from the model string and prepend it:
```python
def slugify(model: str) -> str:
    parts = model.split("/")
    provider = parts[0] if len(parts) > 1 else "unknown"
    base = parts[-1]
    slug = re.sub(r"[^a-z0-9-]", "-", base.lower()).strip("-")
    return f"{provider}-{slug}"
```
Update tests/dry-run to validate the new slug format.
**Must do after:** T-13 (both touch `free_model_sync.py` — batch together).
**Dependency for:** Nothing.

---

**T-15 · Use tempfile.mkdtemp() for mypy_report_dir in phase5**
Source: `phases.md`
`phase5_quality_gate.py` uses `/tmp/mypy_report_{name}` as the mypy output directory. If two pipeline runs with the same project name execute concurrently, they share and overwrite each other's mypy reports. Currently acceptable (single-pipeline-at-a-time design), but brittle.
**Action:** Replace:
```python
mypy_report_dir = f"/tmp/mypy_report_{name}"
```
With:
```python
import tempfile
mypy_report_dir = tempfile.mkdtemp(prefix=f"mypy_{name}_")
```
And add cleanup after mypy completes: `shutil.rmtree(mypy_report_dir, ignore_errors=True)`.
**Must do after:** Nothing.
**Dependency for:** Nothing.

---

**T-16 · Create dedicated DB users for n8n and keycloak**
Source: `postgres-init.md`, `deferred.md`
n8n and Keycloak connect to PostgreSQL using the `litellm` superuser credentials. No privilege separation exists. A compromised n8n or Keycloak container has full database access.
**Action:**
1. Add `postgres-init/05-create-service-users.sql` that creates `n8n_user` and `keycloak_user` with passwords and `GRANT ALL ON DATABASE n8n TO n8n_user`.
2. Update `.env.example` with `N8N_DB_USER`, `N8N_DB_PASSWORD`, `KC_DB_USER`, `KC_DB_PASSWORD`.
3. Update `docker-compose.yml` n8n and Keycloak DB env vars.
4. This only applies to new installs (init scripts don't re-run on existing volumes) — add a manual migration comment.
**Must do after:** T-09 (infrastructure stable before touching DB auth).
**Dependency for:** Nothing.

---

**T-17 · Update Dockerfile.pipeline dependency versions**
Source: `root-files.md`
`Dockerfile.pipeline` has `fastapi==0.115.0` and `uvicorn==0.30.0` while `requirements-lead-review.txt` uses `fastapi==0.115.5` and `uvicorn==0.31.x`. Minor drift; not a bug, but inconsistency could mask incompatibility if a dependency requires a specific fastapi patch.
**Action:** Update `Dockerfile.pipeline` to match `requirements-lead-review.txt` on the next pipeline-server rebuild. No emergency — include in the next planned `docker compose build pipeline-server`.
**Must do after:** T-10 (if shared_utils refactor touches Dockerfile.pipeline, batch together).
**Dependency for:** Nothing.

---

**T-18 · Add NOTIFY_SMS_EMAIL to .env and .env.example**
Source: `docker-compose.prod.yml.md`
`docker-compose.prod.yml` passes `NOTIFY_SMS_EMAIL=${NOTIFY_SMS_EMAIL}` to n8n. The variable is not in `.env` — compose warns "not set" and n8n SMS-to-email notifications fail silently.
**Action:** Add `NOTIFY_SMS_EMAIL=` to `.env.example` with a comment (same section as `NOTIFY_SMS_TO`). Set the value in `.env` on the VPS to the SMS-to-email gateway address.
**Must do after:** Nothing.
**Dependency for:** Nothing.

---

### INFO — Runbooks & Documentation

**T-19 · Rename phase3_report.md to phase4_report.md**
Source: `phases.md`, `deferred.md`
`pipeline_server.py` writes the test/fix iteration report as `phase3_report.md` (legacy naming from when test/fix was Phase 3). Phase 9 reads it with a `try both names` fallback. The naming mismatch creates confusion when reading logs or the output directory.
**Action:** In `scripts/pipeline_server.py`, change the report filename to `phase4_report.md`. In `phases/phase9_monitoring.py`, update `_read_iterations_from_report` to look for `phase4_report.md` first (keep `phase3_report.md` as the fallback for existing output directories).
**Must do after:** Nothing (isolated change, but coordinate both files in one commit).
**Dependency for:** Nothing.

---

**T-20 · Write n8n workflow ID update runbook**
Source: `nginx.md`
`nginx/conf.d/portal.conf` (and portal.conf.template after T-02) contains hardcoded n8n workflow IDs (`FrPgMVhkBNi9nE85`, `wgvZCgnHnlIkSEN5`, etc.) in the webhook proxy paths. If a workflow is recreated in n8n, its ID changes and the nginx proxy returns 404 silently.
**Action:** Write a runbook in `docs/runbooks/n8n-workflow-id-rotation.md` documenting:
1. How to find the new workflow ID: `docker exec n8n n8n list:workflow`
2. Which nginx config file to update
3. How to reload nginx: `docker exec sa_nginx_private nginx -s reload` (after T-02, reload the portal nginx service)
**Must do after:** T-02 (runbook should reference the post-envsubst file path).
**Dependency for:** Nothing.

---

**T-21 · Write Keycloak realm re-export runbook**
Source: `keycloak.md`
If Keycloak clients are added or modified via the admin UI without re-exporting, `keycloak/realm-export.json` drifts from the live realm. Fresh deploys will be missing those clients.
**Action:** Write `docs/runbooks/keycloak-realm-export.md` documenting:
1. When to re-export (after any client/realm config change)
2. How: `bash scripts/keycloak_export_realm.sh` (already exists — exports to `keycloak/realm-export.json`, strips secrets)
3. Commit the updated export
4. Note: secrets are not included in the export; downstream services still need `.env` values
**Must do after:** Nothing.
**Dependency for:** T-22.

---

**T-22 · Document client secret rotation process**
Source: `keycloak.md`
Keycloak client secrets are not included in realm exports. On a fresh Keycloak import (e.g., new deploy or volume wipe), all client secrets are regenerated. Services with stale `KC_CLIENT_SECRET` values in `.env` will fail OIDC token exchange silently.
**Action:** Write `docs/runbooks/keycloak-secret-rotation.md` documenting:
1. When secrets change (fresh import, manual rotation in admin UI)
2. Where to find new secrets: Keycloak Admin > Realm > Clients > {client} > Credentials > Regenerate
3. Which `.env` variables to update per service
4. Services to restart after `.env` update (all oauth2-proxy services, n8n, lead-review)
**Must do after:** T-21 (reference the re-export runbook).
**Dependency for:** Nothing.

---

**T-23 · Establish production DB backup strategy**
Source: `docker-compose.prod.yml.md`
`docker-compose.prod.yml` has no `backup` service (unlike the homelab compose, which uses `backup` + ofelia). Production PostgreSQL data is unprotected against container or volume loss.
**Action:** Choose one:
(a) Add the `backup` service + ofelia schedule from `docker-compose.yml` to `docker-compose.prod.yml` and point it at a remote mount (S3 via rclone, etc.)
(b) Set up a host-level cron on the VPS that calls `scripts/backup.sh` directly
Document the chosen strategy in `docs/runbooks/prod-db-backup.md`. Include restore procedure using `backup_test.sh`.
**Must do after:** T-05 (backup.sh timeout fix should be in before prod backups are established).
**Dependency for:** Nothing.

---

**T-24 · Establish schema migration strategy**
Source: `postgres-init.md`, `deferred.md`
`postgres-init/` scripts only run on a fresh volume. Schema changes (new columns, new tables) require manual `ALTER TABLE` on existing installations. There is no migration tooling or version tracking.
**Action:** Add a `postgres-init/99-migrations.sql` file that uses idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements for any schema changes made after the initial deploy. Document in a `SCHEMA_CHANGELOG.md` what each migration does and when it was added. Optionally evaluate Flyway or Liquibase if the schema evolves rapidly.
**Must do after:** T-16 (if adding service users, include in migration file).
**Dependency for:** Nothing.

---

## Dependency Map

```
T-01 (jwt history)       — independent; do first
T-02 (nginx envsubst)    — independent; T-20 follows
T-03 (keycloak issuer)   — independent; T-22 follows
T-04 (docker_registry)   — before T-06, T-07, T-10
T-05 (backup timeout)    — before T-23
T-06 (phase8 port)       — after T-04; before T-07
T-07 (phase8 ssh)        — after T-06
T-08 (pkce homelab)      — independent; Keycloak config first
T-09 (ssl path vps)      — before T-12
T-10 (dedup servers)     — after T-04, T-06, T-07
T-11 (nginx webhook lb)  — after T-02 if batching nginx changes
T-12 (ssl_stapling)      — after T-09
T-13 (tier group dedup)  — independent; batch with T-14
T-14 (slugify prefix)    — after T-13
T-15 (mypy tmpdir)       — independent
T-16 (db users)          — after T-09; before T-24
T-17 (dockerfile drift)  — after T-10 if batching
T-18 (sms email env)     — independent
T-19 (phase4 report)     — independent; coordinate 2 files
T-20 (n8n id runbook)    — after T-02
T-21 (realm export doc)  — independent; before T-22
T-22 (secret rotation)   — after T-21; after T-03
T-23 (prod backup)       — after T-05
T-24 (schema migration)  — after T-16
```

---

## Summary Table

| ID | Task | Difficulty | Impacted Containers / Services | ROI |
|---|---|---|---|---|
| T-01 | Scrub N8N_API_KEY JWT from git history | Easy | n8n, git remote | **Very High** — eliminates credential in repo history; one-time git operation |
| T-02 | Replace hardcoded LITELLM_API_KEY in portal nginx with envsubst | Moderate | portal (nginx), docker-compose.yml | **High** — removes hardcoded credential; enables key rotation without config edits |
| T-03 | Fix lead-review KEYCLOAK_ISSUER in prod compose | Easy | lead-review (VPS), Keycloak | **High** — OIDC auth may be broken in prod right now; VPS verify + one-line fix |
| T-04 | Strip inline .env comments from DOCKER_REGISTRY in pipeline_server.py | Easy | pipeline-server | **High** — prevents silent broken docker push with corrupted registry URL |
| T-05 | Add timeout wrapper to backup.sh | Easy | backup | **High** — prevents silent hung backup; protects daily pg_dumpall from lockout |
| T-06 | Make container port configurable in phase8_deployment.py | Moderate | pipeline-server | **Medium** — fixes local deploy for non-8000 projects; low frequency but hard to debug |
| T-07 | Fix/document Phase 8 SSH deploy registry requirement | Easy | pipeline-server | **Medium** — prevents silent remote deploy failure; low-effort guard or clear error |
| T-08 | Add PKCE S256 to homelab oauth2-proxy services | Moderate | oauth2-proxy-n8n/litellm/jupyter/webui, Keycloak | **Medium** — homelab/prod parity; reduces divergence risk; requires Keycloak changes first |
| T-09 | Verify/fix SSL cert path on VPS | Easy | certbot, nginx (public), nginx-private (VPS) | **High** — cert renewal silently broken if path mismatch; HTTPS expires unnoticed |
| T-10 | Deduplicate test_runner + pipeline_server shared utilities | Hard | pipeline-server, test-runner | **Medium** — reduces drift; fixes must be applied in two places until done |
| T-11 | Convert n8n webhook proxy_pass to late-binding in nginx | Easy | nginx-public (VPS), nginx-private | **Medium** — prevents nginx reload failure when n8n restarts; low blast radius |
| T-12 | Add ssl_stapling to nginx-private server blocks | Easy | nginx-private | **Low** — marginal TLS performance improvement; no functional risk |
| T-13 | Fix sync_tier_groups to skip existing entries | Moderate | free-model-sync, LiteLLM (free/* tier) | **Low** — prevents accumulating DB cruft; no user-visible impact |
| T-14 | Add provider prefix to slugify for collision safety | Easy | free-model-sync, LiteLLM (free/* tier) | **Low** — no collision observed today; insurance for future catalog growth |
| T-15 | Use tempfile.mkdtemp() for mypy_report_dir in phase5 | Easy | pipeline-server | **Low** — only matters when concurrent pipelines are supported; safe to defer |
| T-16 | Create dedicated DB users for n8n and Keycloak | Moderate | postgres, n8n, keycloak | **Medium** — privilege separation; only applies on fresh installs |
| T-17 | Update Dockerfile.pipeline dependency versions | Easy | pipeline-server | **Low** — minor version drift; no known incompatibility; fix on next rebuild |
| T-18 | Add NOTIFY_SMS_EMAIL to .env and .env.example | Easy | n8n (prod), .env.example | **Low** — SMS notifications only; silent failure already present |
| T-19 | Rename phase3_report.md to phase4_report.md | Easy | pipeline-server, phase9_monitoring | **Low** — cosmetic naming fix; phase9 already handles both names |
| T-20 | Write n8n workflow ID update runbook | Easy | nginx (portal), n8n | **Medium** — prevents silent 404s after workflow recreation; ops knowledge capture |
| T-21 | Write Keycloak realm re-export runbook | Easy | keycloak, all OIDC-protected services | **Medium** — prevents client drift on fresh deploys; ops knowledge capture |
| T-22 | Document client secret rotation process | Easy | keycloak, all oauth2-proxy services, n8n, lead-review | **High** — critical ops gap; secret mismatch after fresh deploy breaks all SSO |
| T-23 | Establish production DB backup strategy | Moderate | postgres (VPS), backup, ofelia (VPS) | **Very High** — no prod backup exists; single container failure = permanent data loss |
| T-24 | Establish schema migration strategy | Moderate | postgres, lead-review, all DB-backed services | **Medium** — prevents schema drift on updates; low urgency until schema evolves again |

---

*Difficulty scale: Easy = <30 min, Moderate = 30 min–2 hr, Hard = 2 hr+*
*ROI considers: security risk reduction, reliability improvement, and implementation cost*
