---
name: block-credential-env-edits
enabled: true
event: file
action: block
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.env(\.|$)
  - field: new_text
    operator: regex_match
    pattern: BW_CLIENT|BW_MASTER|KEYCLOAK_ADMIN_PASS|PORTAL_OIDC|OAUTH2_PROXY_COOKIE_SECRET
---

BLOCKED: Direct .env edit for a protected credential variable.

Editing credential variables directly in .env bypasses the atomic multi-system rotation workflow and leaves Vaultwarden and Keycloak out of sync.

Use the /rotate-credential skill instead:

  /rotate-credential "<Item Name> to <new-password>"

The skill will:
1. Update the Vaultwarden item via vault-sync /update
2. Atomically sync Keycloak if the item is in KEYCLOAK_SYNC_ITEMS
3. Offer to update the .env variable after both upstream systems confirm success
4. Produce a full audit trail

Protected variables: BW_CLIENT*, BW_MASTER*, KEYCLOAK_ADMIN_PASS*, PORTAL_OIDC*, OAUTH2_PROXY_COOKIE_SECRET*

If you genuinely need to bypass this guard (e.g., bootstrapping a fresh environment), disable this rule temporarily:
  set enabled: false in .claude/hookify.block-credential-env-edits.local.md
