---
name: n8n-workflow
description: Use when the user invokes /n8n-workflow, asks to list n8n workflows, validate webhook routing, export workflows for git tracking, detect drift between live n8n state and the workflows/ directory, or activate/deactivate a workflow. This skill manages n8n workflow state via the REST API over SSH to the VPS.
---

# n8n Workflow Manager

A guided CLI workflow for inspecting and managing n8n workflows on the VPS at `root@187.77.208.197`. Covers listing, webhook routing validation, export for git tracking, drift detection, and activation toggling.

## Quick Reference

| Operation | Section |
|-----------|---------|
| List all workflows | Step 1 |
| Validate webhook routing vs nginx | Step 2 |
| Export workflows to `workflows/` | Step 3 |
| Detect git drift | Step 4 |
| Activate / deactivate a workflow | Step 5 |

**API base (from VPS):** `http://localhost:5678/api/v1/`
**Auth:** `X-N8N-API-KEY` header — value read from `/opt/agentic-sdlc/.env`

---

## Step 1 — List All Workflows

Fetch all workflows, showing ID, name, active status, and any registered webhook paths:

```bash
ssh root@187.77.208.197 '
N8N_KEY=$(grep N8N_API_KEY /opt/agentic-sdlc/.env | cut -d= -f2)
curl -s http://localhost:5678/api/v1/workflows \
  -H "X-N8N-API-KEY: $N8N_KEY" | python3 -c "
import json, sys
d = json.load(sys.stdin)
wfs = d.get(\"data\", [])
print(f\"{len(wfs)} workflows found\n\")
print(f\"{'ID':<20} {'Active':<8} {'Name'}\")
print(\"-\" * 70)
for w in wfs:
    nodes = w.get(\"nodes\", []) or []
    webhooks = [n.get(\"parameters\",{}).get(\"path\",\"\")
                for n in nodes if n.get(\"type\") == \"n8n-nodes-base.webhook\"]
    print(f\"{w['id']:<20} {str(w['active']):<8} {w['name']}\")
    if webhooks:
        print(f\"  webhooks: {', '.join(webhooks)}\")
"
'
```

**Expected output format:**
```
12 workflows found

ID                   Active   Name
----------------------------------------------------------------------
DXjMzKwRB6c54GCY     True     Portal: Provision Service
  webhooks: portal-provision
VHUS5Dx1q9HBZPln     True     Portal: Get Services
  webhooks: portal-services
```

**Interpret results:**
- `Active = False` on a webhook-bearing workflow means the webhook is dead — nginx will get 404s
- Multiple webhook nodes in one workflow are all listed
- Note the IDs of any ID-embedded webhook paths (needed for nginx `portal.conf.template`)

---

## Step 2 — Validate Webhook Routing

Compare n8n's registered webhook paths against nginx `portal.conf.template` proxy_pass targets. Flags mismatches.

```bash
ssh root@187.77.208.197 '
N8N_KEY=$(grep N8N_API_KEY /opt/agentic-sdlc/.env | cut -d= -f2)

echo "=== N8N ACTIVE WEBHOOKS ==="
curl -s http://localhost:5678/api/v1/workflows \
  -H "X-N8N-API-KEY: $N8N_KEY" | python3 -c "
import json, sys
d = json.load(sys.stdin)
paths = set()
for w in d.get(\"data\", []):
    if not w.get(\"active\"):
        continue
    for n in (w.get(\"nodes\") or []):
        if n.get(\"type\") == \"n8n-nodes-base.webhook\":
            p = n.get(\"parameters\",{}).get(\"path\",\"\")
            if p:
                paths.add(p)
for p in sorted(paths):
    print(p)
" 2>/dev/null

echo ""
echo "=== NGINX WEBHOOK PROXY_PASS TARGETS ==="
grep -oP "proxy_pass http://n8n:5678/webhook/\K[^;]+" \
  /opt/agentic-sdlc/nginx/conf.d/portal.conf.template 2>/dev/null \
  | sed "s|/webhook$||" | sort
'
```

After gathering output, perform the cross-check:

**Webhook path matching rules:**
- Plain path `portal-update` in n8n → nginx should have `proxy_pass http://n8n:5678/webhook/portal-update`
- ID-embedded path `DXjMzKwRB6c54GCY/webhook/portal-provision` → n8n path is just `portal-provision`; verify the workflow ID in the listing matches the nginx URL segment

**Mismatch cases and fixes:**

| Symptom | Cause | Fix |
|---------|-------|-----|
| nginx path references workflow ID not in n8n | Workflow was recreated | Follow `docs/runbooks/n8n-workflow-id-rotation.md` |
| nginx proxies to `/webhook/foo` but n8n has no active webhook `foo` | Workflow inactive or deleted | Activate workflow (Step 5) or re-import from `workflows/` |
| n8n has active webhook but nginx has no route | New workflow added without nginx update | Add `location /api/foo` block to `portal.conf.template`; restart portal |

---

## Step 3 — Export Workflows to `workflows/`

Dump all workflow JSON files from n8n to the `workflows/` directory on the VPS, then commit to git for tracking. Run this after any workflow change in the n8n UI.

### 3a — Export all via n8n CLI (recommended)

```bash
ssh root@187.77.208.197 "
docker exec n8n n8n export:workflow --all --output=/data/workflows/
"
```

### 3b — Export a single workflow by ID

```bash
ssh root@187.77.208.197 "
docker exec n8n n8n export:workflow --id=<WORKFLOW_ID> \
  --output=/data/workflows/<filename>.json
"
```

### 3c — Export via REST API (if CLI is unavailable)

```bash
ssh root@187.77.208.197 '
N8N_KEY=$(grep N8N_API_KEY /opt/agentic-sdlc/.env | cut -d= -f2)
OUTDIR=/opt/agentic-sdlc/workflows

curl -s http://localhost:5678/api/v1/workflows \
  -H "X-N8N-API-KEY: $N8N_KEY" | python3 -c "
import json, sys, urllib.request, os

data = json.load(sys.stdin)
key = open(\"/opt/agentic-sdlc/.env\").read()
import re
m = re.search(r\"N8N_API_KEY=(.+)\", key)
key = m.group(1).strip() if m else \"\"
outdir = \"/opt/agentic-sdlc/workflows\"
os.makedirs(outdir, exist_ok=True)

for w in data.get(\"data\", []):
    wid = w[\"id\"]
    req = urllib.request.Request(
        f\"http://localhost:5678/api/v1/workflows/{wid}\",
        headers={\"X-N8N-API-KEY\": key}
    )
    with urllib.request.urlopen(req) as r:
        wf = json.load(r)
    name = w[\"name\"].lower().replace(\" \",\"_\").replace(\":\",\"\").replace(\"/\",\"-\")
    fname = f\"{outdir}/{name}.json\"
    with open(fname, \"w\") as f:
        json.dump(wf, f, indent=2)
    print(f\"exported: {fname}\")
"
'
```

After exporting, review diffs in git before committing to capture any unintended UI changes.

---

## Step 4 — Detect Drift

Identify whether the `workflows/` directory in git is out of sync with live n8n state.

### 4a — Count comparison (fast sanity check)

```bash
ssh root@187.77.208.197 '
N8N_KEY=$(grep N8N_API_KEY /opt/agentic-sdlc/.env | cut -d= -f2)
N8N_COUNT=$(curl -s http://localhost:5678/api/v1/workflows \
  -H "X-N8N-API-KEY: $N8N_KEY" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get(\"data\",[])))") 
FILE_COUNT=$(ls /opt/agentic-sdlc/workflows/*.json 2>/dev/null | wc -l)
echo "n8n workflows: $N8N_COUNT"
echo "workflows/ files: $FILE_COUNT"
[ "$N8N_COUNT" -eq "$FILE_COUNT" ] && echo "COUNT: MATCH" || echo "COUNT: MISMATCH — run export (Step 3) and commit"
'
```

### 4b — Content drift check (full diff)

After running Step 3a to export, check git diff on the local repo:

```bash
cd /home/user/vibe_coding/Agentic_SDLC
git diff workflows/
git status workflows/
```

**Interpret results:**
- No diff → workflows/ is in sync with live n8n
- Modified files → workflow was edited in n8n UI; review diff, commit if intentional
- New untracked files → new workflow exists in n8n but not committed to git
- Files deleted from git but present in n8n → file was deleted without removing workflow

**Action for drift:**
1. Run Step 3 (export) to refresh `workflows/`
2. Review `git diff workflows/` — confirm changes are intentional
3. Commit: `git add workflows/ && git commit -m "ops: sync workflows from live n8n"`

---

## Step 5 — Activate / Deactivate a Workflow

Toggle a workflow's active state via the n8n REST API.

First, find the workflow ID using Step 1.

### Activate

```bash
ssh root@187.77.208.197 '
N8N_KEY=$(grep N8N_API_KEY /opt/agentic-sdlc/.env | cut -d= -f2)
WORKFLOW_ID="<paste workflow ID here>"
curl -s -X PATCH "http://localhost:5678/api/v1/workflows/$WORKFLOW_ID" \
  -H "X-N8N-API-KEY: $N8N_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"active\": true}" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(\"ID:\", d.get(\"id\"))
print(\"Name:\", d.get(\"name\"))
print(\"Active:\", d.get(\"active\"))
"
'
```

### Deactivate

```bash
ssh root@187.77.208.197 '
N8N_KEY=$(grep N8N_API_KEY /opt/agentic-sdlc/.env | cut -d= -f2)
WORKFLOW_ID="<paste workflow ID here>"
curl -s -X PATCH "http://localhost:5678/api/v1/workflows/$WORKFLOW_ID" \
  -H "X-N8N-API-KEY: $N8N_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"active\": false}" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(\"ID:\", d.get(\"id\"))
print(\"Active:\", d.get(\"active\"))
"
'
```

**After deactivating a webhook workflow:** its nginx-proxied endpoint will return 404. Notify users if the affected workflow backs a portal API route.

**After activating:** verify via Step 2 that the webhook path now appears in the active webhook list.

---

## Step 6 — Post-Deploy Validation Checklist

Run after every deploy touching n8n workflows or nginx config:

- [ ] Run Step 1 — confirm expected workflows are present and active
- [ ] Run Step 2 — confirm all nginx webhook routes resolve to active n8n webhooks; no orphan routes, no unrouted webhooks
- [ ] Spot-check a live endpoint:
  ```bash
  curl -s -o /dev/null -w "%{http_code}" \
    https://home.private.sovereignadvisory.ai/api/portal-services
  ```
  - `200` = healthy
  - `404` = webhook path mismatch — see Step 2
  - `502` = n8n down — `docker compose -f /opt/agentic-sdlc/docker-compose.prod.yml restart n8n`
- [ ] If workflow IDs changed, update `nginx/conf.d/portal.conf.template` and restart portal
- [ ] Run Step 4 — confirm `workflows/` is not drifted from live state; commit if needed

---

## Workflow ID Reference (current)

| Workflow Name | ID | Webhook Path | ID-embedded in nginx? |
|---|---|---|---|
| Portal: Provision Service | `DXjMzKwRB6c54GCY` | `portal-provision` | Yes |
| Portal: Get Services | `VHUS5Dx1q9HBZPln` | `portal-services` | Yes |
| Portal: Track Recent | `GOpCkjqJyPjy5dgG` | `portal-track-recent` | Yes |
| Portal: Update Categories | _(plain path)_ | `portal-update-categories` | No |
| Portal: Update Service | _(plain path)_ | `portal-update` | No |
| Portal: Delete Service | _(plain path)_ | `portal-delete` | No |
| Credential Rotation | _(plain path)_ | `rotate-credential` | No |

Always re-run Step 1 to get fresh IDs — this table may be stale after a workflow recreation. If IDs have changed, follow `docs/runbooks/n8n-workflow-id-rotation.md`.

---

## Notes

- The n8n API key lives at `/opt/agentic-sdlc/.env` (key: `N8N_API_KEY`)
- Three webhook paths embed the workflow ID in the nginx `proxy_pass` URL — these break silently when a workflow is recreated; use `docs/runbooks/n8n-workflow-id-rotation.md` to fix
- The `workflows/` directory on the VPS is mounted into n8n at `/data/workflows`; files there are NOT auto-loaded — use `n8n import:workflow` or the REST API to load them into n8n
- Never edit workflow JSON files directly expecting n8n to pick up changes — always import via CLI (`docker exec n8n n8n import:workflow --input=...`) or REST API
- The VPS SSH target is always `root@187.77.208.197`
- Production compose file: `/opt/agentic-sdlc/docker-compose.prod.yml`; local dev: `docker-compose.yml`
- n8n REST API docs: `http://localhost:5678/api/v1/` (Swagger UI available at the same host)
