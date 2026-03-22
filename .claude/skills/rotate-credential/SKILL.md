---
name: rotate-credential
description: Use when the user invokes /rotate-credential, asks to rotate a credential, rotate a password, update a secret in Vaultwarden or Keycloak, or propagate a new credential to multiple systems. This skill performs atomic multi-system credential rotation via the vault-sync /update endpoint.
---

# Credential Rotation

An atomic workflow for rotating credentials across Vaultwarden and Keycloak (and optionally the VPS `.env`) in a single operation via the `vault-sync` service. Always confirms propagation to every target system before declaring success.

## Step 1 — Parse the Rotation Request

Extract three pieces of information from the user's natural-language request:

1. **Item name** — the Vaultwarden item name (e.g., `Keycloak SSO`, `SMTP Relay`, `PostgreSQL Admin`)
2. **Username** — optional; include only if explicitly provided
3. **New password** — the literal password string, or `<generate>` if none was given

**Password generation rule:** If the user did not provide a new password, generate a secure 24-character password using only:
- Uppercase: `A-Z` (excluding `I`, `O`)
- Lowercase: `a-z` (excluding `l`)
- Digits: `2-9` (excluding `0`, `1`)
- Symbols: `!@#$%^&*-_=+`
- **Exclude ambiguous characters**: `0`, `O`, `l`, `1`, `I`

Generate using Python on the VPS to avoid local entropy issues:
```bash
ssh root@187.77.208.197 "python3 -c \"
import secrets
alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789!@#\$%^&*-_=+'
print(''.join(secrets.choice(alphabet) for _ in range(24)))
\""
```

Present the generated password to the user and ask for confirmation before proceeding.

## Step 2 — Call vault-sync /update

Construct and execute the rotation call via SSH into the `vault_sync` container:

```bash
ssh root@187.77.208.197 "docker exec vault_sync python3 -c \"
import urllib.request, json
payload = {'name': 'ITEM_NAME', 'password': 'NEW_PASSWORD'}
# Include username only if provided:
# payload['username'] = 'USERNAME'
data = json.dumps(payload).encode()
req = urllib.request.Request(
    'http://localhost:8777/update',
    method='POST',
    headers={'Content-Type': 'application/json'},
    data=data
)
with urllib.request.urlopen(req, timeout=30) as r:
    print(r.status, r.read().decode())
\""
```

**Substitute**: `ITEM_NAME`, `NEW_PASSWORD`, and optionally `USERNAME` from Step 1.

## Step 3 — Interpret the Response

Parse the JSON response and branch on the `keycloak_synced` field:

| Response field | Meaning | Action |
|---|---|---|
| `keycloak_synced: true` | Vaultwarden + Keycloak both updated atomically | Report full success |
| `keycloak_synced: false` + no error | Item not in `KEYCLOAK_SYNC_ITEMS` — only Vaultwarden updated | Report Vaultwarden-only success; note Keycloak not applicable |
| `partial: true` | One system updated, the other failed | **WARN LOUDLY** — partial state; instruct user to check manually |
| HTTP error or exception | Both systems may be in unknown state | Report failure; instruct user to verify manually |

**Partial state warning template:**
```
WARNING: PARTIAL ROTATION — CREDENTIAL STATE IS INCONSISTENT

Vaultwarden update: [succeeded / failed]
Keycloak update:    [succeeded / failed]

ACTION REQUIRED: The credential is in an inconsistent state between systems.
Do NOT use the new password until you have verified and reconciled both systems manually.

vault-sync logs: ssh root@187.77.208.197 "docker logs vault_sync --tail 50"
Keycloak admin:  https://auth.sovereignadvisory.ai (admin console -> Users)
```

## Step 4 — Offer .env Update

After a successful rotation, ask the user:

```
The credential has been rotated in Vaultwarden[/Keycloak].

Does this credential also live in the VPS .env file?
If yes, provide the environment variable name (e.g. KEYCLOAK_ADMIN_PASS)
and I will update /root/vibe_coding/Agentic_SDLC/.env on the server.
```

If the user confirms with a variable name, perform the update:

```bash
ssh root@187.77.208.197 "sed -i 's|^ENV_VAR_NAME=.*|ENV_VAR_NAME=NEW_PASSWORD|' /root/vibe_coding/Agentic_SDLC/.env"
```

Then verify the change was applied:
```bash
ssh root@187.77.208.197 "grep '^ENV_VAR_NAME=' /root/vibe_coding/Agentic_SDLC/.env"
```

After updating `.env`, remind the user which containers need restart to pick up the new value:
- If the variable is consumed by `vault_sync`: `docker compose restart vault_sync`
- If consumed by `n8n`, `litellm`, or others: `docker compose restart <service>`

## Step 5 — Output Audit Trail

Always end with a structured audit summary, regardless of success or failure:

```
=== CREDENTIAL ROTATION AUDIT ===
Timestamp:        <ISO-8601 timestamp>
Item name:        Keycloak SSO
Username:         relder@sovereignadvisory.ai
Password changed: yes (24-char generated) / yes (user-supplied)

Systems updated:
  Vaultwarden:    updated
  Keycloak:       synced (keycloak_synced=true)
  VPS .env:       updated (KEYCLOAK_ADMIN_PASS)  /  not requested

vault-sync response: {"status": "ok", "keycloak_synced": true, ...}
=================================
```

## Implementation Checklist

- [ ] Identify item name and target systems
- [ ] Generate or accept new password (confirm with user if generated)
- [ ] Call vault-sync /update via SSH (atomic Vaultwarden + Keycloak if applicable)
- [ ] Confirm both sides updated — check `keycloak_synced` field
- [ ] Warn loudly if partial update detected
- [ ] Update VPS .env if credential is an env var (ask user for var name)
- [ ] Restart affected containers if .env was updated
- [ ] Report full audit trail: what was rotated, which systems, timestamp

## Quick Reference — vault-sync Endpoint

- **URL (from VPS):** `http://localhost:8777/update`
- **Container:** `vault_sync` on network `vibe_net`
- **Method:** `POST`
- **Payload:** `{"name": "...", "password": "...", "username": "..."(optional)}`
- **Keycloak sync:** Triggered automatically when item name is in `KEYCLOAK_SYNC_ITEMS` env var
- **Logs:** `ssh root@187.77.208.197 "docker logs vault_sync --tail 50"`

## Important Notes

- Never rotate credentials by directly editing `.env` — use this skill so all systems stay in sync
- Never run `sqlite3` writes against the Vaultwarden data volume — use vault-sync `/update` instead (direct DB writes cause PBKDF2/hash format mismatches)
- The VPS SSH target is always `root@187.77.208.197`
- The vault-sync container is always `vault_sync`; the internal port is always `8777`
