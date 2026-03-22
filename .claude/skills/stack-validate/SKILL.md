---
name: stack-validate
description: Use when the user invokes /stack-validate, asks to check stack health, validate the VPS deployment, confirm nginx/n8n/vault-sync are in sync, or wants a pre-work health check before starting development on the Agentic SDLC project.
---

# Stack Validate

Runs a comprehensive health check against the VPS deployment at root@187.77.208.197 and produces a status table. Run this at the start of any session that will touch production infrastructure.

## Step 1 — Run All Checks via SSH

Execute the following SSH command to gather raw data for all six checks:

```bash
ssh root@187.77.208.197 "
set -e
N8N_KEY=\$(grep N8N_API_KEY /opt/agentic-sdlc/.env | cut -d= -f2)

echo '=== BW_VERSION ==='
docker exec vault_sync bw --version 2>/dev/null || echo 'CONTAINER_ERROR'

echo '=== CONTAINER_HEALTH ==='
cd /opt/agentic-sdlc && docker compose -f docker-compose.prod.yml ps --format json 2>/dev/null | python3 -c '
import json,sys
data = sys.stdin.read().strip()
if not data:
    print(\"NO_DATA\")
else:
    try:
        containers = json.loads(data)
        if isinstance(containers, list):
            for c in containers:
                name = c.get(\"Name\") or c.get(\"Service\",\"unknown\")
                health = c.get(\"Health\",\"\") or c.get(\"Status\",\"unknown\")
                print(f\"{name} {health}\")
        else:
            print(\"PARSE_ERROR\")
    except Exception as e:
        print(f\"PARSE_ERROR: {e}\")
' 2>/dev/null || docker compose -f docker-compose.prod.yml ps 2>/dev/null

echo '=== N8N_WEBHOOKS ==='
curl -s http://localhost:5678/api/v1/workflows \
  -H \"X-N8N-API-KEY: \$N8N_KEY\" 2>/dev/null | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
    wfs = d.get(\"data\",[])
    for w in wfs:
        nodes = w.get(\"nodes\",[]) or []
        for n in nodes:
            if n.get(\"type\") == \"n8n-nodes-base.webhook\":
                path = n.get(\"parameters\",{}).get(\"path\",\"\")
                print(w[\"id\"], w[\"name\"], \"active=\"+str(w[\"active\"]), \"path=\"+path)
except Exception as e:
    print(\"ERROR:\", e)
' 2>/dev/null || echo 'N8N_UNREACHABLE'

echo '=== NGINX_CONF ==='
diff /opt/agentic-sdlc/nginx/conf.d/portal.conf \
     /opt/agentic-sdlc/nginx/conf.d/portal.conf.template 2>/dev/null \
  && echo 'IN_SYNC' || echo 'DIFF_DETECTED'

echo '=== NGINX_WEBHOOK_ROUTES ==='
grep -oP 'proxy_pass http://[^/]+/webhook/\K[^;\"]+' \
  /opt/agentic-sdlc/nginx/conf.d/portal.conf 2>/dev/null || echo 'NO_WEBHOOK_ROUTES_FOUND'

echo '=== KEYCLOAK ==='
curl -sf https://kc.sovereignadvisory.ai/health/ready --max-time 10 && echo 'OK' || echo 'FAIL'

echo '=== VAULTWARDEN ==='
curl -sf https://vault.private.sovereignadvisory.ai/alive --max-time 10 && echo 'OK' || echo 'FAIL'
"
```

## Step 2 — Parse and Display Results

After running the SSH command, evaluate each section and render the status table:

### bw CLI version check
- **Expected**: `2024.6.0`
- Pass: output matches `2024.6.0`
- Fail: any other version or `CONTAINER_ERROR`
- Fix: `docker pull vaultwarden/bw-cli:2024.6.0 && docker compose -f /opt/agentic-sdlc/docker-compose.prod.yml up -d --no-deps vault_sync`

### Container health check
- Pass: all containers show `running`, `healthy`, or blank health (no health check configured)
- Fail: any container shows `unhealthy`, `exited`, or `restarting`
- Fix for unhealthy: `docker compose -f /opt/agentic-sdlc/docker-compose.prod.yml restart <container_name>`
- Fix for exited: `docker compose -f /opt/agentic-sdlc/docker-compose.prod.yml up -d <service_name>`

### n8n webhooks vs nginx routes
- Collect all webhook paths from `=== N8N_WEBHOOKS ===` section (active workflows only)
- Collect all webhook route IDs/paths from `=== NGINX_WEBHOOK_ROUTES ===` section
- Pass: every nginx webhook path appears as an active n8n webhook
- Fail: nginx references a webhook path not present in active n8n workflows
- Fix: Re-import the affected workflow from `workflows/` directory or update nginx `portal.conf.template` with the new webhook ID, then `docker compose -f /opt/agentic-sdlc/docker-compose.prod.yml restart portal`

### nginx portal.conf sync
- `=== NGINX_CONF ===` section shows `IN_SYNC` or `DIFF_DETECTED`
- Pass: `IN_SYNC` (no diff between portal.conf and portal.conf.template)
- Fail: `DIFF_DETECTED` — the running conf has drifted from the template
- Note: If template uses `${ENV_VAR}` substitution, diffs in substituted values are expected. Only flag if structural `location` blocks differ.
- Fix: Review diff with `diff /opt/agentic-sdlc/nginx/conf.d/portal.conf /opt/agentic-sdlc/nginx/conf.d/portal.conf.template`, update template, restart portal container

### Keycloak reachability
- Pass: `=== KEYCLOAK ===` section ends with `OK`
- Fail: `FAIL` or curl error
- Fix: `docker compose -f /opt/agentic-sdlc/docker-compose.prod.yml restart keycloak`; check `docker compose logs -f keycloak`

### Vaultwarden reachability
- Pass: `=== VAULTWARDEN ===` section ends with `OK`
- Fail: `FAIL` or curl error
- Fix: `docker compose -f /opt/agentic-sdlc/docker-compose.prod.yml restart vaultwarden`; check `docker compose logs -f vaultwarden`

## Step 3 — Render Status Table

Present results as a formatted table using these symbols:
- `✅` — check passed
- `⚠️ ` — check failed or degraded
- `❓` — check could not run (data unavailable)

Example output format:
```
Check                    Status
──────────────────────────────────────────────────────────
✅ nginx portal.conf       in sync
✅ n8n webhooks            4/4 match nginx routes
✅ vault-sync bw version   2024.6.0
⚠️  Container health        oauth2_proxy_n8n unhealthy
✅ Keycloak                 reachable
✅ Vaultwarden             reachable
```

## Step 4 — Report Failures and Fixes

For any `⚠️` row, output a block:

```
ISSUE: <what is wrong>
FIX:   <exact command to run>
```

If all checks pass, output:
```
Stack is healthy. All 6 checks passed. Safe to proceed.
```

## Notes

- Always SSH as `root@187.77.208.197` — no sudo needed
- The `.env` file with `N8N_API_KEY` lives at `/opt/agentic-sdlc/.env`
- `docker-compose.prod.yml` is the production compose file; `docker-compose.yml` is for local dev
- If the VPS is unreachable, report: `SSH to VPS failed — check connectivity or run: ssh root@187.77.208.197 'echo ok'`
- The nginx `portal.conf` is generated from `portal.conf.template` via `envsubst` at container start — minor substitution differences are normal; flag only structural changes
