# Refactor: ofelia.ini

**Date:** 2026-03-20
**Refactored by:** Claude Sonnet 4.6

---

## File Overview

| Property | Value |
|---|---|
| **Path** | `ofelia.ini` |
| **Purpose** | Cron schedule for the Agentic SDLC stack |
| **Role** | Defines two scheduled jobs executed inside Docker containers via `docker exec` |
| **Mounted by** | `ofelia` service in `docker-compose.yml` at `/etc/ofelia/config.ini:ro` |
| **Upstream deps** | `free_model_sync` container, `backup` container |
| **Downstream deps** | None (ofelia is a leaf scheduler — nothing depends on it) |

### Jobs defined
| Job | Schedule | Container | Command |
|---|---|---|---|
| `free-model-sync` | Every 6 hours | `free_model_sync` | `python /app/free_model_sync.py` |
| `daily-backup` | Daily at 02:00:00 | `backup` | `bash /backup.sh` |

---

## Gaps Found

| # | Gap | Severity | Description |
|---|---|---|---|
| 1 | Missing `no-overlap` on both jobs | **Medium** | Without `no-overlap = true`, Ofelia will start a new job instance even if the previous run is still executing. For the backup job this could cause concurrent writes to the same output files. For free-model-sync, duplicate API calls and conflicting LiteLLM DB writes. |
| 2 | No timeout on backup job | **Medium** | A hung or slow backup has no kill switch. Without a timeout the job can run indefinitely, blocking disk I/O and leaving stale lock files. **Ofelia does not support a `timeout` ini key** (confirmed by binary inspection — no `gcfg:"timeout"` field in mcuadros/ofelia:latest). Runaway backup protection must be handled inside `backup.sh` itself (e.g. `timeout 30m bash /backup.sh`). This gap is **deferred** to the `backup.sh` refactor pass. |
| 3 | `ofelia` `depends_on` missing `backup` | **Low** | In `docker-compose.yml`, ofelia's `depends_on` only listed `free-model-sync`. Since ofelia also exec's into the `backup` container, missing this dependency means ofelia could start before `backup` is ready. Fixed in `docker-compose.yml` as part of this refactor pass. |
| 4 | Comment inconsistency | **Low** | The 6-field cron format note was only on the `daily-backup` job, not at the file header where it is more useful. |

---

## Changes Made

### ofelia.ini

| Change | Before | After | Reason |
|---|---|---|---|
| Added `no-overlap = true` to `free-model-sync` | *(absent)* | `no-overlap = true` | Prevents concurrent model sync runs |
| Added `no-overlap = true` to `daily-backup` | *(absent)* | `no-overlap = true` | Prevents concurrent backup runs and file corruption |
| ~~Added `timeout = 30m` to `daily-backup`~~ | *(attempted)* | **Removed** — `timeout` is not a supported Ofelia ini key; silently ignored | Confirmed unsupported via `strings /usr/bin/ofelia \| grep gcfg` — no `gcfg:"timeout"` entry exists in the binary. Protection deferred to `backup.sh`. |
| Moved 6-field cron comment to file header | Inline on daily-backup only | File-level header comment | More useful as global context |
| Aligned key spacing | Inconsistent | Consistent column alignment | Readability |

### docker-compose.yml (collateral fix)

| Change | Before | After | Reason |
|---|---|---|---|
| `ofelia.depends_on` | `- free-model-sync` only | Added `- backup` | Ofelia exec's into both containers; backup must be ready |

---

## Test Results

### Syntax validation
| Check | Result |
|---|---|
| `docker compose config --quiet` | ✅ VALID |
| `configparser.read('ofelia.ini')` | ✅ VALID — 2 sections parsed correctly |

### Functional validation — supported key audit
Binary string inspection of `mcuadros/ofelia:latest` to confirm which ini keys are actually parsed:

```
docker exec ofelia strings /usr/bin/ofelia | grep gcfg
```

| Key | Binary entry found | Supported |
|---|---|---|
| `no-overlap` | `NoOverlap+gcfg:"no-overlap" mapstructure:"no-overlap"` | ✅ Yes |
| `timeout` | *(not found)* | ❌ No — silently ignored |

### Functional validation — live reload
| Step | Command | Result |
|---|---|---|
| Config was stale | Container started 2026-03-18, before ini changes | Old config was active |
| Restart ofelia | `docker compose restart ofelia` | ✅ Restarted |
| Validate config | `docker exec ofelia /usr/bin/ofelia validate --config=/etc/ofelia/config.ini` | ✅ `OK, Found 2 jobs` |
| Jobs registered in logs | `docker compose logs ofelia` | ✅ Both `free-model-sync` and `daily-backup` loaded |

### Upstream dependency check
| Container | Status |
|---|---|
| `free_model_sync` | ✅ Running (confirmed via `docker ps`) |
| `backup` | ✅ Running (confirmed via `docker ps`) |

### Downstream dependency check
| Check | Result |
|---|---|
| Nothing depends on ofelia | ✅ Confirmed — ofelia is a leaf service |

---

## Final State

`ofelia.ini` is now hardened against concurrent job execution via `no-overlap = true` on both jobs. The `docker-compose.yml` `depends_on` gap was corrected as a collateral fix. No functional behavior changed — schedules and commands are identical to the original.

**Deferred:** Runaway backup timeout protection cannot be implemented in `ofelia.ini` (Ofelia does not support a `timeout` ini key). This must be addressed in `backup.sh` using shell-level timeout (e.g. `exec timeout 30m bash /backup.sh`). Tracked as a gap for the `backup.sh` refactor pass.
