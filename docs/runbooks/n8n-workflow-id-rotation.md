# Runbook: n8n Workflow ID Rotation

## Background

Only **one** webhook proxy path in `nginx/conf.d/portal.conf.template` embeds an n8n workflow ID:

```
/api/portal-provision  →  http://n8n:5678/webhook/<WORKFLOW_ID>/webhook/portal-provision
```

The other three portal endpoints (`portal-update-categories`, `portal-update`, `portal-delete`) use plain `/webhook/<path>` paths and are **not affected** by workflow recreation.

**Current workflow ID:** `DXjMzKwRB6c54GCY` (workflow: *Portal: Provision Service*)

---

## When this happens

- After deleting and recreating the *Portal: Provision Service* workflow in n8n
- After importing workflows into a fresh n8n database
- After a full n8n database wipe/restore

**Symptom:** `POST /api/portal-provision` returns `404` even though n8n is healthy.

---

## Step 1: Find the new workflow ID

```bash
# List all workflows and their IDs
docker exec n8n n8n list:workflow
```

Look for the row with `Name = Portal: Provision Service`. Note the value in the `ID` column (e.g., `AbCdEfGhIjKlMnOp`).

Alternatively, open the n8n UI → Workflows → click *Portal: Provision Service* → the ID appears in the browser URL bar.

---

## Step 2: Update the nginx template

```bash
# Replace old ID with new ID (run from repo root)
OLD_ID="DXjMzKwRB6c54GCY"
NEW_ID="<paste new ID here>"

sed -i "s|/webhook/${OLD_ID}/webhook/|/webhook/${NEW_ID}/webhook/|g" \
    nginx/conf.d/portal.conf.template
```

Verify the change:

```bash
grep proxy_pass nginx/conf.d/portal.conf.template | grep portal-provision
# Should show the new ID
```

---

## Step 3: Restart the portal service

The portal container renders `portal.conf.template` via `envsubst` on startup:

```bash
docker compose restart portal
```

---

## Step 4: Verify

```bash
# Should return 200 or a valid n8n response body (not 404)
curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost/api/portal-provision \
    -H "Content-Type: application/json" \
    -d '{"test": true}'
```

- **200 / 4xx from n8n** = proxy is working correctly
- **404** = ID still wrong — repeat from Step 1
- **502** = n8n is not healthy — check `docker compose logs n8n`

---

## Step 5: Commit the change

```bash
git add nginx/conf.d/portal.conf.template
git commit -m "ops: update portal-provision n8n workflow ID to ${NEW_ID}"
```

---

## Rollback

```bash
git checkout nginx/conf.d/portal.conf.template
docker compose restart portal
```
