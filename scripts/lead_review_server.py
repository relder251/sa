"""
lead_review_server.py — Sovereign Advisory Lead Review Portal (FastAPI)

Endpoints:
  GET  /review/{token}                  → serve the HTML review portal (password-gated)
  POST /api/review/{token}/auth         → verify password, return session key
  GET  /api/review/{token}              → return lead + draft JSON (requires session key)
  POST /api/review/{token}/action       → approve / regenerate / queue / dnfu
  GET  /api/review/{token}/pdf          → stream the PDF lead brief
  GET  /health                          → liveness probe

Environment variables (all required unless noted):
  DATABASE_URL          postgres://user:pass@host:5432/db
  LEAD_REVIEW_PASSWORD  shared password for the review portal
  N8N_BASE_URL          http://n8n:5678  (internal Docker hostname)
  PDF_OUTPUT_DIR        /data/output/lead_pdfs  (optional, default shown)
"""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import ssl
import time
from contextlib import asynccontextmanager
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode

import asyncpg
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
import httpx
from jose import jwt, JWTError
from lead_pdf_generator import generate_lead_pdf

# ── Config ─────────────────────────────────────────────────────────────────────
DATABASE_URL         = os.environ["DATABASE_URL"]
REVIEW_PASSWORD      = os.environ["LEAD_REVIEW_PASSWORD"]
N8N_BASE_URL         = os.environ.get("N8N_BASE_URL", "http://n8n:5678")
LITELLM_BASE_URL     = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_API_KEY      = os.environ.get("LITELLM_API_KEY", "")
PDF_OUTPUT_DIR       = Path(os.environ.get("PDF_OUTPUT_DIR", "/data/output/lead_pdfs"))
TEMPLATE_DIR         = Path(__file__).parent / "templates"

# ── SMTP config (Resend) ───────────────────────────────────────────────────────
SMTP_HOST      = os.environ.get("NEO_SMTP_HOST", "smtp.resend.com")
SMTP_PORT      = int(os.environ.get("NEO_SMTP_PORT", "465"))
SMTP_USER      = os.environ.get("NEO_SMTP_USER", "resend")
SMTP_PASS      = os.environ.get("NEO_SMTP_PASS", "")
SMTP_FROM      = os.environ.get("NOTIFY_EMAIL", "relder@sovereignadvisory.ai")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Robert Elder")

# ── OIDC / Keycloak config ─────────────────────────────────────────────────────
OIDC_ENABLED           = os.environ.get("OIDC_ENABLED", "false").lower() == "true"
KEYCLOAK_ISSUER        = os.environ.get("KEYCLOAK_ISSUER", "http://keycloak:8080/realms/agentic-sdlc")
KEYCLOAK_EXTERNAL_URL  = os.environ.get("KEYCLOAK_EXTERNAL_URL", "http://localhost:8080")
KEYCLOAK_CLIENT_ID     = os.environ.get("KEYCLOAK_CLIENT_ID", "lead-review")
KEYCLOAK_CLIENT_SECRET = os.environ.get("KEYCLOAK_CLIENT_SECRET", "")
# If set, used as the base URL for OIDC callback (avoids relying on X-Forwarded-* headers).
# Example: https://sovereignadvisory.ai
LEAD_REVIEW_PUBLIC_URL = os.environ.get("LEAD_REVIEW_PUBLIC_URL", "").rstrip("/")

# JWKS cache: {"keys": [...], "fetched_at": float}
_jwks_cache: dict = {}
JWKS_TTL = 3600  # 1 hour

# In-memory session store: {session_key: {lead_id, expires_at}}
# Small deployment — no Redis needed. Trade-off: sessions are lost on container
# restart (crashes, Watchtower nightly updates). Acceptable at this scale.
_sessions: dict[str, dict] = {}
SESSION_TTL = 4 * 3600  # 4 hours

# OIDC post-login nonce store: {nonce: {session_key, expires_at}}
# Short-lived (60s) one-time tokens used to hand the session key to the browser
# without exposing it in URLs (which appear in logs, browser history, Referer headers).
_oidc_nonces: dict[str, dict] = {}
OIDC_NONCE_TTL = 60  # seconds

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pool is created lazily on first request — no startup DB dependency
    yield
    if _pool:
        await _pool.close()

app = FastAPI(title="SA Lead Review Portal", docs_url=None, redoc_url=None, lifespan=lifespan)


# ── DB pool ────────────────────────────────────────────────────────────────────
_pool: Optional[asyncpg.Pool] = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool




# ── Session helpers ─────────────────────────────────────────────────────────────
def _create_session(lead_id: str) -> str:
    key = secrets.token_hex(32)
    _sessions[key] = {"lead_id": lead_id, "expires_at": time.time() + SESSION_TTL}
    return key


def _verify_session(token: str, session_key: str) -> str:
    """Return lead_id if valid, raise 401 otherwise. Returns '*' for admin sessions."""
    _purge_expired_sessions()
    sess = _sessions.get(session_key)
    if not sess:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    if sess["expires_at"] < time.time():
        _sessions.pop(session_key, None)
        raise HTTPException(status_code=401, detail="Session expired")
    return sess["lead_id"]


def _purge_expired_sessions():
    now = time.time()
    expired = [k for k, v in _sessions.items() if v["expires_at"] < now]
    for k in expired:
        _sessions.pop(k, None)


# ── OIDC / JWKS helpers ────────────────────────────────────────────────────────

async def _get_jwks() -> list:
    """Return Keycloak JWKS keys, cached for JWKS_TTL seconds."""
    now = time.time()
    if _jwks_cache.get("fetched_at", 0) + JWKS_TTL > now:
        return _jwks_cache["keys"]
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Fetch OIDC discovery document
        disc = await client.get(f"{KEYCLOAK_ISSUER}/.well-known/openid-configuration")
        disc.raise_for_status()
        jwks_uri = disc.json()["jwks_uri"]
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        keys = resp.json()["keys"]
    _jwks_cache["keys"] = keys
    _jwks_cache["fetched_at"] = now
    return keys


async def _verify_jwt(id_token: str, access_token: str | None = None) -> dict:
    """Decode and verify a Keycloak id_token. Returns claims or raises HTTPException."""
    try:
        keys = await _get_jwks()
        claims = jwt.decode(
            id_token,
            keys,
            algorithms=["RS256"],
            audience=KEYCLOAK_CLIENT_ID,
            options={"verify_exp": True},
            access_token=access_token,
        )
        return claims
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid OIDC token: {exc}")


def _kc_auth_url(redirect_uri: str, state: str) -> str:
    """Build the Keycloak authorization URL for the browser redirect."""
    params = {
        "client_id": KEYCLOAK_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    return f"{KEYCLOAK_EXTERNAL_URL}/realms/agentic-sdlc/protocol/openid-connect/auth?{urlencode(params)}"


async def _exchange_code(code: str, redirect_uri: str) -> tuple[str, str]:
    """Exchange authorization code for tokens. Returns (id_token, access_token)."""
    token_url = f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": KEYCLOAK_CLIENT_ID,
                "client_secret": KEYCLOAK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if resp.status_code != 200:
            import sys
            print(
                f"[lead_review] OIDC exchange FAILED {resp.status_code}: {resp.text}"
                f" | redirect_uri={redirect_uri}",
                file=sys.stderr, flush=True,
            )
            raise HTTPException(status_code=401, detail=f"OIDC token exchange failed: {resp.text}")
        data = resp.json()
        return data["id_token"], data["access_token"]


# ── Token / lead helpers ────────────────────────────────────────────────────────
async def _get_review_token(token: str) -> asyncpg.Record:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT rt.id, rt.lead_id, rt.n8n_resume_url, rt.is_active, rt.used_at
        FROM sa_review_tokens rt
        WHERE rt.token = $1
        """,
        token,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Review link not found")
    if not row["is_active"]:
        raise HTTPException(status_code=410, detail="Review link has already been used")
    return row


async def _get_lead_with_draft(lead_id: str) -> dict:
    pool = await get_pool()
    lead = await pool.fetchrow("SELECT * FROM sa_leads WHERE id = $1", lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    draft = await pool.fetchrow(
        "SELECT * FROM sa_lead_drafts WHERE lead_id = $1 AND is_current = TRUE ORDER BY version DESC LIMIT 1",
        lead_id,
    )

    def _parse_jsonb(val):
        if val is None:
            return []
        if isinstance(val, (list, dict)):
            return val
        try:
            return json.loads(val)
        except Exception:
            return []

    def _parse_jsonb_obj(val):
        if val is None:
            return {}
        if isinstance(val, dict):
            return val
        try:
            return json.loads(val)
        except Exception:
            return {}

    return {
        "lead": {
            "id":                   str(lead["id"]),
            "first_name":           lead["first_name"] or "",
            "last_name":            lead["last_name"] or "",
            "email":                lead["email"],
            "domain":               lead["domain"] or "",
            "service_area":         lead["service_area"] or "",
            "message":              lead["message"] or "",
            "summary":              lead["summary"] or "",
            "approach":             lead["approach"] or "",
            "conversation_starters": _parse_jsonb(lead["conversation_starters"]),
            "questions":            _parse_jsonb(lead["questions"]),
            "scenarios":            _parse_jsonb(lead["scenarios"]),
            "company_research":     _parse_jsonb_obj(lead["company_research"]),
            "person_research":      _parse_jsonb_obj(lead["person_research"]),
            "status":               lead["status"] or "",
            "pdf_path":             lead["pdf_path"] or "",
            "created_at":           lead["created_at"].isoformat() if lead["created_at"] else "",
        },
        "draft": {
            "id":        str(draft["id"]) if draft else "",
            "version":   draft["version"] if draft else 1,
            "subject":   draft["subject"] or "" if draft else "",
            "body_html": draft["body_html"] or "" if draft else "",
            "body_text": draft["body_text"] or "" if draft else "",
        } if draft else {},
    }


# ── SMTP helper ─────────────────────────────────────────────────────────────────

def _send_outreach_email_sync(to_email: str, subject: str, body_text: str) -> None:
    """Send outreach email via Resend SMTP (SSL on port 465). Runs in a thread."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{SMTP_FROM_NAME} <{SMTP_FROM}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(body_text, "plain"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_FROM, to_email, msg.as_string())


# ── Draft regeneration (server-side, no n8n dependency) ────────────────────────

async def _regenerate_draft_via_llm(lead_id: str, notes: str) -> None:
    """
    Call LiteLLM directly to regenerate the email draft and save as a new version.
    Runs as a background asyncio task so the HTTP response returns immediately.
    """
    import re

    try:
        data = await _get_lead_with_draft(lead_id)
        lead = data["lead"]
        current_draft = data["draft"]

        # Build prospect context
        name    = f"{lead['first_name']} {lead['last_name']}".strip()
        company = lead.get("domain") or (lead["email"].split("@")[-1] if "@" in lead["email"] else "")

        research = lead.get("company_research") or {}
        research_text = research.get("summary", "") if isinstance(research, dict) else str(research)

        analysis_parts = []
        if lead.get("summary"):
            analysis_parts.append(f"Strategic summary: {lead['summary']}")
        if lead.get("approach"):
            analysis_parts.append(f"Recommended approach: {lead['approach']}")
        analysis_text = "\n".join(analysis_parts)

        prev_subject = current_draft.get("subject", "") if current_draft else ""
        prev_body    = current_draft.get("body_text", "") if current_draft else ""

        # Build prompt
        system_msg = (
            "You are writing on behalf of Robert Elder, CEO of Sovereign Advisory. "
            "Return ONLY a valid JSON object — no preamble, no explanation, no markdown. "
            "Your entire response must be pure JSON starting with { and ending with }. "
            'Fields: subject (string), body_text (plain text email, no HTML).'
        )

        parts = [
            f"Prospect: {name}",
            f"Company: {company}",
            f"Message: {lead.get('message', '')}",
        ]
        if research_text:
            parts.append(f"\nResearch:\n{research_text}")
        if analysis_text:
            parts.append(f"\nStrategic analysis:\n{analysis_text}")
        if prev_subject or prev_body:
            parts.append(f"\nPrevious draft subject: {prev_subject}\nPrevious draft:\n{prev_body}")
        if notes:
            parts.append(f"\nReviewer feedback (you MUST incorporate this):\n{notes}")
        parts.append("\nWrite a revised outreach email that fully addresses the reviewer feedback above.")

        user_msg = "\n".join(parts)

        # Call LiteLLM
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{LITELLM_BASE_URL}/v1/chat/completions",
                headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
                json={
                    "model": "cloud/smart",
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user",   "content": user_msg},
                    ],
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()

        # Parse JSON from LLM response
        try:
            draft_json = json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if m:
                draft_json = json.loads(m.group())
            else:
                raise ValueError(f"LLM returned non-JSON: {content[:300]}")

        new_subject = draft_json.get("subject") or prev_subject
        new_body    = draft_json.get("body_text") or ""
        next_version = (current_draft.get("version", 1) if current_draft else 1) + 1

        # Save new draft and reset status
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sa_lead_drafts SET is_current = FALSE WHERE lead_id = $1",
                lead_id,
            )
            await conn.execute(
                """
                INSERT INTO sa_lead_drafts (lead_id, version, subject, body_text, is_current)
                VALUES ($1, $2, $3, $4, TRUE)
                """,
                lead_id, next_version, new_subject, new_body,
            )
            await conn.execute(
                "UPDATE sa_leads SET status = 'pending_review' WHERE id = $1",
                lead_id,
            )
        print(f"[regen] lead {lead_id} → new draft v{next_version} saved", flush=True)

    except Exception as exc:
        print(f"[error] _regenerate_draft_via_llm failed for lead {lead_id}: {exc}", flush=True)
        # Reset status so reviewer isn't stuck on 'regenerating'
        try:
            pool = await get_pool()
            await pool.execute(
                "UPDATE sa_leads SET status = 'pending_review' WHERE id = $1 AND status = 'regenerating'",
                lead_id,
            )
        except Exception:
            pass


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


@app.get("/review/", response_class=HTMLResponse)
async def dashboard_page():
    """Serve the dashboard HTML (no token — password-gated SPA)."""
    html_path = TEMPLATE_DIR / "lead_review.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="Review template missing")
    return HTMLResponse(content=html_path.read_text(), status_code=200, headers=_NO_CACHE)


@app.post("/api/review/auth")
async def global_auth(request: Request):
    """Authenticate with just the password — returns a session key valid for all leads."""
    body = await request.json()
    if not hmac.compare_digest(body.get("password", ""), REVIEW_PASSWORD):
        raise HTTPException(status_code=401, detail="Incorrect password")
    session_key = _create_session("*")  # "*" = admin, can access any lead
    return JSONResponse({"session_key": session_key})


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
        SELECT l.id, l.first_name, l.last_name, l.email, l.domain, l.service_area,
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
            "SELECT lead_id::text, token::text FROM sa_review_tokens "
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
                "domain":        l["domain"] or "",
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


@app.get("/review/{token}", response_class=HTMLResponse)
async def review_page(token: str, request: Request, sk: Optional[str] = None, oidc_nonce: Optional[str] = None):
    """Serve the review HTML portal.
    - OIDC mode: redirects to Keycloak if no session established.
    - Password mode: serves HTML directly (client-side auth form).
    """
    html_path = TEMPLATE_DIR / "lead_review.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="Review template missing")

    if OIDC_ENABLED:
        resolved_sk: Optional[str] = sk
        if oidc_nonce:
            # Exchange one-time nonce for session key; nonce is consumed on use.
            entry = _oidc_nonces.pop(oidc_nonce, None)
            if entry and time.time() < entry["expires_at"]:
                resolved_sk = entry["session_key"]
        if resolved_sk:
            # Inject session key directly into sessionStorage via an inline script
            # so JS picks it up immediately without showing the password form.
            html = html_path.read_text()
            inject = (
                f'<script>'
                f'sessionStorage.setItem("sa_review_sk", {json.dumps(resolved_sk)});'
                f'window.__OIDC__ = true;'
                f'</script>'
            )
            html = html.replace("</head>", f"{inject}\n</head>")
            return HTMLResponse(content=html, status_code=200, headers=_NO_CACHE)
        # No session — redirect to Keycloak
        # Prefer explicit LEAD_REVIEW_PUBLIC_URL; fall back to forwarded headers.
        if LEAD_REVIEW_PUBLIC_URL:
            callback_url = f"{LEAD_REVIEW_PUBLIC_URL}/auth/callback"
        else:
            proto = request.headers.get("x-forwarded-proto", request.url.scheme)
            host  = request.headers.get("host", request.url.netloc)
            callback_url = f"{proto}://{host}/auth/callback"
        return RedirectResponse(url=_kc_auth_url(callback_url, state=token))

    return HTMLResponse(content=html_path.read_text(), status_code=200, headers=_NO_CACHE)


@app.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Keycloak OIDC authorization code callback.
    Exchanges code for id_token, creates session, redirects to /review/{token}?sk=...
    state = review token (set in _kc_auth_url).
    """
    if error:
        raise HTTPException(status_code=401, detail=f"OIDC error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    if LEAD_REVIEW_PUBLIC_URL:
        callback_url = f"{LEAD_REVIEW_PUBLIC_URL}/auth/callback"
    else:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host  = request.headers.get("host", request.url.netloc)
        callback_url = f"{proto}://{host}/auth/callback"
    id_token, access_token = await _exchange_code(code, callback_url)
    await _verify_jwt(id_token, access_token)  # validates signature, expiry, audience, at_hash

    # Look up the review token to get the lead_id — reuses existing session logic
    token_row = await _get_review_token(state)
    session_key = _create_session(str(token_row["lead_id"]))

    # Use a short-lived one-time nonce in the URL instead of the session key.
    # The session key itself never appears in URLs (logs, browser history, Referer).
    nonce = secrets.token_hex(32)
    _oidc_nonces[nonce] = {"session_key": session_key, "expires_at": time.time() + OIDC_NONCE_TTL}
    return RedirectResponse(url=f"/review/{state}?oidc_nonce={nonce}")


@app.post("/api/review/{token}/auth")
async def auth(token: str, request: Request):
    """Verify password and return a session key."""
    body = await request.json()
    password = body.get("password", "")

    # Constant-time comparison
    if not hmac.compare_digest(password, REVIEW_PASSWORD):
        raise HTTPException(status_code=401, detail="Incorrect password")

    # Validate the token exists and is active
    token_row = await _get_review_token(token)
    session_key = _create_session(str(token_row["lead_id"]))
    return JSONResponse({"session_key": session_key})


@app.get("/api/review/{token}")
async def get_review_data(token: str, x_session_key: str = Header(None)):
    if not x_session_key:
        raise HTTPException(status_code=401, detail="Missing X-Session-Key header")
    token_row = await _get_review_token(token)
    session_lead_id = _verify_session(token, x_session_key)

    if session_lead_id != "*" and session_lead_id != str(token_row["lead_id"]):
        raise HTTPException(status_code=403, detail="Session/token mismatch")

    return await _get_lead_with_draft(str(token_row["lead_id"]))


@app.post("/api/review/{token}/action")
async def take_action(token: str, request: Request, x_session_key: str = Header(None)):
    """
    Handle reviewer decisions.

    Body: {
      "action": "approve" | "regenerate" | "queue" | "dnfu",
      "notes":  "optional notes for regenerate",
      "email_body_text": "edited email body (optional, for approve)"
    }
    """
    if not x_session_key:
        raise HTTPException(status_code=401, detail="Missing X-Session-Key header")
    token_row = await _get_review_token(token)
    session_lead_id = _verify_session(token, x_session_key)
    if session_lead_id != "*" and session_lead_id != str(token_row["lead_id"]):
        raise HTTPException(status_code=403, detail="Session/token mismatch")
    lead_id = str(token_row["lead_id"])

    body    = await request.json()
    action  = body.get("action")
    notes   = body.get("notes", "")
    email_body_text = body.get("email_body_text", "")

    if action not in ("approve", "regenerate", "queue", "unqueue", "dnfu", "archive"):
        raise HTTPException(status_code=400, detail=f"Unknown action: {action!r}")

    pool = await get_pool()
    n8n_resume_url = token_row["n8n_resume_url"]

    # ── Apply DB state changes ─────────────────────────────────────────────
    async with pool.acquire() as conn:
        if action == "approve":
            # If reviewer edited the email, update the draft first
            if email_body_text:
                await conn.execute(
                    "UPDATE sa_lead_drafts SET body_text = $1 WHERE lead_id = $2 AND is_current = TRUE",
                    email_body_text, lead_id,
                )
            # Do NOT mark token inactive here — do it after successful send below

        elif action == "regenerate":
            await conn.execute(
                "UPDATE sa_leads SET status = 'regenerating' WHERE id = $1",
                lead_id,
            )
            if notes:
                await conn.execute(
                    "UPDATE sa_lead_drafts SET rejection_notes = $1 WHERE lead_id = $2 AND is_current = TRUE",
                    notes, lead_id,
                )

        elif action == "queue":
            await conn.execute(
                "UPDATE sa_leads SET status = 'queued', reviewed_at = NOW() WHERE id = $1",
                lead_id,
            )

        elif action == "unqueue":
            await conn.execute(
                "UPDATE sa_leads SET status = 'pending_review', reviewed_at = NULL WHERE id = $1",
                lead_id,
            )

        elif action == "dnfu":
            await conn.execute(
                """
                UPDATE sa_leads
                SET status = 'do_not_follow_up', do_not_follow_up = TRUE, reviewed_at = NOW()
                WHERE id = $1
                """,
                lead_id,
            )
            await conn.execute(
                "UPDATE sa_review_tokens SET is_active = FALSE, used_at = NOW() WHERE id = $1",
                token_row["id"],
            )

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

    # ── Queue / Unqueue: park or restore for later, do not advance workflow ────
    if action in ("queue", "unqueue", "archive"):
        return JSONResponse({"status": "ok", "action": action})

    # ── Approve: send outreach email directly via SMTP ──────────────────────
    # We own the email send here — no dependency on n8n staying alive.
    if action == "approve":
        data = await _get_lead_with_draft(lead_id)
        lead_data  = data["lead"]
        draft_data = data["draft"]
        to_email   = lead_data["email"]
        subject    = draft_data.get("subject") or "Following up"
        body       = draft_data.get("body_text") or ""
        if email_body_text:
            body = email_body_text

        try:
            await asyncio.get_running_loop().run_in_executor(
                None, _send_outreach_email_sync, to_email, subject, body
            )
        except Exception as exc:
            print(f"[error] SMTP send failed for lead {lead_id}: {exc}", flush=True)
            raise HTTPException(status_code=502, detail=f"Email send failed: {exc}")

        # Email sent — now commit the permanent state changes
        pool2 = await get_pool()
        async with pool2.acquire() as conn2:
            await conn2.execute(
                "UPDATE sa_leads SET status = 'sent', sent_at = NOW() WHERE id = $1",
                lead_id,
            )
            await conn2.execute(
                "UPDATE sa_review_tokens SET is_active = FALSE, used_at = NOW() WHERE id = $1",
                token_row["id"],
            )

        return JSONResponse({"status": "ok", "action": action})

    # ── Regenerate: call LiteLLM directly (server-side, no n8n dependency) ─────
    # The n8n wait webhook can only be consumed once per execution — using it for
    # regeneration caused 409 Conflict on all attempts after the first, silently
    # discarding reviewer notes. The server now handles regeneration in-process.
    if action == "regenerate":
        asyncio.create_task(_regenerate_draft_via_llm(lead_id, notes))
        return JSONResponse({"status": "ok", "action": action})

    # ── DNFU: resume n8n workflow ─────────────────────────────────────────────
    # Rewrite the stored resume URL to use the internal Docker hostname.
    # The URL stored in the DB may have been generated with a public hostname;
    # parse and replace the scheme+host portion instead of matching literals.
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(n8n_resume_url)
    internal_base = urlparse(N8N_BASE_URL)
    internal_resume_url = urlunparse(parsed._replace(
        scheme=internal_base.scheme,
        netloc=internal_base.netloc,
    ))

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                internal_resume_url,
                params={"action": action, "notes": notes, "lead_id": lead_id},
            )
            resp.raise_for_status()
    except Exception as exc:
        print(f"[warn] n8n resume failed for lead {lead_id}: {exc}", flush=True)

    return JSONResponse({"status": "ok", "action": action})


@app.get("/api/review/{token}/pdf")
async def get_pdf(token: str, x_session_key: str = Header(None)):
    """Stream the lead brief PDF, generating it on-the-fly if needed."""
    if not x_session_key:
        raise HTTPException(status_code=401, detail="Missing X-Session-Key header")
    token_row = await _get_review_token(token)
    session_lead_id = _verify_session(token, x_session_key)
    if session_lead_id != "*" and session_lead_id != str(token_row["lead_id"]):
        raise HTTPException(status_code=403, detail="Session/token mismatch")
    lead_id = str(token_row["lead_id"])

    data = await _get_lead_with_draft(lead_id)
    lead, draft = data["lead"], data["draft"]

    # Check if PDF already exists
    pdf_path = lead.get("pdf_path")
    if pdf_path and Path(pdf_path).exists():
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"lead_brief_{lead['last_name'] or lead_id[:8]}.pdf",
        )

    # Generate on-the-fly
    PDF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = str(PDF_OUTPUT_DIR / f"lead_{lead_id}.pdf")
    generate_lead_pdf(lead, draft, out_path)

    # Save path to DB
    pool = await get_pool()
    await pool.execute(
        "UPDATE sa_leads SET pdf_path = $1 WHERE id = $2", out_path, lead_id
    )

    return FileResponse(
        out_path,
        media_type="application/pdf",
        filename=f"lead_brief_{lead.get('last_name') or lead_id[:8]}.pdf",
    )


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5003, log_level="info")
