"""
company_research_server.py — Company Research + Lead Processing Service

Endpoints:
  POST /research      — company research only (backward-compatible, used by v1 workflow)
  POST /process-lead  — research + analysis + draft in one call (used by v2 workflow)
  GET  /health        — liveness probe
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

LITELLM_BASE_URL   = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_API_KEY    = os.environ.get("LITELLM_API_KEY", "")
RESEARCH_MODEL     = os.environ.get("RESEARCH_STRUCTURING_MODEL", "cloud/smart")
LEAD_PIPELINE_MODEL = os.environ.get("LEAD_PIPELINE_MODEL", "cloud/fast")
N8N_PUBLIC_BASE    = os.environ.get("N8N_PUBLIC_BASE", "https://sovereignadvisory.ai/n8n")
N8N_INTERNAL_BASE  = os.environ.get("N8N_INTERNAL_BASE", "http://n8n:5678")

_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 3600

PARKING_SIGNALS = [
    "hugedomains.com", "godaddy.com/parking", "domain for sale",
    "buy this domain", "sedoparking.com", "this domain is parked",
    "namecheap.com parking", "afternic.com",
]

# ── Prompts ──────────────────────────────────────────────────────────────────

RESEARCH_SYSTEM_PROMPT = """You are a senior business intelligence analyst producing a company profile \
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

ANALYSIS_SYSTEM_PROMPT = """You are a senior business advisor at Sovereign Advisory. \
Produce a strategic intelligence brief to guide how our CEO should engage with this inbound prospect. \
RULES: \
(1) Use the company research profile to ground your analysis in specific facts. \
(2) The summary MUST connect what the prospect is asking for to specific facts about their company — explain the WHY. \
(3) Base the engagement strategy on the prospect's OWN WORDS as the primary signal of intent. \
(4) Do NOT hallucinate facts not present in either the message or the research profile. \
Return ONLY a valid JSON object with EXACTLY these fields and types — no preamble, no markdown, no extra keys: \
{"summary": "<string: 3 paragraphs joined with \\n\\n. \
Para 1 — Their request in context: What are they asking for, and what does their company profile \
(industry, size, stage, tech stack) tell us about WHY they are asking this now? \
Connect their stated need to specific company facts. \
Para 2 — The underlying problem: What operational or strategic pressure is most likely driving this \
inquiry, given their initiatives, pain points, and competitive position? Name the specific pressure. \
Para 3 — Our opportunity: What makes this a strong or weak fit for Sovereign Advisory, and what is \
the sharpest angle to lead with? Reference their exact words where possible. \
NOT an object, a plain string>", \
"approach": "<string: 1-2 paragraphs on recommended engagement tone and strategy — NOT an object, a plain string>", \
"conversation_starters": ["<string>", "<string>", "<string>"], \
"questions": ["<string>", "<string>", "<string>"], \
"scenarios": ["<string>", "<string>", "<string>"]} \
ALL fields are required. summary and approach MUST be plain strings — never objects or arrays. \
conversation_starters, questions, and scenarios MUST be arrays of exactly 3 plain strings each."""

DRAFT_SYSTEM_PROMPT = """You are writing a personal reply on behalf of Robert Elder, CEO of Sovereign Advisory, \
responding to someone who submitted a contact inquiry on the Sovereign Advisory website. \
This person reached out to us — this is NOT cold outreach. \
The email must: open by acknowledging their specific inquiry (not a generic greeting), \
show we read and understood their message and situation, \
briefly position how we can help with their stated need, \
and close with a clear next step (offer a brief call). \
Tone: warm, direct, peer-to-peer — not salesy. \
Return ONLY a valid JSON object — no preamble, no markdown. \
Fields: subject (personal reply feel, not marketing), \
body_text (plain text, 3-4 short paragraphs, no HTML, no bullets). Do NOT include any closing, sign-off, or signature line — a signature is appended automatically)."""

EMAIL_SIGNATURE = (
    "\n\nBest regards,\n\n"
    "Robert Elder\n"
    "Founder & CEO, Sovereign Advisory\n"
    "relder@sovereignadvisory.ai\n"
    "sovereignadvisory.ai"
)


# ── Request models ────────────────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    domain: str
    first_name: str = ""
    last_name: str = ""
    service: str = ""
    message: str = ""
    callback_url: str = ""  # legacy async mode support


class ProcessLeadRequest(BaseModel):
    domain: str
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    service: str = ""
    message: str = ""


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Company Research & Lead Processing Service")


@app.get("/health")
def health():
    return {"status": "ok"}


# ── v1 endpoint (backward-compatible) ────────────────────────────────────────

@app.post("/research")
async def research(req: ResearchRequest, background_tasks: BackgroundTasks):
    domain = req.domain.lower().strip()

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
        job_id = str(uuid.uuid4())[:8]
        log.info("Async research queued: %s job=%s", domain, job_id)
        background_tasks.add_task(_run_research_and_callback, req, job_id)
        return JSONResponse({"status": "queued", "job_id": job_id}, status_code=202)

    result = await _run_research(req)
    return JSONResponse(result)


# ── v2 endpoint: research + analysis + draft in one call ─────────────────────

@app.post("/process-lead")
async def process_lead(req: ProcessLeadRequest):
    """
    Full lead processing pipeline:
      1. Company research (web scraping + LLM structuring)
      2. Strategic analysis (engagement brief for CEO)
      3. Personalised email draft

    Returns a single flat JSON payload ready for n8n to save and notify.
    Typically completes in 15-60s depending on the company's web footprint.
    """
    domain = req.domain.lower().strip()
    log.info("Processing lead: %s (%s %s)", domain, req.first_name, req.last_name)

    # Run all three steps; each has its own retry logic
    research  = await _run_research(req)
    analysis  = await _run_analysis(req, research)
    draft     = await _run_draft(req, analysis)

    log.info(
        "Lead processing complete: %s | confidence=%s | analysis=%s | draft=%s",
        domain,
        research.get("data_confidence", "?"),
        "ok" if analysis.get("summary") else "empty",
        "ok" if draft.get("subject") else "empty",
    )

    return {
        # Research fields (all 22)
        **research,
        # Analysis fields
        "summary":               analysis.get("summary", ""),
        "approach":              analysis.get("approach", ""),
        "conversation_starters": analysis.get("conversation_starters", []),
        "questions":             analysis.get("questions", []),
        "scenarios":             analysis.get("scenarios", []),
        # Draft (signature appended here so n8n doesn't need to)
        "draft_subject": draft.get("subject", "Following up on your inquiry"),
        "draft_body":    (draft.get("body_text") or "") + EMAIL_SIGNATURE,
    }


# ── Core processing functions ─────────────────────────────────────────────────

async def _run_research_and_callback(req: ResearchRequest, job_id: str):
    result = await _run_research(req)
    result["job_id"] = job_id
    await _post_callback(req.callback_url, result)


async def _post_callback(callback_url: str, payload: dict):
    if N8N_PUBLIC_BASE and callback_url.startswith(N8N_PUBLIC_BASE):
        callback_url = N8N_INTERNAL_BASE + callback_url[len(N8N_PUBLIC_BASE):]
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(callback_url, json=payload)
            if resp.status_code < 400:
                return
            log.warning("Callback HTTP %d — retrying (%d/5)", resp.status_code, attempt + 1)
        except Exception as e:
            log.warning("Callback failed (%d/5): %s", attempt + 1, e)
        await asyncio.sleep(5 * (attempt + 1))
    log.error("All callback attempts failed for %s", callback_url)


async def _search_web(domain: str, req) -> str:
    """Query cloud/search (web-search-enabled LLM) for live company intelligence."""
    company_hint = domain.split(".")[0].replace("-", " ").replace("_", " ")
    query = (
        f"Research the company at domain {domain} (likely named something like '{company_hint}'). "
        f"Find and report: full legal company name, parent company or ownership structure, "
        f"approximate annual revenue, total employee count, year founded, headquarters location, "
        f"all major products and services, notable clients or government contracts, "
        f"executive leadership names and titles, recent news or announcements from the last 2 years, "
        f"technology stack or platforms used, funding stage or stock ticker if public, "
        f"and their competitive position in their market. "
        f"Be specific — use real numbers, named contracts, and named executives."
    )
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                f"{LITELLM_BASE_URL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {LITELLM_API_KEY}",
                    "Cache-Control": "no-cache, no-store",
                },
                json={
                    "model": "cloud/search",
                    "messages": [{"role": "user", "content": query}],
                    "max_tokens": 2000,
                    "cache": {"no-cache": True, "no-store": True},
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            log.info("cloud/search: %d chars for %s", len(content), domain)
            return f"[LIVE WEB SEARCH — {domain}]\n{content}"
    except Exception as e:
        log.warning("cloud/search failed for %s: %s", domain, e)
        return ""


async def _run_research(req) -> dict:
    domain = req.domain.lower().strip()
    current_year = datetime.now().year
    log.info("Research: %s", domain)

    # Run web search (cloud/search) and URL scraping concurrently
    candidates = _candidate_urls(domain)
    FETCH_DEADLINE = time.monotonic() + 30

    async def scrape_all() -> list[dict]:
        found: list[dict] = []
        async with httpx.AsyncClient(timeout=7.0, follow_redirects=True, max_redirects=3) as client:
            for i in range(0, len(candidates), 4):
                if len(found) >= 8 or time.monotonic() > FETCH_DEADLINE:
                    break
                results = await asyncio.gather(
                    *[_fetch_text(client, url) for url in candidates[i:i+4]],
                    return_exceptions=True,
                )
                for url, text in zip(candidates[i:i+4], results):
                    if isinstance(text, str) and text:
                        found.append({"url": url, "text": text[:4000]})
                        log.info("  fetched %d chars from %s", len(text), url)
                        if len(found) >= 8:
                            break
        return found

    extracted, search_text = await asyncio.gather(
        scrape_all(),
        _search_web(domain, req),
    )

    # Build combined context: web search first (highest quality), then scraped pages
    context_parts = []
    if search_text:
        context_parts.append(search_text)
    for e in extracted:
        context_parts.append(f"[PAGE CONTENT: {e['url']}]\n{e['text'][:3000]}")
    full_context = "\n\n".join(context_parts)[:24000]

    if not full_context.strip():
        log.warning("No content at all for %s", domain)
        structured = _empty_result(domain)
        structured["cached"] = False
        _CACHE[domain] = (time.time(), structured)
        return structured

    source_desc = (
        f"{'live web search + ' if search_text else ''}"
        f"{len(extracted)} scraped page(s)" if extracted else "live web search only"
    )
    user_prompt = (
        f"Research target domain: {domain}\n"
        f"Contact person: {req.first_name} {req.last_name}\n"
        f"Service they requested: {req.service}\n"
        f"Their message: {req.message[:600]}\n"
        f"Current year: {current_year}\n\n"
        f"Intelligence gathered from {source_desc}:\n\n{full_context}"
    )

    structured = await _call_llm(RESEARCH_MODEL, RESEARCH_SYSTEM_PROMPT, user_prompt)
    if structured is None:
        structured = _empty_result(domain)

    if structured.get("founded_year") and not structured.get("years_in_business"):
        try:
            structured["years_in_business"] = current_year - int(structured["founded_year"])
        except Exception:
            pass

    structured["sources"] = (["cloud/search"] if search_text else []) + [e["url"] for e in extracted]
    structured["cached"] = False
    _CACHE[domain] = (time.time(), structured)
    log.info("Research done: %s | %s | sources: %s", domain, structured.get("data_confidence"), len(structured["sources"]))
    return structured


async def _run_analysis(req: ProcessLeadRequest, research: dict) -> dict:
    """Strategic lead analysis — grounds recommendations in research + prospect's own words."""
    user_content = (
        f"Inbound inquiry:\n"
        f"Name: {req.first_name} {req.last_name}\n"
        f"Company domain: {req.domain}\n"
        f"Service requested: {req.service}\n"
        f"Their message (verbatim):\n\"{req.message[:800]}\"\n\n"
        f"Company intelligence profile:\n{json.dumps(research)}"
    )
    result = await _call_llm(LEAD_PIPELINE_MODEL, ANALYSIS_SYSTEM_PROMPT, user_content)
    if result is None:
        log.warning("Analysis LLM failed for %s — returning empty", req.domain)
        return {"summary": "", "approach": "", "conversation_starters": [], "questions": [], "scenarios": []}
    return result


async def _run_draft(req: ProcessLeadRequest, analysis: dict) -> dict:
    """Generate personalised outreach email from CEO based on analysis."""
    user_content = (
        f"This person submitted a contact form requesting our services.\n\n"
        f"Name: {req.first_name} {req.last_name}\n"
        f"Company domain: {req.domain}\n"
        f"Service inquired about: {req.service}\n"
        f"What they wrote:\n\"{req.message[:800]}\"\n\n"
        f"Strategic context (personalise from this — do not reference it explicitly):\n"
        f"{json.dumps(analysis)}"
    )
    result = await _call_llm(LEAD_PIPELINE_MODEL, DRAFT_SYSTEM_PROMPT, user_content)
    if result is None:
        log.warning("Draft LLM failed for %s — returning empty", req.domain)
        return {"subject": "Following up on your inquiry", "body_text": ""}
    return result


def _candidate_urls(domain: str) -> list[str]:
    company = domain.split(".")[0].lower()
    company_spaced = company.replace("-", " ").replace("_", " ")
    slugs = list(dict.fromkeys([company, company.replace("_", "-")]))
    urls = []

    # 1. Company's own site — most authoritative for facts
    for path in ("", "/about", "/about-us", "/company", "/team",
                 "/leadership", "/investors", "/press", "/newsroom"):
        urls.append(f"https://{domain}{path}")

    # 2. LinkedIn public company page
    for slug in slugs:
        urls.append(f"https://www.linkedin.com/company/{slug}")

    # 3. Wikipedia — use search API first, then direct guesses
    urls.append(
        f"https://en.wikipedia.org/w/api.php?action=query&list=search"
        f"&srsearch={company_spaced}&srlimit=1&srprop=snippet&format=json"
    )
    for tv in [company_spaced.title(), company.title(), company.upper()]:
        urls.append(f"https://en.wikipedia.org/wiki/{tv.replace(' ', '_')}")

    # 4. Crunchbase
    for slug in slugs:
        urls.append(f"https://www.crunchbase.com/organization/{slug}")

    # 5. OpenCorporates — public company registry aggregator
    urls.append(f"https://opencorporates.com/companies?q={company_spaced}&utf8=%E2%9C%93")

    # 6. Better Business Bureau
    urls.append(f"https://www.bbb.org/search?find_text={company_spaced}")

    # 7. Bloomberg & ZoomInfo
    for slug in slugs:
        urls.append(f"https://www.bloomberg.com/profile/company/{slug}")
    for slug in slugs:
        urls.append(f"https://www.zoominfo.com/c/{slug}/")

    return urls


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str | None:
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
        if any(sig in resp.text.lower() for sig in PARKING_SIGNALS):
            return None
        text = trafilatura.extract(resp.text, include_comments=False, include_tables=True, no_fallback=False)
        if text and len(text) > 150:
            return text
    except Exception as e:
        log.debug("Fetch failed %s: %s", url, e)
    return None


async def _call_llm(model: str, system_prompt: str, user_content: str) -> dict | None:
    """Call LiteLLM with retry logic. Returns parsed JSON dict or None on failure."""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    f"{LITELLM_BASE_URL}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {LITELLM_API_KEY}",
                        # no-cache = skip reading from cache; no-store = don't write to cache
                        "Cache-Control": "no-cache, no-store",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": user_content},
                        ],
                        "response_format": {"type": "json_object"},
                        # LiteLLM body-level cache bypass (works regardless of proxy header config)
                        "cache": {"no-cache": True, "no-store": True},
                    },
                )
            if resp.status_code in (429, 502, 503, 504) and attempt < 2:
                wait = 15 * (attempt + 1)
                log.warning("LLM %d — retrying in %ds", resp.status_code, wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
            return json.loads(m.group(1) if m else raw)
        except Exception as e:
            log.error("LLM call failed (attempt %d/%d): %s", attempt + 1, 3, e)
            if attempt < 2:
                await asyncio.sleep(10)
    return None


def _empty_result(domain: str) -> dict:
    return {
        "company_name": None, "website": f"https://{domain}",
        "founded_year": None, "years_in_business": None,
        "industry": None, "sub_industry": None, "business_model": None,
        "employee_count": None, "annual_revenue": None, "funding_stage": None,
        "geographic_footprint": None,
        "key_products_services": [], "key_clients_partners": [], "leadership": [],
        "recent_projects": [], "recent_contracts": [], "recent_initiatives": [],
        "recent_news": [], "technology_stack": [], "pain_points": [],
        "competitive_position": None, "growth_signals": [],
        "data_confidence": "low", "sources": [], "cached": False,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("company_research_server:app", host="0.0.0.0", port=5004, log_level="info")
