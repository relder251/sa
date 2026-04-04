"""
company_research_server.py — Enhanced Company Research Microservice

Two modes of operation:

SYNC (default, backward-compatible):
  POST /research  {domain, ...}
  → blocks until complete, returns full profile

ASYNC (preferred for n8n stability):
  POST /research  {domain, ..., callback_url: "http://n8n:5678/webhook/..."}
  → returns immediately: {"status": "queued", "job_id": "..."}
  → when done, POSTs full profile JSON to callback_url
  → n8n uses a Wait node to receive the callback; no timeout risk

GET /health  — liveness probe
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime

import httpx
import trafilatura
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_API_KEY  = os.environ.get("LITELLM_API_KEY", "")
RESEARCH_MODEL   = os.environ.get("RESEARCH_STRUCTURING_MODEL", "cloud/fast")

_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 3600

# Signals that a domain is parked / for-sale — skip these pages
PARKING_SIGNALS = [
    "hugedomains.com", "godaddy.com/parking", "domain for sale",
    "buy this domain", "sedoparking.com", "this domain is parked",
    "namecheap.com parking", "afternic.com",
]

SYSTEM_PROMPT = """You are a senior business intelligence analyst producing a company profile \
for a strategic sales engagement. Extract and structure ONLY factual information that is \
explicitly present in the provided web content. Do NOT invent, guess, or hallucinate facts. \
If a field cannot be determined from the sources, use null for strings or [] for arrays.

Return ONLY a valid JSON object — no preamble, no markdown fences. Use this exact schema:

{
  "company_name": "Legal company name (string or null)",
  "website": "Primary website URL (string or null)",
  "founded_year": "Year founded as integer, or null",
  "years_in_business": "Calculated from founded_year to current year as integer, or null",
  "industry": "Primary industry/sector (string or null)",
  "sub_industry": "More specific niche or vertical (string or null)",
  "business_model": "How the company generates revenue — B2B/B2C/SaaS/services/product/etc. (string or null)",
  "employee_count": "Specific number or range, e.g. '120', '500-1000', '~2,000' (string or null)",
  "annual_revenue": "Estimated annual revenue, e.g. '$5M', '$50-100M ARR', '$2B' (string or null)",
  "funding_stage": "Bootstrapped/Seed/Series A-D/Public/PE-backed/etc. and any known raise amounts (string or null)",
  "geographic_footprint": "HQ location and any other offices, regions, or countries served (string or null)",
  "key_products_services": ["List of primary products or services offered"],
  "key_clients_partners": ["Notable named clients or partners if publicly mentioned"],
  "leadership": ["Key executives: Name, Title format"],
  "recent_projects": ["Specific projects, implementations, or deployments announced in the last 1-2 years"],
  "recent_contracts": ["Specific contracts, awards, or deals announced in the last 1-2 years"],
  "recent_initiatives": ["Strategic initiatives, transformations, or programs they are publicly pursuing"],
  "recent_news": ["Notable news items, announcements, or events from the last 1-2 years — one sentence each"],
  "technology_stack": ["Known technologies, platforms, tools, or vendors they use"],
  "pain_points": ["Inferred operational or strategic challenges based on their industry, size, public statements, and the prospect's own inquiry — be specific and actionable"],
  "competitive_position": "Their position in their market — leader/challenger/niche/emerging (string or null)",
  "growth_signals": ["Any signals of growth, expansion, hiring, or investment activity"],
  "data_confidence": "overall confidence: high (3+ solid sources), medium (1-2 sources), or low (sparse data)"
}"""


class ResearchRequest(BaseModel):
    domain: str
    first_name: str = ""
    last_name: str = ""
    service: str = ""
    message: str = ""
    callback_url: str = ""   # if set: async mode — respond 202 immediately, POST result here when done


app = FastAPI(title="Company Research Service")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/research")
async def research(req: ResearchRequest, background_tasks: BackgroundTasks):
    domain = req.domain.lower().strip()

    # Cache hit — respond immediately regardless of mode
    cached = _CACHE.get(domain)
    if cached and (time.time() - cached[0]) < CACHE_TTL:
        log.info("Cache hit for %s", domain)
        result = dict(cached[1])
        result["cached"] = True
        if req.callback_url:
            background_tasks.add_task(_post_callback, req.callback_url, result)
            return JSONResponse({"status": "queued", "job_id": "cached", "cached": True}, status_code=202)
        return JSONResponse(result)

    if req.callback_url:
        # Async mode: return 202 immediately, do work in background
        job_id = str(uuid.uuid4())[:8]
        log.info("Async research queued: %s job=%s callback=%s", domain, job_id, req.callback_url)
        background_tasks.add_task(_run_research_and_callback, req, job_id)
        return JSONResponse({"status": "queued", "job_id": job_id}, status_code=202)

    # Sync mode: block until complete (backward-compatible)
    result = await _run_research(req)
    return JSONResponse(result)


async def _run_research_and_callback(req: ResearchRequest, job_id: str):
    """Background task: run research then POST result to callback_url."""
    result = await _run_research(req)
    result["job_id"] = job_id
    await _post_callback(req.callback_url, result)


async def _post_callback(callback_url: str, payload: dict):
    """POST research result to n8n webhook resume URL."""
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(callback_url, json=payload)
            if resp.status_code < 400:
                log.info("Callback delivered to %s (status %d)", callback_url, resp.status_code)
                return
            log.warning("Callback HTTP %d — retrying (%d/5)", resp.status_code, attempt + 1)
        except Exception as e:
            log.warning("Callback failed (%d/5): %s", attempt + 1, e)
        await asyncio.sleep(5 * (attempt + 1))
    log.error("All callback attempts failed for %s", callback_url)


async def _run_research(req: ResearchRequest) -> dict:
    """Core research logic — fetch URLs, call LLM, return structured profile."""
    domain = req.domain.lower().strip()
    current_year = datetime.now().year

    log.info("Deep research: %s (%s %s)", domain, req.first_name, req.last_name)

    # ── 1. Build URL candidate list ──────────────────────────────────────────
    candidates = _candidate_urls(domain)

    # ── 2. Fetch pages concurrently in batches ───────────────────────────────
    extracted: list[dict] = []
    MAX_SOURCES = 8
    FETCH_DEADLINE = time.monotonic() + 30  # hard cap: never spend more than 30s fetching

    async with httpx.AsyncClient(timeout=7.0, follow_redirects=True, max_redirects=3) as client:
        for i in range(0, len(candidates), 4):
            if len(extracted) >= MAX_SOURCES or time.monotonic() > FETCH_DEADLINE:
                break
            batch = candidates[i : i + 4]
            tasks = [_fetch_text(client, url) for url in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for url, text in zip(batch, results):
                if isinstance(text, str) and text:
                    extracted.append({"url": url, "text": text[:4000]})
                    log.info("  fetched %d chars from %s", len(text), url)
                    if len(extracted) >= MAX_SOURCES:
                        break

    # ── 3. Build LLM context ─────────────────────────────────────────────────
    if not extracted:
        log.warning("No content extracted for %s — returning empty result", domain)
        result = _empty_result(domain)
        _CACHE[domain] = (time.time(), result)
        return result

    context_parts = [
        f"[PAGE CONTENT: {e['url']}]\n{e['text'][:3500]}"
        for e in extracted
    ]
    full_context = "\n\n".join(context_parts)[:20000]

    # ── 4. LLM structuring ───────────────────────────────────────────────────
    user_prompt = (
        f"Research target domain: {domain}\n"
        f"Contact person: {req.first_name} {req.last_name}\n"
        f"Service they requested from us: {req.service}\n"
        f"Their message to us: {req.message[:600]}\n"
        f"Current year: {current_year}\n\n"
        f"Web content fetched from {len(extracted)} sources:\n{full_context}"
    )

    structured = await _call_llm(user_prompt)
    if structured is None:
        structured = _empty_result(domain)

    # Post-process: compute years_in_business from founded_year
    if structured.get("founded_year") and not structured.get("years_in_business"):
        try:
            structured["years_in_business"] = current_year - int(structured["founded_year"])
        except Exception:
            pass

    structured["sources"] = [e["url"] for e in extracted]
    structured["cached"] = False

    _CACHE[domain] = (time.time(), structured)
    log.info(
        "Research complete for %s: confidence=%s, %d sources",
        domain,
        structured.get("data_confidence", "?"),
        len(extracted),
    )
    return structured


def _candidate_urls(domain: str) -> list[str]:
    """Build an ordered list of URLs to try for company intelligence."""
    company = domain.split(".")[0].lower()
    slugs = list(dict.fromkeys([company, company.replace("_", "-")]))

    urls = []

    # 1. Company's own pages (highest value for firmographic facts)
    for path in ("", "/about", "/about-us", "/company", "/team",
                 "/leadership", "/investors", "/press", "/newsroom"):
        urls.append(f"https://{domain}{path}")

    # 2. LinkedIn company page (public summaries often readable without login)
    for slug in slugs:
        urls.append(f"https://www.linkedin.com/company/{slug}")

    # 3. Crunchbase organization profile
    for slug in slugs:
        urls.append(f"https://www.crunchbase.com/organization/{slug}")

    # 4. Wikipedia
    title_variants = [company.title(), company.upper(), company.replace("-", "_").title()]
    for tv in title_variants[:2]:
        urls.append(f"https://en.wikipedia.org/wiki/{tv}")

    # 5. Bloomberg company profile
    for slug in slugs:
        urls.append(f"https://www.bloomberg.com/profile/company/{slug}")

    # 6. ZoomInfo (public snippets)
    for slug in slugs:
        urls.append(f"https://www.zoominfo.com/c/{slug}/")

    return urls


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch a URL and extract clean text. Returns None if blocked/empty/parked."""
    try:
        resp = await client.get(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        if resp.status_code not in (200, 203):
            return None

        # Detect domain parking / for-sale pages
        raw_lower = resp.text.lower()
        if any(sig in raw_lower for sig in PARKING_SIGNALS):
            log.debug("Parking page detected at %s — skipping", url)
            return None

        text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        if text and len(text) > 150:
            return text
    except Exception as e:
        log.debug("Fetch failed for %s: %s", url, e)
    return None


async def _call_llm(user_prompt: str) -> dict | None:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    f"{LITELLM_BASE_URL}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {LITELLM_API_KEY}",
                        "Cache-Control": "no-store",  # bypass LiteLLM semantic cache
                    },
                    json={
                        "model": RESEARCH_MODEL,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                )
            if resp.status_code == 429 and attempt < 2:
                wait = 15 * (attempt + 1)
                log.warning("LLM 429 — retrying in %ds (attempt %d)", wait, attempt + 1)
                await asyncio.sleep(wait)
                continue
            if resp.status_code in (502, 503, 504) and attempt < 2:
                # LiteLLM restarting — wait and retry
                wait = 20 * (attempt + 1)
                log.warning("LLM %d (likely restarting) — retrying in %ds", resp.status_code, wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
            return json.loads(m.group(1) if m else raw)
        except Exception as e:
            log.error("LLM call failed (attempt %d): %s", attempt + 1, e)
            if attempt < 2:
                await asyncio.sleep(10)
    return None


def _empty_result(domain: str) -> dict:
    return {
        "company_name": None,
        "website": f"https://{domain}",
        "founded_year": None,
        "years_in_business": None,
        "industry": None,
        "sub_industry": None,
        "business_model": None,
        "employee_count": None,
        "annual_revenue": None,
        "funding_stage": None,
        "geographic_footprint": None,
        "key_products_services": [],
        "key_clients_partners": [],
        "leadership": [],
        "recent_projects": [],
        "recent_contracts": [],
        "recent_initiatives": [],
        "recent_news": [],
        "technology_stack": [],
        "pain_points": [],
        "competitive_position": None,
        "growth_signals": [],
        "data_confidence": "low",
        "sources": [],
        "cached": False,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("company_research_server:app", host="0.0.0.0", port=5004, log_level="info")
