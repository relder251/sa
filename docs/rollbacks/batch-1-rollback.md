# Batch-1 Rollback Guide

Rollback procedures for tasks T-13, T-14, T-15, T-19, T-06, and T-07.

## Task T-13: Add Keycloak hostname config to LiteLLM

**Commit:** `f535c4a`

**What Changed:**
- Added `KC_HOSTNAME` and `KC_HOSTNAME_ADMIN` environment variables to the LiteLLM service in `docker-compose.yml`
- These variables configure Keycloak hostname settings for integration with the LiteLLM authentication layer

**Rollback Command:**
```bash
git revert f535c4a --no-edit
```

**Verification Steps:**
```bash
# Verify the revert was applied
git log --oneline -3

# Check that KC_HOSTNAME and KC_HOSTNAME_ADMIN are removed from docker-compose.yml
grep -n "KC_HOSTNAME" docker-compose.yml || echo "Keycloak hostname vars removed"

# Restart LiteLLM service to apply changes
docker compose restart litellm

# Verify service is healthy
docker compose logs -f litellm | head -20
```

---

## Task T-14: Add PKCE support to oauth2-proxy services

**Commit:** `c8f6a47`

**What Changed:**
- Added PKCE S256 (Proof Key for Code Exchange) configuration to all oauth2-proxy service instances
- Implemented PKCE_METHOD=S256 and related security parameters across multiple oauth2-proxy containers
- Enhanced OAuth2 security posture by enforcing PKCE on all protected endpoints

**Rollback Command:**
```bash
git revert c8f6a47 --no-edit
```

**Verification Steps:**
```bash
# Verify the revert was applied
git log --oneline -3

# Check that PKCE configuration is removed from oauth2-proxy services
grep -r "PKCE_METHOD" docker-compose.yml || echo "PKCE config removed"

# Restart oauth2-proxy services
docker compose restart oauth2-proxy

# Verify services are healthy
docker compose ps | grep oauth2-proxy
```

---

## Task T-15: Remove pipeline subdomain from nginx

**Commit:** `44ff980`

**What Changed:**
- Removed `pipeline.example.com` subdomain from nginx reverse proxy configuration
- Removed pipeline-related proxy_pass directives from `nginx/conf.d/portal.conf.template`
- Simplified nginx configuration by eliminating the pipeline service endpoint

**Rollback Command:**
```bash
git revert 44ff980 --no-edit
```

**Verification Steps:**
```bash
# Verify the revert was applied
git log --oneline -3

# Check that pipeline subdomain is restored in nginx template
grep -n "pipeline" nginx/conf.d/portal.conf.template || echo "Pipeline config removed"

# Rebuild and restart nginx with the restored configuration
docker compose restart nginx

# Verify nginx is healthy
docker compose logs nginx | tail -20
```

---

## Task T-19: Add Twingate resource creation script

**Commit:** `bc9ec47`

**What Changed:**
- Added `twingate_add_resource.sh` script for programmatic Twingate resource creation
- Script enables automation of Twingate resource management without manual dashboard interaction
- Includes support for alias requirements and resourceCreate API address handling

**Rollback Command:**
```bash
git revert bc9ec47 --no-edit
```

**Verification Steps:**
```bash
# Verify the revert was applied
git log --oneline -3

# Confirm twingate_add_resource.sh is removed
ls -la twingate_add_resource.sh 2>&1 | grep -q "cannot access" && echo "Script removed successfully"

# Verify no other Twingate automation files are affected
find . -name "*twingate*" -type f | grep -v ".git"
```

---

## Task T-06: Fix resourceCreate address argument

**Commit:** `f535c4a`

**What Changed:**
- Corrected the resourceCreate address argument in the Twingate resource creation logic
- Changed from passing an object to passing a plain string for the address parameter
- Added note about alias requirement for proper Twingate resource naming
- Fixed argument passing in the resource creation API call

**Rollback Command:**
```bash
git revert f535c4a --no-edit
```

**Verification Steps:**
```bash
# Verify the revert was applied
git log --oneline -3

# Check the resourceCreate function signature in relevant scripts
grep -n "resourceCreate" . -r --include="*.sh" --include="*.py" | head -10

# Review the change in context
git diff HEAD~1 HEAD | grep -A5 -B5 "resourceCreate"
```

---

## Task T-07: Remove Twingate pipeline subdomain

**Commit:** `44ff980`

**What Changed:**
- Removed Twingate-related subdomain configuration from nginx proxy setup
- Removed proxy_pass directive for Twingate pipeline endpoint
- Simplified infrastructure by eliminating separate Twingate pipeline routing

**Rollback Command:**
```bash
git revert 44ff980 --no-edit
```

**Verification Steps:**
```bash
# Verify the revert was applied
git log --oneline -3

# Check that Twingate pipeline configuration is restored
grep -n "twingate" nginx/conf.d/portal.conf.template || echo "Twingate config removed"

# Rebuild nginx configuration
docker compose restart nginx

# Check nginx logs for any errors
docker compose logs nginx | grep -i "error" || echo "No nginx errors found"
```

---

## Rollback All Tasks at Once

If you need to rollback all tasks in this batch simultaneously:

```bash
git revert --no-edit f535c4a c8f6a47 44ff980 bc9ec47
```

Then restart all affected services:

```bash
docker compose restart litellm oauth2-proxy nginx
```

And verify the full stack is healthy:

```bash
docker compose ps
docker compose logs --tail=50 litellm oauth2-proxy nginx
```

---

## Cross-References

- **n8n Workflow ID Rotation:** See `/docs/runbooks/n8n-workflow-id-rotation.md` for related infrastructure changes
- **Nginx Configuration:** `/nginx/conf.d/portal.conf.template`
- **Docker Compose:** `/docker-compose.yml`
- **Keycloak Integration:** LiteLLM service authentication configuration
- **OAuth2-Proxy:** Multi-service reverse proxy authentication layer
- **Twingate Resources:** Programmatic resource management scripts

