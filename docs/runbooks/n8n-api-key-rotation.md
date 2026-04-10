# Runbook: n8n API Key Rotation

**When to use:**
- After a suspected key compromise (e.g., key found in git history)
- Scheduled rotation as part of secrets hygiene
- After n8n version upgrades that may invalidate existing keys

---

## Background

n8n API keys are JWTs (HS256) signed with a derived secret. There is **no programmatic REST endpoint** for creating or rotating API keys — the UI (Settings > n8n API) is the only official method. However, keys can be rotated transparently via JWT generation + direct database operations. No service restart is required.

---

## JWT Signing Key Derivation

n8n derives its JWT signing secret from `N8N_ENCRYPTION_KEY` using a custom algorithm:

1. Take **every other character** from `N8N_ENCRYPTION_KEY` (indices 0, 2, 4, ...)
2. SHA-256 hash the result
3. Use the hex digest as the HMAC-SHA256 signing key

```python
import hashlib
enc_key = "YOUR_N8N_ENCRYPTION_KEY"  # from: docker inspect n8n
base_key = "".join(enc_key[i] for i in range(0, len(enc_key), 2))
jwt_secret = hashlib.sha256(base_key.encode()).hexdigest()
```

Source: `n8n/dist/services/jwt.service.js` inside the n8n container.

---

## JWT Payload Structure

```json
{
  "sub": "user-UUID-from-n8n-user-table",
  "iss": "n8n",
  "aud": "public-api",
  "jti": "new-uuid4",
  "iat": 1775786643,
  "exp": 1807322643
}
```

---

## Database: user_api_keys

| Column    | Type    | Notes |
|-----------|---------|-------|
| id        | varchar | Unique row ID (e.g., "VaultRotated20260410") |
| userId    | uuid    | Must match the n8n user UUID |
| label     | varchar | Human-readable label |
| apiKey    | varchar | The full JWT string |
| createdAt | timestamptz | |
| updatedAt | timestamptz | |
| scopes    | json    | **Must contain full scope array** — empty [] means auth works but no access |
| audience  | varchar | Must be "public-api" |

**Critical:** The scopes array must contain the required permissions. Copy from an existing key row. An empty scopes array results in 0 results from all endpoints despite valid authentication.

Database: `n8n` database in `litellm_db` container, user `litellm`.

---

## Step-by-Step Rotation Procedure

### 1. Get the encryption key
```bash
docker inspect n8n --format '{{range .Config.Env}}{{println .}}{{end}}' | grep N8N_ENCRYPTION_KEY
```

### 2. Generate new JWT (Python)
```python
import hmac, hashlib, base64, json, uuid, time

enc_key = "value-from-step-1"
base_key = "".join(enc_key[i] for i in range(0, len(enc_key), 2))
jwt_secret = hashlib.sha256(base_key.encode()).hexdigest()

user_id = "user-UUID-from-SELECT-id-FROM-user-in-n8n-DB"

def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

header = {"alg": "HS256", "typ": "JWT"}
now = int(time.time())
payload = {
    "sub": user_id, "iss": "n8n", "aud": "public-api",
    "jti": str(uuid.uuid4()), "iat": now,
    "exp": now + (365 * 24 * 3600)
}

h = b64url(json.dumps(header, separators=(",", ":")).encode())
p = b64url(json.dumps(payload, separators=(",", ":")).encode())
sig = hmac.new(jwt_secret.encode(), (h + "." + p).encode(), hashlib.sha256).digest()
print(h + "." + p + "." + b64url(sig))
```

### 3. Insert new key into database
```sql
INSERT INTO user_api_keys (id, "userId", label, "apiKey", "createdAt", "updatedAt", scopes, audience)
SELECT 'NewKeyId', 'user-uuid', 'vault-managed-rotated', 'new-JWT-here',
       NOW(), NOW(), scopes, 'public-api'
FROM user_api_keys WHERE id = 'existing-key-id';
```

### 4. Test new key
```bash
N8N_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' n8n)
curl -s "http://${N8N_IP}:5678/api/v1/workflows?limit=1" -H "X-N8N-API-KEY: new-token"
```
Expected: HTTP 200 with workflow data. If 401: JWT signature is wrong. If 200 but empty results: scopes are missing.

### 5. Delete old keys
```sql
DELETE FROM user_api_keys WHERE id IN ('old-key-1', 'old-key-2');
```

### 6. Push to Vault and regenerate .env.prod
```bash
source /root/.vault-keys
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN=$VAULT_ROOT_TOKEN \
  vault vault kv patch secret/sdlc/prod "N8N_API_KEY=new-token"
cd /opt/agentic-sdlc && bash scripts/vault-env.sh
```

### 7. Verify end-to-end
```bash
source /opt/agentic-sdlc/.env.prod
N8N_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' n8n)
curl -s "http://${N8N_IP}:5678/api/v1/workflows?limit=1" -H "X-N8N-API-KEY: $N8N_API_KEY"
```

---

## Rollback

If the new key fails, re-insert the old JWT into user_api_keys with the same scopes. Old JWTs remain valid as long as they exist in the database and have not expired.

---

## Notes

- n8n validates JWTs at request time — no restart needed after DB changes
- The signing algorithm was found in n8n 2.15.0; verify if upgrading n8n
- All old keys should be deleted after rotation to prevent use of compromised tokens
