# Lead Dashboard: Archive, Search & Filter

**Date:** 2026-03-16
**Status:** Approved

---

## Overview

Add archive management, multi-select filter pills, sort, and search to the Sovereign Advisory lead review dashboard. The goal is to let Robert quickly triage a growing lead queue without losing visibility into historical outcomes.

---

## Database

**Schema changes:**

```sql
ALTER TABLE sa_leads ADD COLUMN archived BOOLEAN NOT NULL DEFAULT FALSE;
```

`archived` is a flag independent of `status`. Existing status values (`pending_review`, `sent`, `do_not_follow_up`, etc.) are preserved as-is. Archiving a lead from the review page sets `archived = true` and optionally updates `status` to reflect outcome (see Archive Action below). This keeps status reportable and decoupled from the "out of queue" concept.

`sa_leads.do_not_follow_up` already exists in the schema — no migration needed.

`sa_review_tokens.used_at` already exists in the schema — no migration needed for that column.

---

## Dashboard — Filter Pills

### Filter model

There are two independent filter dimensions:

1. **Status pills** — `Needs Action`, `Sent`, `Declined`, `Queued`. Selecting multiple status pills uses **OR** logic: a lead is included if it matches *any* selected status pill.
2. **Archived pill** — a dimension toggle. When active it **ANDs** with the status selection: results must match the selected statuses *and* be archived. When not active, archived leads are excluded by default.
3. **All** — a reset mode (mutually exclusive with all other pills). Clears all active pills and returns every lead regardless of status or archived state. "All" is not combinable with other pills.

This means:
- "Sent" + "Declined" → leads that are sent OR declined (OR within status group)
- "Sent" + "Archived" → leads that are sent AND archived (AND across dimensions)
- "All" → literally everything

### Pills

| Label | Matches |
|---|---|
| All | Every lead — no filter applied (exclusive mode, clears others) |
| Needs Action | `status IN ('pending_review', 'queued', 'regenerating') AND archived = false` |
| Sent | `status = 'sent'` |
| Declined | `status = 'do_not_follow_up'` |
| Queued | `status = 'queued'` (regardless of archived state — intentionally broader than Needs Action) |
| Archived | `archived = true` — ANDs with any active status pills; excluded from results when inactive |

**Implicit archived filter:** Unless the Archived pill is explicitly active, all status-filtered queries implicitly add `archived = false`. "All" is the only pill that applies no archived filter. This rule covers Sent, Declined, and Queued — none of them will show archived leads unless the Archived pill is also active.

Note: "Needs Action" hard-codes `archived = false` in its own definition for clarity, but this is consistent with the implicit rule above. "Queued" has no archived constraint in its definition because the implicit rule covers it; a queued-but-archived lead appears under Queued only when Archived is also active.

### Persistence

Active pill state and sort preference persist in `localStorage` under key `sa_lead_dashboard_v1` (namespaced to avoid collision). **Default on first visit:** "Needs Action" pill active.

Shape: `{ pills: string[], sort: string }` where `pills` is an array of pill identifiers (`"needs_action"`, `"sent"`, `"declined"`, `"queued"`, `"archived"`, `"all"`). The "All" mode is stored as `pills: ["all"]`. An empty array is not a valid stored state — if the restored value is empty, fall back to `["needs_action"]`.

### Sort

Sort dropdown beside the search input:

| Option | Order |
|---|---|
| Newest (default) | `created_at DESC` |
| Oldest | `created_at ASC` |
| Name A→Z | `last_name ASC, first_name ASC` |
| Name Z→A | `last_name DESC, first_name DESC` |
| Company A→Z | `domain ASC` |
| Sent date (newest first) | `sent_at DESC NULLS LAST` |

Sort preference persists in `localStorage` alongside filter state.

### Search

- Text input that filters client-side across `first_name`, `last_name`, `email`, `domain`, and `service_area` as the user types.
- Applied on top of the server-returned set. Search operates only on leads returned by the active server-side filters — it will not surface leads excluded by the current pill selection.
- No server round-trip needed at current data volumes.

### Checkboxes & bulk archive

- Each row has a checkbox.
- An **"Archive selected"** button appears when one or more checkboxes are checked.
- Bulk archive sets `archived = true` on all selected leads without changing their status. No disposition prompt is shown — this is intentional to keep bulk operations fast. There is no undo (un-archiving is out of scope for this release).

**Bulk archive endpoint:**

```
POST /api/review/bulk-archive
Headers: X-Session-Key: <key>
Body: { "lead_ids": ["uuid1", "uuid2", ...] }
```

Server sets `archived = true` on all provided `lead_ids` that the session has access to (admin session: any lead; per-token session: only the session's own lead). Invalid or already-archived IDs are silently skipped. Returns `{"status": "ok", "archived_count": N}`.

---

## Dashboard — API Changes

`GET /api/review/leads` gains optional query parameters so filtering happens in the DB:

| Param | Type | Description |
|---|---|---|
| `status` | `string[]` | One or more status values — results match ANY (OR logic) |
| `archived` | `boolean` | `true` → only archived; `false` → exclude archived; omitted → no archived filter (returns both) |
| `sort` | `string` | One of: `newest`, `oldest`, `name_asc`, `name_desc`, `company_asc`, `sent_desc` |

The "All" pill omits all params. The "Needs Action" pill passes `status[]=pending_review&status[]=queued&status[]=regenerating&archived=false`.

---

## Review Page — Archive Button

### Placement

The Archive button sits in the existing action bar alongside Approve, Regenerate, Queue, and DNFU.

### Interaction (inline expand)

1. User taps **Archive**.
2. The button expands inline (no modal) to reveal two choices: **Sent** and **Declined**, plus an **✕** cancel.
3. Choosing **Sent** sets `archived = true`, `status = 'sent'`, marks the review token inactive (`is_active = false`, `used_at = NOW()`), redirects to dashboard.
4. Choosing **Declined** sets `archived = true`, `status = 'do_not_follow_up'`, `do_not_follow_up = true`, marks token inactive, redirects to dashboard.
5. **✕** collapses back to the Archive button with no changes.

On mobile (viewport width ≤ 640px) the Sent / Declined choices stack vertically below the Archive button; this is CSS-only using a flex-direction change at that breakpoint.

### API change

`POST /api/review/{token}/action` accepts a new action:

```json
{ "action": "archive", "disposition": "sent" | "declined" }
```

Server sets:
- `archived = true`
- `status = 'sent'` or `'do_not_follow_up'` per disposition
- `do_not_follow_up = true` if declined
- `sa_review_tokens.is_active = false`, `used_at = NOW()`

Returns `{"status": "ok", "action": "archive"}`.

---

## Frontend — Component Summary

| Component | Change |
|---|---|
| `renderDashboard()` | Add filter pills, sort dropdown, search input, checkboxes, bulk archive button |
| `doAction()` | Handle `archive` action with inline expand/collapse state |
| `localStorage` | Persist `{ pills: [...], sort: "newest" }` under `sa_lead_dashboard_v1` |
| Lead row HTML | Add checkbox, apply filtered/sorted/searched rendering |

---

## Out of Scope

- Server-side search (not needed at current data volumes)
- Un-archiving from the dashboard
- Reporting / analytics on archived outcomes
- Bulk archive confirmation prompt (intentionally omitted — bulk is fast path, no undo by design)
