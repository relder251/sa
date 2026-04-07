# Postman Collection: Agentic SDLC — n8n Webhook API

## Collection

**Name:** Agentic SDLC — n8n Webhook API
**Postman Collection ID:** `437441d1-6c3d-4a99-a963-217be9debe7c`
**Postman UID:** `53286998-437441d1-6c3d-4a99-a963-217be9debe7c`
**Workspace:** Robert Elder's Team Workspace (`5920f58c-4be7-4370-8b0b-1acc2440d5b7`)

View in Postman: https://www.postman.com/robert-elder251/workspace/robert-elder-s-workspace/collection/53286998-437441d1-6c3d-4a99-a963-217be9debe7c

## Base URL Variable

The collection uses `{{base_url}}` set to `https://home.private.sovereignadvisory.ai`.

## Folders and Requests

### Portal Provisioning

All backed by n8n webhooks proxied through nginx on the portal host.

| Request | Method | Nginx Route | n8n Webhook Path | ID-embedded? | Timeout |
|---------|--------|------------|-----------------|-------------|---------|
| Get Portal Services | GET | `/api/portal-services` | `VHUS5Dx1q9HBZPln/webhook/portal-services` | Yes | 10s |
| Provision New Service | POST | `/api/portal-provision` | `DXjMzKwRB6c54GCY/webhook/portal-provision` | Yes | 120s |
| Update Service | POST | `/api/portal-update` | `portal-update` | No | 30s |
| Delete Service | POST | `/api/portal-delete` | `portal-delete` | No | 30s |
| Update Categories | POST | `/api/portal-update-categories` | `portal-update-categories` | No | 30s |
| Track Recent Service | POST | `/api/portal-track-recent` | `GOpCkjqJyPjy5dgG/webhook/portal-track-recent` | Yes | 10s |

**Note on ID-embedded paths:** Three webhooks embed the n8n workflow ID in the nginx `proxy_pass` URL. If these workflows are deleted and recreated in n8n, the ID changes and nginx must be updated. See `docs/runbooks/n8n-workflow-id-rotation.md`.

### Vault Sync API

Internal service running at `http://localhost:8777` on the VPS. Not exposed through nginx — access via SSH only.

| Request | Method | URL | Purpose |
|---------|--------|-----|---------|
| Health Check | GET | `http://localhost:8777/health` | Confirm vault_sync container is healthy |
| Sync Vault Items | POST | `http://localhost:8777/sync` | Refresh vault-sync's Vaultwarden item cache |
| Update Credential | POST | `http://localhost:8777/update` | Atomically update Vaultwarden + Keycloak |
| Update Keycloak User | POST | `http://localhost:8777/update-keycloak` | Keycloak-only password update (recovery) |

### Credential Rotation

| Request | Method | Nginx Route | n8n Webhook Path | Timeout |
|---------|--------|------------|-----------------|---------|
| Rotate Credential | POST | `/api/rotate-credential` | `rotate-credential` | 60s |

## Related Resources

- Skill: `.claude/skills/rotate-credential/SKILL.md` — guided atomic rotation workflow
- Skill: `.claude/skills/n8n-workflow/SKILL.md` — webhook routing validation
- Runbook: `docs/runbooks/n8n-workflow-id-rotation.md` — fix broken ID-embedded webhook paths
- nginx config: `nginx/conf.d/portal.conf.template` — all nginx-to-n8n proxy routes
