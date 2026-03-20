# Refactor: Deferred Items

Cross-cutting issues deferred from individual file passes. Tracked here so they
don't get lost and can be prioritized independently.

---

## Credential Management

### [HIGH] Hardcoded API key in nginx portal config

| Property | Value |
|---|---|
| **File** | `nginx/conf.d/portal.conf` |
| **Line** | `proxy_set_header Authorization "Bearer sk-vibe-coding-key-123";` |
| **Risk** | Low (internal-only server, not TLS, not public-facing) — but bad practice |
| **Broader issue** | `sk-vibe-coding-key-123` is the default LITELLM_API_KEY hardcoded in `CLAUDE.md`, `pipeline_server.py`, and `smoke_test.sh`. The root fix is ensuring all services read from `.env`, not from code. |

**Recommended fix (envsubst, Option A):**
1. Rename `nginx/conf.d/portal.conf` → `nginx/conf.d/portal.conf.template`
2. Replace literal key with `${LITELLM_API_KEY}` in the template
3. In `docker-compose.yml`, pass env var to the portal nginx service:
   ```yaml
   nginx-portal:
     environment:
       - LITELLM_API_KEY=${LITELLM_API_KEY}
   ```
4. nginx Docker entrypoint runs `envsubst` on all `.template` files automatically
5. Vaultwarden remains the human-facing source of truth; `.env` is the runtime injection point

**Deferred because:** Requires coordinated change to `docker-compose.yml` (currently mid-refactor) and the portal nginx service definition.

---

## Docker Swarm Mode Assessment

**User question (2026-03-20):** Docker Swarm mode was raised as an alternative for secrets injection. Decision: **Do not migrate to Swarm.**

See evaluation below — documented here for future reference.

**What Swarm adds:**
- Native `docker secret` — secret files injected into containers at `/run/secrets/<name>`, never stored in environment or config
- Rolling deploys (`docker service update --image`)
- Multi-node scheduling and HA
- Health-based automatic restarts across nodes

**What Swarm costs for this stack:**
- `docker-compose.yml` becomes `docker stack deploy` — not the same format. `depends_on`, `build:`, named volumes, and many compose fields are ignored or unsupported in Swarm mode.
- Every service becomes a Swarm service — debugging changes (`docker exec`, direct container restart) gets more cumbersome
- Single-node Swarm works but provides none of the HA benefits; it's all cost, minimal gain
- Secrets must be created via `docker secret create` CLI — secrets are not in `.env` anymore, which complicates fresh deploys and backup/restore
- Ollama GPU passthrough requires workarounds — Swarm's resource reservation syntax differs from compose

**Verdict for this stack:** Not worth it. The stack runs on a single homelab node. The credential problem is better solved by envsubst templates + `.env` (Option A above), which keeps the compose file format, requires no migration, and achieves the same result of keeping secrets out of config files.

**Revisit if:** Stack migrates to multi-node (homelab + VPS active-active) or if Docker secrets become a compliance requirement.

---

## Other Cross-Cutting Deferred Items

| Item | Source | Notes |
|---|---|---|
| `DOCKER_REGISTRY` reads inline `.env` comment as value | `scripts/pipeline_server.py` | `DOCKER_REGISTRY=# optional: ...` sets env var to comment text. Fix: strip comments in env reading or add a guard. |
| Deduplicate `_call_llm` / `_read_source_files` | `phases/` pass | **Done** — `phases/utils.py` created 2026-03-20 |
| Rename `phase3_report.md` → `phase4_report.md` | `pipeline_server.py` | Coordinate with `phase9._read_iterations_from_report` which checks both names |
| `mypy_report_dir` collision on concurrent pipeline runs | `phases/phase5` | `/tmp/mypy_report_{name}` — safe until concurrent pipelines are supported |
| `free_model_sync.py` duplicate tier group entries | `free_model_sync.py` | Appends without dedup check; harmless but grows on each sync |
| `backup.sh` needs exec timeout wrapper | `scripts/backup.sh` | Long-running pg_dumpall can hang silently; add `timeout 300` |
| Dedicated DB users for n8n and keycloak | `postgres-init/` | Both connect as litellm superuser; acceptable for homelab |
| Schema migration strategy | `postgres-init/` | No migration tooling; manual `ALTER TABLE` on existing installs |
