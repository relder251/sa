# Lead Archive, Search & Filter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `archived` flag, filter pills, sort/search, bulk archive from the dashboard, and an inline Archive button on the review page.

**Architecture:** Single DB column addition (`archived boolean`), two backend changes (updated `list_leads` query + new endpoints), and a frontend rewrite of the dashboard section inside the existing single-page HTML. No new files — all changes go into `lead_review_server.py` and `lead_review.html`.

**Tech Stack:** Python / FastAPI / asyncpg (backend), vanilla JS / HTML (frontend), PostgreSQL (DB), Docker on VPS at `sovereignadvisory.ai`.

---

## Deployment pattern

There is no local test environment. All changes are deployed by:
1. Edit the file locally at `scripts/lead_review_server.py` or `scripts/templates/lead_review.html`
2. `scp` the file to the VPS and `docker cp` into the running container
3. `docker restart sa_lead_review`
4. Manual smoke-test via browser or `curl`

The deploy commands for each task are included. Run them exactly — do **not** recreate or remove the container (see prior incident where `docker rm` broke networking).

---

## Chunk 1: Database & Backend

---

### Task 1: Add `archived` column to `sa_leads`

**Files:**
- No file change — run SQL directly against the live DB

- [ ] **Step 1: Add the column**

```bash
ssh root@sovereignadvisory.ai 'docker exec sa_lead_review python3 -c "
import asyncio, asyncpg, os
async def f():
    p = await asyncpg.create_pool(os.environ[\"DATABASE_URL\"])
    await p.execute(\"ALTER TABLE sa_leads ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE\")
    r = await p.fetchrow(\"SELECT column_name FROM information_schema.columns WHERE table_name=\047sa_leads\047 AND column_name=\047archived\047\")
    print(\"Column exists:\", r is not None)
asyncio.run(f())
"'
```

Expected output: `Column exists: True`

- [ ] **Step 2: Verify with a quick count**

```bash
ssh root@sovereignadvisory.ai 'docker exec sa_lead_review python3 -c "
import asyncio, asyncpg, os
async def f():
    p = await asyncpg.create_pool(os.environ[\"DATABASE_URL\"])
    r = await p.fetchrow(\"SELECT count(*) FROM sa_leads WHERE archived = true\")
    print(\"Archived leads:\", r[0])
asyncio.run(f())
"'
```

Expected output: `Archived leads: 0`

---

### Task 2: Update `list_leads` to accept filter + sort query params

**Files:**
- Modify: `scripts/lead_review_server.py` — `list_leads` function (lines ~321–378)

The current endpoint fetches all leads and returns them. Replace the single static query with a dynamic one that respects `status[]`, `archived`, and `sort` query params.

- [ ] **Step 1: Replace the `list_leads` function**

Find and replace the entire `list_leads` function in `lead_review_server.py`:

```python
@app.get("/api/review/leads")
async def list_leads(
    request: Request,
    x_session_key: str = Header(None),
    sort: str = "newest",
):
    """Return leads for the dashboard, filtered and sorted server-side.

    Query params:
      status   (repeatable) — filter by one or more status values (OR logic)
      archived (bool)       — true=only archived, false=exclude archived, omitted=no filter
      sort                  — newest|oldest|name_asc|name_desc|company_asc|sent_desc
    """
    if not x_session_key:
        raise HTTPException(status_code=401, detail="Missing X-Session-Key header")
    _purge_expired_sessions()
    sess = _sessions.get(x_session_key)
    if not sess or sess["expires_at"] < time.time():
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    # Parse repeated `status` params from raw query string
    from urllib.parse import parse_qs
    qs = parse_qs(str(request.url.query))
    status_filter = qs.get("status", [])   # list of status strings, empty = no filter
    archived_raw  = qs.get("archived", [None])[0]
    if archived_raw == "true":
        archived_filter = True
    elif archived_raw == "false":
        archived_filter = False
    else:
        archived_filter = None  # omitted — no archived constraint

    # Build WHERE clauses
    conditions = []
    params     = []

    if sess["lead_id"] != "*":
        params.append(sess["lead_id"])
        conditions.append(f"l.id = ${len(params)}::uuid")

    if status_filter:
        placeholders = ", ".join(f"${len(params)+i+1}" for i in range(len(status_filter)))
        conditions.append(f"l.status IN ({placeholders})")
        params.extend(status_filter)

    if archived_filter is True:
        conditions.append("l.archived = TRUE")
    elif archived_filter is False:
        conditions.append("l.archived = FALSE")
    # None → no archived clause

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sort_map = {
        "newest":    "l.created_at DESC",
        "oldest":    "l.created_at ASC",
        "name_asc":  "l.last_name ASC NULLS LAST, l.first_name ASC NULLS LAST",
        "name_desc": "l.last_name DESC NULLS LAST, l.first_name DESC NULLS LAST",
        "company_asc": "l.domain ASC NULLS LAST",
        "sent_desc": "l.sent_at DESC NULLS LAST",
    }
    order = sort_map.get(sort, sort_map["newest"])

    query = f"""
        SELECT l.id, l.first_name, l.last_name, l.email, l.service_area,
               l.status, l.archived, l.created_at, l.sent_at,
               d.subject AS draft_subject
        FROM sa_leads l
        LEFT JOIN sa_lead_drafts d ON d.lead_id = l.id AND d.is_current = TRUE
        {where}
        ORDER BY {order}
        LIMIT 200
    """

    pool = await get_pool()
    leads = await pool.fetch(query, *params)

    # Fetch active tokens for each lead
    tokens: dict[str, str] = {}
    if leads:
        token_rows = await pool.fetch(
            "SELECT lead_id::text, token FROM sa_review_tokens "
            "WHERE lead_id = ANY($1::uuid[]) AND is_active = TRUE",
            [r["id"] for r in leads],
        )
        tokens = {r["lead_id"]: r["token"] for r in token_rows}

    return JSONResponse({
        "leads": [
            {
                "id":            str(l["id"]),
                "first_name":    l["first_name"] or "",
                "last_name":     l["last_name"] or "",
                "email":         l["email"],
                "service_area":  l["service_area"] or "",
                "status":        l["status"] or "",
                "archived":      l["archived"],
                "created_at":    l["created_at"].isoformat() if l["created_at"] else "",
                "sent_at":       l["sent_at"].isoformat() if l["sent_at"] else "",
                "draft_subject": l["draft_subject"] or "",
                "token":         tokens.get(str(l["id"]), ""),
            }
            for l in leads
        ]
    })
```

- [ ] **Step 2: Deploy and smoke-test**

```bash
scp scripts/lead_review_server.py root@sovereignadvisory.ai:/tmp/lead_review_server.py
ssh root@sovereignadvisory.ai "docker cp /tmp/lead_review_server.py sa_lead_review:/app/lead_review_server.py && docker restart sa_lead_review && sleep 4"
```

Test default (no params — should still return leads):
```bash
ssh root@sovereignadvisory.ai "curl -s 'http://localhost:$(docker inspect sa_lead_review --format '{{(index (index .NetworkSettings.Ports \"5003/tcp\") 0).HostPort}}')/api/review/leads' -H 'X-Session-Key: bad' | python3 -c 'import sys,json; print(json.load(sys.stdin))'" 2>/dev/null || echo "401 expected without valid key — server is up"
```

Expected: 401 (no valid key in test — confirms server is running).

---

### Task 3: Add `bulk-archive` endpoint

**Files:**
- Modify: `scripts/lead_review_server.py` — add new endpoint after `list_leads`

- [ ] **Step 1: Add the endpoint**

Insert after the `list_leads` function (before the `@app.get("/review/{token}")` route):

```python
@app.post("/api/review/bulk-archive")
async def bulk_archive(request: Request, x_session_key: str = Header(None)):
    """Archive multiple leads at once. Sets archived=True without changing status.

    Body: { "lead_ids": ["uuid1", "uuid2", ...] }
    Returns: { "status": "ok", "archived_count": N }
    """
    if not x_session_key:
        raise HTTPException(status_code=401, detail="Missing X-Session-Key header")
    _purge_expired_sessions()
    sess = _sessions.get(x_session_key)
    if not sess or sess["expires_at"] < time.time():
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    body = await request.json()
    lead_ids = body.get("lead_ids", [])
    if not lead_ids:
        return JSONResponse({"status": "ok", "archived_count": 0})

    pool = await get_pool()

    # Admin can archive any lead; per-token session can only archive its own lead
    if sess["lead_id"] == "*":
        result = await pool.execute(
            "UPDATE sa_leads SET archived = TRUE WHERE id = ANY($1::uuid[]) AND archived = FALSE",
            lead_ids,
        )
    else:
        # Only allow the session's own lead
        allowed = [lid for lid in lead_ids if lid == sess["lead_id"]]
        if not allowed:
            return JSONResponse({"status": "ok", "archived_count": 0})
        result = await pool.execute(
            "UPDATE sa_leads SET archived = TRUE WHERE id = ANY($1::uuid[]) AND archived = FALSE",
            allowed,
        )

    # asyncpg returns "UPDATE N" string — parse the count
    count = int(result.split()[-1]) if result else 0
    return JSONResponse({"status": "ok", "archived_count": count})
```

- [ ] **Step 2: Deploy**

```bash
scp scripts/lead_review_server.py root@sovereignadvisory.ai:/tmp/lead_review_server.py
ssh root@sovereignadvisory.ai "docker cp /tmp/lead_review_server.py sa_lead_review:/app/lead_review_server.py && docker restart sa_lead_review && sleep 4 && docker logs sa_lead_review --tail=5"
```

Expected: server starts cleanly, no import errors.

---

### Task 4: Add `archive` action to `take_action`

**Files:**
- Modify: `scripts/lead_review_server.py` — `take_action` function

- [ ] **Step 1: Add `archive` to the allowed actions list and handle it**

In `take_action`, find:
```python
if action not in ("approve", "regenerate", "queue", "unqueue", "dnfu"):
```
Change to:
```python
if action not in ("approve", "regenerate", "queue", "unqueue", "dnfu", "archive"):
```

Then in the DB state changes block, add after the `dnfu` block:

```python
        elif action == "archive":
            disposition = body.get("disposition", "")
            new_status = "sent" if disposition == "sent" else "do_not_follow_up"
            await conn.execute(
                """
                UPDATE sa_leads
                SET archived = TRUE,
                    status = $1,
                    do_not_follow_up = $2,
                    reviewed_at = NOW()
                WHERE id = $3
                """,
                new_status,
                disposition == "declined",
                lead_id,
            )
            await conn.execute(
                "UPDATE sa_review_tokens SET is_active = FALSE, used_at = NOW() WHERE id = $1",
                token_row["id"],
            )
```

Then add early return for archive (alongside queue/unqueue):

Find:
```python
    if action in ("queue", "unqueue"):
        return JSONResponse({"status": "ok", "action": action})
```
Change to:
```python
    if action in ("queue", "unqueue", "archive"):
        return JSONResponse({"status": "ok", "action": action})
```

- [ ] **Step 2: Deploy and verify server starts**

```bash
scp scripts/lead_review_server.py root@sovereignadvisory.ai:/tmp/lead_review_server.py
ssh root@sovereignadvisory.ai "docker cp /tmp/lead_review_server.py sa_lead_review:/app/lead_review_server.py && docker restart sa_lead_review && sleep 4 && docker logs sa_lead_review --tail=5"
```

Expected: `Application startup complete.`

- [ ] **Step 3: Commit Chunk 1**

```bash
git add scripts/lead_review_server.py
git commit -m "feat: add archived column migration, filter/sort API, bulk-archive endpoint, archive action"
```

---

## Chunk 2: Frontend — Dashboard

---

### Task 5: Dashboard toolbar (filter pills, search, sort)

**Files:**
- Modify: `scripts/templates/lead_review.html` — dashboard HTML and JS

This task replaces the static `<table>` header with a toolbar containing pills, a search input, and a sort dropdown, then rewires the JS.

- [ ] **Step 1: Add CSS for pills and toolbar**

In the `<style>` block, add after the existing `.status-pill` rules:

```css
/* ── Dashboard toolbar ───────────────────────────────────────────── */
.dash-toolbar { display:flex; flex-wrap:wrap; gap:0.5rem; align-items:center; margin-bottom:0.75rem; }
.dash-toolbar input[type=text] {
  flex:1; min-width:140px; background:var(--bg-card); border:1px solid var(--border);
  border-radius:6px; padding:0.35rem 0.7rem; color:var(--text); font-size:0.85rem;
}
.dash-toolbar input[type=text]::placeholder { color:var(--text-2); }
.dash-toolbar select {
  background:var(--bg-card); border:1px solid var(--border); border-radius:6px;
  padding:0.35rem 0.6rem; color:var(--text); font-size:0.85rem; cursor:pointer;
}
.filter-pills { display:flex; flex-wrap:wrap; gap:0.4rem; margin-bottom:0.5rem; }
.filter-pill {
  padding:0.25rem 0.75rem; border-radius:20px; font-size:0.78rem; font-weight:600;
  cursor:pointer; border:1px solid var(--border); background:var(--bg-card);
  color:var(--text-2); transition:background 0.15s, color 0.15s;
  user-select:none;
}
.filter-pill.active { background:var(--gold-dim); color:var(--gold); border-color:rgba(200,169,110,0.4); }
.bulk-bar { display:none; align-items:center; gap:0.75rem; padding:0.5rem 0; }
.bulk-bar.visible { display:flex; }
.lead-cb { accent-color:var(--gold); width:15px; height:15px; cursor:pointer; flex-shrink:0; }
```

- [ ] **Step 2: Replace dashboard HTML**

Replace the entire `<div id="dashboard-screen">` block:

```html
  <!-- Dashboard -->
  <div id="dashboard-screen" style="display:none">
    <div class="dash-header">
      <h2>Lead Queue</h2>
      <p id="dashSubtitle"></p>
    </div>

    <!-- Filter pills -->
    <div class="filter-pills" id="filterPills">
      <span class="filter-pill" data-pill="all">All</span>
      <span class="filter-pill active" data-pill="needs_action">Needs Action</span>
      <span class="filter-pill" data-pill="sent">Sent</span>
      <span class="filter-pill" data-pill="declined">Declined</span>
      <span class="filter-pill" data-pill="queued">Queued</span>
      <span class="filter-pill" data-pill="archived">Archived</span>
    </div>

    <!-- Search + sort toolbar -->
    <div class="dash-toolbar">
      <input type="text" id="searchInput" placeholder="🔍  Search name, company, email…" oninput="applyClientFilter()">
      <select id="sortSelect" onchange="onSortChange()">
        <option value="newest">Newest</option>
        <option value="oldest">Oldest</option>
        <option value="name_asc">Name A→Z</option>
        <option value="name_desc">Name Z→A</option>
        <option value="company_asc">Company A→Z</option>
        <option value="sent_desc">Sent date (newest first)</option>
      </select>
    </div>

    <!-- Bulk action bar (shown when checkboxes are checked) -->
    <div class="bulk-bar" id="bulkBar">
      <span id="bulkCount" style="font-size:0.85rem;color:var(--text-2)">0 selected</span>
      <button class="btn" style="background:var(--gold-dim);color:var(--gold);border-color:rgba(200,169,110,0.3)" onclick="doBulkArchive()">Archive selected</button>
      <button class="btn" style="font-size:0.8rem" onclick="clearCheckboxes()">✕ Clear</button>
    </div>

    <table class="lead-table" id="leadTable">
      <thead>
        <tr>
          <th style="width:1.5rem"><input type="checkbox" class="lead-cb" id="cbSelectAll" title="Select all" onchange="toggleSelectAll(this)"></th>
          <th>Name</th>
          <th>Service</th>
          <th>Status</th>
          <th>Received</th>
        </tr>
      </thead>
      <tbody id="leadTableBody"></tbody>
    </table>
    <div class="empty-state" id="emptyState" style="display:none">No leads match this filter.</div>
  </div>
```

- [ ] **Step 3: Add filter state management JS**

In the `<script>` block, after the existing state variables (`let sessionKey`, `let currentToken`, `let regenPoll`), add:

```js
  // ── Filter / sort state ────────────────────────────────────────────────────
  const FILTER_KEY = 'sa_lead_dashboard_v1';
  let activePills  = ['needs_action'];   // restored from localStorage below
  let currentSort  = 'newest';
  let allLeads     = [];                 // full server response for client-side filter

  function loadFilterState() {
    try {
      const saved = JSON.parse(localStorage.getItem(FILTER_KEY) || '{}');
      if (Array.isArray(saved.pills) && saved.pills.length > 0) activePills = saved.pills;
      if (saved.sort) currentSort = saved.sort;
    } catch (_) {}
    // Sync UI
    document.querySelectorAll('.filter-pill').forEach(el => {
      el.classList.toggle('active', activePills.includes(el.dataset.pill));
    });
    const sel = document.getElementById('sortSelect');
    if (sel) sel.value = currentSort;
  }

  function saveFilterState() {
    localStorage.setItem(FILTER_KEY, JSON.stringify({ pills: activePills, sort: currentSort }));
  }

  function onPillClick(pill) {
    if (pill === 'all') {
      activePills = ['all'];
    } else {
      activePills = activePills.filter(p => p !== 'all');  // remove "all" mode
      if (activePills.includes(pill)) {
        activePills = activePills.filter(p => p !== pill);
        if (activePills.length === 0) activePills = ['needs_action'];  // fallback
      } else {
        activePills.push(pill);
      }
    }
    document.querySelectorAll('.filter-pill').forEach(el => {
      el.classList.toggle('active', activePills.includes(el.dataset.pill));
    });
    saveFilterState();
    showDashboard(false);  // re-fetch with new filters, no history push
  }

  function onSortChange() {
    currentSort = document.getElementById('sortSelect').value;
    saveFilterState();
    showDashboard(false);
  }

  // Build query params for the /api/review/leads request from activePills
  function pillsToParams() {
    if (activePills.includes('all')) return '?sort=' + currentSort;

    const pillStatusMap = {
      needs_action: ['pending_review', 'queued', 'regenerating'],
      sent:         ['sent'],
      declined:     ['do_not_follow_up'],
      queued:       ['queued'],
    };

    const statusPills   = activePills.filter(p => p !== 'archived');
    const archivedActive = activePills.includes('archived');

    const params = new URLSearchParams();
    params.set('sort', currentSort);

    let statuses = [];
    statusPills.forEach(p => {
      if (pillStatusMap[p]) statuses.push(...pillStatusMap[p]);
    });
    // De-duplicate
    [...new Set(statuses)].forEach(s => params.append('status', s));

    if (archivedActive) {
      params.set('archived', 'true');
    } else if (statusPills.length > 0) {
      // Implicit archived=false for all status-filtered queries
      params.set('archived', 'false');
    }
    // If no status pills and no archived pill, omit archived param (shouldn't reach here)

    return '?' + params.toString();
  }
```

- [ ] **Step 4: Wire pill click handlers**

After `loadFilterState()` is called (add the call to `loadFilterState()` at the top of the script init section, see Step 6), add:

```js
  document.querySelectorAll('.filter-pill').forEach(el => {
    el.addEventListener('click', () => onPillClick(el.dataset.pill));
  });
```

This will be placed inside the `DOMContentLoaded` or inline init block — see Task 7 Step 1 for exact placement.

- [ ] **Step 5: Rewrite `showDashboard` to use filter params**

Replace the existing `showDashboard` function:

```js
  async function showDashboard(pushHistory = true) {
    stopRegenPoll();
    currentToken = null;
    if (pushHistory) history.pushState({view:'dashboard'}, '', '/review/');
    document.getElementById('app-shell').style.display      = 'flex';
    document.getElementById('auth-screen').style.display    = 'none';
    document.getElementById('dashboard-screen').style.display = 'block';
    document.getElementById('detail-screen').style.display  = 'none';
    document.getElementById('actionBar').style.display      = 'none';
    document.getElementById('navBack').style.display        = 'none';
    document.getElementById('emptyState').style.display     = 'none';
    document.getElementById('leadTableBody').innerHTML      = '';
    clearCheckboxes();
    try {
      const res = await fetch('/api/review/leads' + pillsToParams(), {
        headers: { 'X-Session-Key': sessionKey },
      });
      if (res.status === 401) { sessionExpired(); return; }
      const data = await res.json();
      allLeads = data.leads || [];
      applyClientFilter();
    } catch (err) {
      console.error('showDashboard fetch error:', err);
      showToast('Failed to load lead list — please refresh.', true);
      document.getElementById('emptyState').style.display = 'block';
      document.getElementById('emptyState').textContent  = 'Failed to load leads. Please refresh the page.';
    }
  }
```

- [ ] **Step 6: Rewrite `renderDashboard` as `applyClientFilter`**

Replace the existing `renderDashboard` function with `applyClientFilter` (client-side search + render):

```js
  function applyClientFilter() {
    const q = (document.getElementById('searchInput')?.value || '').toLowerCase();
    const leads = q
      ? allLeads.filter(l =>
          [l.first_name, l.last_name, l.email, l.domain, l.service_area]
            .join(' ').toLowerCase().includes(q)
        )
      : allLeads;

    const tbody = document.getElementById('leadTableBody');
    tbody.innerHTML = '';
    let rendered = 0;

    leads.forEach(lead => {
      if (!lead.token && !['sent','do_not_follow_up'].includes(lead.status)) return;
      rendered++;
      const fullName = [lead.first_name, lead.last_name].filter(Boolean).join(' ');
      const tr = document.createElement('tr');
      tr.style.cursor = 'pointer';
      tr.dataset.leadId = lead.id;
      tr.innerHTML = `
        <td onclick="event.stopPropagation()">
          <input type="checkbox" class="lead-cb row-cb" data-id="${esc(lead.id)}" onchange="onRowCheck()">
        </td>
        <td>
          <div class="lead-name">${esc(fullName || lead.email)}</div>
          ${fullName ? `<div class="lead-email">${esc(lead.email)}</div>` : ''}
        </td>
        <td>${esc(lead.service_area || '—')}</td>
        <td>${statusPill(lead.status)}</td>
        <td>${fmtDate(lead.created_at)}</td>
      `;
      if (lead.token) {
        tr.addEventListener('click', () => showDetail(lead.token));
      }
      tbody.appendChild(tr);
    });

    const subtitle = document.getElementById('dashSubtitle');
    if (subtitle) subtitle.textContent = `${rendered} lead${rendered !== 1 ? 's' : ''}`;

    const es = document.getElementById('emptyState');
    es.style.display = rendered === 0 ? 'block' : 'none';
  }
```

Also update any existing calls to `renderDashboard(data.leads)` (they are already replaced in the `showDashboard` rewrite above).

---

### Task 6: Checkbox and bulk archive JS

**Files:**
- Modify: `scripts/templates/lead_review.html` — add checkbox helper functions

- [ ] **Step 1: Add checkbox management functions**

Add these functions to the `<script>` block:

```js
  function onRowCheck() {
    const checked = document.querySelectorAll('.row-cb:checked');
    const bulkBar = document.getElementById('bulkBar');
    const countEl = document.getElementById('bulkCount');
    if (checked.length > 0) {
      bulkBar.classList.add('visible');
      countEl.textContent = `${checked.length} selected`;
    } else {
      bulkBar.classList.remove('visible');
    }
    // Update select-all checkbox state
    const all = document.querySelectorAll('.row-cb');
    const cbAll = document.getElementById('cbSelectAll');
    if (cbAll) {
      cbAll.checked = all.length > 0 && checked.length === all.length;
      cbAll.indeterminate = checked.length > 0 && checked.length < all.length;
    }
  }

  function toggleSelectAll(cb) {
    document.querySelectorAll('.row-cb').forEach(el => { el.checked = cb.checked; });
    onRowCheck();
  }

  function clearCheckboxes() {
    document.querySelectorAll('.row-cb').forEach(el => { el.checked = false; });
    const cbAll = document.getElementById('cbSelectAll');
    if (cbAll) { cbAll.checked = false; cbAll.indeterminate = false; }
    const bulkBar = document.getElementById('bulkBar');
    if (bulkBar) bulkBar.classList.remove('visible');
  }

  async function doBulkArchive() {
    const checked = [...document.querySelectorAll('.row-cb:checked')];
    if (checked.length === 0) return;
    const lead_ids = checked.map(el => el.dataset.id);
    try {
      const res = await fetch('/api/review/bulk-archive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-Key': sessionKey },
        body: JSON.stringify({ lead_ids }),
      });
      if (res.status === 401) { sessionExpired(); return; }
      const data = await res.json();
      showToast(`Archived ${data.archived_count} lead${data.archived_count !== 1 ? 's' : ''}.`);
      showDashboard(false);
    } catch (err) {
      showToast('Bulk archive failed — please try again.', true);
    }
  }
```

- [ ] **Step 2: Deploy and smoke-test dashboard**

```bash
scp scripts/templates/lead_review.html root@sovereignadvisory.ai:/tmp/lead_review.html
ssh root@sovereignadvisory.ai "docker cp /tmp/lead_review.html sa_lead_review:/app/templates/lead_review.html && docker restart sa_lead_review && sleep 4"
```

Open `https://sovereignadvisory.ai/review/` in a browser. Verify:
- Filter pills appear and "Needs Action" is active by default
- Clicking "All" pill fetches all leads
- Clicking "Sent" pill fetches only sent leads
- Search input filters the list client-side
- Sort dropdown changes ordering
- Checkboxes appear on each row
- Selecting rows shows the bulk archive bar

---

### Task 7: Wire init and localStorage restore

**Files:**
- Modify: `scripts/templates/lead_review.html` — init block

The `loadFilterState()` call and pill click wiring need to run once when the page loads, before `showDashboard` is called.

- [ ] **Step 1: Add init call**

Find the `popstate` event listener (near the bottom of the `<script>` block) and add `loadFilterState()` just before it:

```js
  // Restore filter state from localStorage before first render
  loadFilterState();

  // Wire filter pill clicks
  document.querySelectorAll('.filter-pill').forEach(el => {
    el.addEventListener('click', () => onPillClick(el.dataset.pill));
  });

  window.addEventListener('popstate', ...  // existing line
```

- [ ] **Step 2: Commit Chunk 2**

```bash
git add scripts/templates/lead_review.html
git commit -m "feat: dashboard filter pills, search, sort, checkboxes, bulk archive"
```

---

## Chunk 3: Frontend — Archive Button on Review Page

---

### Task 8: Add Archive button with inline expand

**Files:**
- Modify: `scripts/templates/lead_review.html` — action bar HTML, CSS, and `doAction` JS

- [ ] **Step 1: Add CSS for archive expand**

Add to the `<style>` block:

```css
/* ── Archive inline expand ────────────────────────────────────────── */
.archive-expand {
  display:none; align-items:center; gap:0.5rem; flex-wrap:wrap;
  background:var(--bg-card); border:1px solid rgba(200,169,110,0.3);
  border-radius:8px; padding:0.5rem 0.75rem;
}
.archive-expand.visible { display:flex; }
.archive-expand .archive-label {
  font-size:0.82rem; color:var(--gold); font-weight:600; white-space:nowrap;
}
@media (max-width:640px) {
  .archive-expand { flex-direction:column; align-items:flex-start; }
}
```

- [ ] **Step 2: Add Archive button and expand panel to action bar HTML**

In the action bar `<div id="actionBar">`, add after `btnDnfu` and before `btnQueue`:

```html
      <button class="btn" id="btnArchive" style="background:var(--gold-dim);color:var(--gold);border-color:rgba(200,169,110,0.3)">📁 Archive</button>
      <div class="archive-expand" id="archiveExpand">
        <span class="archive-label">Archive as…</span>
        <button class="btn btn-approve" id="btnArchiveSent" style="font-size:0.82rem;padding:0.35rem 0.9rem">Sent</button>
        <button class="btn btn-dnfu"    id="btnArchiveDeclined" style="font-size:0.82rem;padding:0.35rem 0.9rem">Declined</button>
        <button class="btn"             id="btnArchiveCancel"   style="font-size:0.82rem;padding:0.35rem 0.7rem;color:var(--text-2)">✕</button>
      </div>
```

- [ ] **Step 3: Wire Archive button JS**

In `showDetail`, add the archive button wiring alongside the other `onclick` assignments:

```js
    document.getElementById('btnArchive').onclick = () => {
      document.getElementById('archiveExpand').classList.add('visible');
      document.getElementById('btnArchive').style.display = 'none';
    };
    document.getElementById('btnArchiveCancel').onclick = () => {
      document.getElementById('archiveExpand').classList.remove('visible');
      document.getElementById('btnArchive').style.display = '';
    };
    document.getElementById('btnArchiveSent').onclick    = () => doAction('archive', 'sent');
    document.getElementById('btnArchiveDeclined').onclick = () => doAction('archive', 'declined');
```

- [ ] **Step 4: Update `doAction` to handle `archive`**

In the `doAction` function, update the signature and add archive handling:

```js
  async function doAction(action, disposition = '') {
```

Then add after the `dnfu` confirmation block and before the fetch call — or more precisely, pass `disposition` in the payload. Update the `payload` construction:

```js
    const payload = { action, notes };
    if (editedBody)    payload.email_body_text = editedBody;
    if (disposition)   payload.disposition     = disposition;
```

And add the archive result handling alongside the other action results:

```js
    } else if (action === 'archive') {
      showToast('Lead archived.');
      showDashboard();
```

Also ensure `setActionButtonsDisabled` covers the new archive buttons. In the `setActionButtonsDisabled` function, add `'btnArchive'` to the list of button IDs.

- [ ] **Step 5: Hide archive expand when navigating away**

In `showDashboard`, add:

```js
    const ae = document.getElementById('archiveExpand');
    if (ae) { ae.classList.remove('visible'); }
    const ba = document.getElementById('btnArchive');
    if (ba) ba.style.display = '';
```

- [ ] **Step 6: Deploy and end-to-end test**

```bash
scp scripts/templates/lead_review.html root@sovereignadvisory.ai:/tmp/lead_review.html
ssh root@sovereignadvisory.ai "docker cp /tmp/lead_review.html sa_lead_review:/app/templates/lead_review.html && docker restart sa_lead_review && sleep 4"
```

Manual test checklist:
1. Open a lead's review page
2. Click "Archive" — expand panel appears with "Sent", "Declined", "✕"
3. Click "✕" — panel collapses, Archive button returns
4. Click "Archive" → "Declined" — lead disappears from Needs Action filter, toast shown
5. Switch to "Declined" pill on dashboard — archived lead appears
6. Switch to "Declined" + "Archived" pills — archived declined lead appears
7. Test on mobile (narrow viewport) — Sent/Declined stack vertically

- [ ] **Step 7: Commit Chunk 3**

```bash
git add scripts/templates/lead_review.html scripts/lead_review_server.py
git commit -m "feat: archive button with inline expand on review page"
```

---

## Post-deployment verification

```bash
# Confirm archived column exists
ssh root@sovereignadvisory.ai 'docker exec sa_lead_review python3 -c "
import asyncio, asyncpg, os
async def f():
    p = await asyncpg.create_pool(os.environ[\"DATABASE_URL\"])
    r = await p.fetchrow(\"SELECT count(*) FROM sa_leads WHERE archived = false\")
    print(\"Non-archived leads:\", r[0])
asyncio.run(f())
"'

# Confirm new endpoints exist
ssh root@sovereignadvisory.ai "docker exec sa_lead_review python3 -c \"
import lead_review_server as s
routes = [r.path for r in s.app.routes]
print('bulk-archive' in str(routes), 'leads' in str(routes))
\""
```
