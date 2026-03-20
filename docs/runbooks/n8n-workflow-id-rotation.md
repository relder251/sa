# n8n Workflow ID Rotation Runbook

## Overview

This runbook guides operators through rotating n8n workflow IDs when webhooks need to be regenerated or updated. Workflow IDs are embedded in the reverse proxy configuration and must be synchronized with the actual workflow IDs in n8n.

## When to Rotate Workflow IDs

### Symptoms and Triggers

- **Webhook authentication failure**: n8n webhooks returning 404 or 401 errors despite services being healthy
- **Workflow ID mismatch**: nginx logs show requests to endpoints that don't exist in n8n
- **After n8n backup/restore**: Workflow IDs may change when restoring from backups
- **Security incident**: Webhook URLs have been exposed or compromised
- **Workflow recreation**: Deleting and recreating workflows generates new IDs
- **n8n database migration**: Moving n8n to a new database instance
- **Regular rotation policy**: Scheduled security rotation of webhook credentials

## Prerequisites

- Docker and docker-compose installed and operational
- n8n container running and healthy
- Access to nginx configuration files
- Git repository with write permissions
- Ability to restart containers

## Step 1: Identify Current Workflow IDs

### Method 1: Using n8n CLI (Recommended)

List all active workflows and their IDs:

```bash
docker exec n8n n8n list:workflow
```

This outputs a table with columns: ID, Name, Active, CreatedAt, UpdatedAt. Note the ID values for the four portal workflows:
- portal-provision
- portal-update-categories
- portal-update
- portal-delete

### Method 2: Viewing nginx Configuration

Current workflow IDs are documented in the reverse proxy configuration. View them:

```bash
cat nginx/conf.d/portal.conf.template
```

Look for the four `location` blocks with their corresponding `proxy_pass` directives. Each contains an embedded workflow ID in the URL path format `/webhook/{WORKFLOW_ID}/webhook/{PATH}`.

## Step 2: Document Current Workflow IDs

Create a temporary record of current mappings before rotation:

```bash
# Save current mappings
cat > /tmp/workflow_mapping_backup.txt << 'EOF'
portal-provision: [CURRENT_ID_1]
portal-update-categories: [CURRENT_ID_2]
portal-update: [CURRENT_ID_3]
portal-delete: [CURRENT_ID_4]
EOF
```

Replace placeholders with actual IDs from Step 1.

## Step 3: Update nginx Configuration

The file `nginx/conf.d/portal.conf.template` contains four webhook location blocks. Update each with the new workflow IDs:

### Portal Provision Webhook

**Location**: `nginx/conf.d/portal.conf.template` - `/api/portal-provision` block

**Current pattern**:
```nginx
location /api/portal-provision {
    proxy_pass http://n8n:5678/webhook/FrPgMVhkBNi9nE85/webhook/portal-provision;
    # ... other settings
}
```

**Update to**:
```nginx
location /api/portal-provision {
    proxy_pass http://n8n:5678/webhook/{NEW_PORTAL_PROVISION_ID}/webhook/portal-provision;
    # ... other settings unchanged
}
```

### Portal Update Categories Webhook

**Location**: `nginx/conf.d/portal.conf.template` - `/api/portal-update-categories` block

**Current pattern**:
```nginx
location /api/portal-update-categories {
    proxy_pass http://n8n:5678/webhook/wgvZCgnHnlIkSEN5/webhook/portal-update-categories;
    # ... other settings
}
```

**Update to**:
```nginx
location /api/portal-update-categories {
    proxy_pass http://n8n:5678/webhook/{NEW_PORTAL_UPDATE_CATEGORIES_ID}/webhook/portal-update-categories;
    # ... other settings unchanged
}
```

### Portal Update Webhook

**Location**: `nginx/conf.d/portal.conf.template` - `/api/portal-update` block

**Current pattern**:
```nginx
location /api/portal-update {
    proxy_pass http://n8n:5678/webhook/HJu73t0odAN7bW2b/webhook/portal-update;
    # ... other settings
}
```

**Update to**:
```nginx
location /api/portal-update {
    proxy_pass http://n8n:5678/webhook/{NEW_PORTAL_UPDATE_ID}/webhook/portal-update;
    # ... other settings unchanged
}
```

### Portal Delete Webhook

**Location**: `nginx/conf.d/portal.conf.template` - `/api/portal-delete` block

**Current pattern**:
```nginx
location /api/portal-delete {
    proxy_pass http://n8n:5678/webhook/tUHxTclRAAU8xCih/webhook/portal-delete;
    # ... other settings
}
```

**Update to**:
```nginx
location /api/portal-delete {
    proxy_pass http://n8n:5678/webhook/{NEW_PORTAL_DELETE_ID}/webhook/portal-delete;
    # ... other settings unchanged
}
```

## Step 4: Validate Configuration Syntax

Before restarting containers, validate the nginx configuration for syntax errors:

```bash
docker exec nginx nginx -t
```

Expected output:
```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

If errors appear, review the changes in Step 3 and correct them before proceeding.

## Step 5: Restart Containers

Restart both nginx and n8n to apply the new configuration:

```bash
# Restart nginx to apply new proxy configuration
docker compose restart nginx

# Restart n8n to ensure it picks up any configuration changes
docker compose restart n8n
```

Monitor restart progress:

```bash
docker compose logs -f nginx n8n
```

Wait for both containers to show healthy status:
```bash
docker compose ps
```

All containers should show `healthy` or `running` status.

## Step 6: Verification Steps

### 6a: Confirm nginx Configuration Loaded

Check that nginx is serving requests with the new configuration:

```bash
docker compose logs nginx | grep "proxy_pass" | tail -4
```

### 6b: Test Webhook Endpoints

Test each of the four webhook endpoints to confirm they're reaching n8n:

```bash
# Test portal-provision (should return n8n response or 405 Method Not Allowed, NOT 404)
curl -v http://localhost/api/portal-provision

# Test portal-update-categories
curl -v http://localhost/api/portal-update-categories

# Test portal-update
curl -v http://localhost/api/portal-update

# Test portal-delete
curl -v http://localhost/api/portal-delete
```

**Expected responses**:
- Status codes: 200, 405 (Method Not Allowed), or other 4xx/5xx from n8n are acceptable
- Status 404 from nginx indicates workflow ID mismatch — return to Step 3
- Status 502 (Bad Gateway) indicates n8n is not healthy — check Step 5

### 6c: Verify n8n Workflows are Active

Confirm all four workflows are enabled in n8n:

```bash
docker exec n8n n8n list:workflow | grep -E "(portal-provision|portal-update-categories|portal-update|portal-delete)"
```

All four workflows should appear with `Active = true` status.

### 6d: Monitor Error Logs

Check for any errors in the past 5 minutes:

```bash
docker compose logs --since 5m nginx n8n | grep -i "error\|fail\|warning"
```

No errors related to proxy_pass or workflow ID mismatches should appear.

## Step 7: Document Changes

Update your infrastructure documentation with the new workflow IDs:

```bash
# Replace old IDs with new ones in your records
# Example: Update a workflow_ids.txt file or similar tracking mechanism
```

Commit the changes to git:

```bash
git add nginx/conf.d/portal.conf.template
git commit -m "ops: rotate n8n workflow IDs for portal webhooks"
git push
```

## Rollback Procedure

If webhook failures occur after rotation:

### Immediate Rollback (Within a Few Minutes)

If you still have the backup from Step 2, you can quickly restore:

```bash
# Using git to revert changes
git diff nginx/conf.d/portal.conf.template  # Review changes
git checkout nginx/conf.d/portal.conf.template  # Revert to previous version

# Restart nginx
docker compose restart nginx

# Verify using Step 6b
curl -v http://localhost/api/portal-provision
```

### Full Rollback from Git History

If changes are committed:

```bash
# View recent commits
git log --oneline -5

# Revert to previous working state (use actual commit hash)
git revert {COMMIT_HASH}

# Restart nginx
docker compose restart nginx

# Verify using Step 6b
curl -v http://localhost/api/portal-provision
```

### Fallback: Manual Restoration

If you have the old workflow IDs recorded:

```bash
# Edit the file manually using the old IDs
nano nginx/conf.d/portal.conf.template

# Or use sed to replace (example)
sed -i 's/{NEW_ID}/{OLD_ID}/g' nginx/conf.d/portal.conf.template

# Restart
docker compose restart nginx

# Verify
curl -v http://localhost/api/portal-provision
```

## Troubleshooting

| Symptom | Cause | Resolution |
|---------|-------|-----------|
| 404 errors on webhook calls | Workflow ID in nginx doesn't match n8n | Return to Step 1 and verify actual IDs, update Step 3 |
| 502 Bad Gateway | n8n not responding | Check `docker compose ps` and `docker compose logs n8n` |
| Nginx syntax errors | Typo in workflow ID or configuration | Review Step 3 line-by-line, run Step 4 validation again |
| Workflows show inactive in n8n | Workflows were disabled during testing | Re-enable workflows in n8n UI or use CLI |
| Connection refused | nginx or n8n containers not running | Run `docker compose up -d` to start them |

## Quick Reference: Workflow ID Locations

All four workflow IDs appear in a single file:

**File**: `nginx/conf.d/portal.conf.template`

**Locations within file**:
- Line with `proxy_pass` under `location /api/portal-provision` - ID 1
- Line with `proxy_pass` under `location /api/portal-update-categories` - ID 2
- Line with `proxy_pass` under `location /api/portal-update` - ID 3
- Line with `proxy_pass` under `location /api/portal-delete` - ID 4

**Command to extract all four**:
```bash
grep -A1 "location /api/portal-" nginx/conf.d/portal.conf.template | grep proxy_pass
```

## Related Documentation

- [n8n Webhook Documentation](https://docs.n8n.io/hosting/configuration/webhook/)
- [nginx Reverse Proxy Configuration](nginx-reverse-proxy-config.md)
- [Docker Compose Reference](../docker-compose-operations.md)
- [Agentic SDLC Architecture](../../CLAUDE.md)

## Support

If issues persist after following this runbook:

1. Check container health: `docker compose ps`
2. Review logs: `docker compose logs -f nginx n8n`
3. Verify workflow IDs with: `docker exec n8n n8n list:workflow`
4. Compare IDs in nginx configuration with n8n output
5. Execute rollback if necessary (see Step 8)
