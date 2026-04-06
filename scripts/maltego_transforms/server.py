"""Maltego Transform Server - Dual-mode: Maltego XML protocol + clean JSON API"""
import asyncio, json, logging, os, re
import httpx
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, PlainTextResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SHODAN_API_KEY   = os.environ.get("SHODAN_API_KEY", "")
HIBP_API_KEY     = os.environ.get("HIBP_API_KEY", "")
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_API_KEY  = os.environ.get("LITELLM_API_KEY", "")
RESEARCH_URL     = os.environ.get("RESEARCH_URL", "http://sa_company_research:5004")

_SAFE_DOMAIN = re.compile(r'^[a-zA-Z0-9._-]{1,253}$')
_SAFE_IP     = re.compile(r'^[\d.:a-fA-F]{2,45}$')

def _validate_domain(v):
    if not _SAFE_DOMAIN.match(v):
        raise ValueError(f"Invalid domain: {v!r}")
    return v

def _validate_ip(v):
    if not _SAFE_IP.match(v):
        raise ValueError(f"Invalid IP: {v!r}")
    return v

def _xml_escape(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

app = FastAPI(title="Maltego Transform Server", version="1.0.0")
TRANSFORMS = {}

def transform(name, input_type, output_types, description):
    def decorator(fn):
        TRANSFORMS[name] = dict(name=name, input_type=input_type,
                                output_types=output_types, description=description, fn=fn)
        return fn
    return decorator

def _e(etype, value, fields=None):
    return {"type": etype, "value": value, "fields": fields or {}}

async def _get(url, params=None, headers=None, timeout=10.0):
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url, params=params, headers=headers)
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        log.debug("GET %s failed: %s", url, e)
    return None

@transform("DomainToIPAddress","maltego.Domain",["maltego.IPv4Address"],"Resolve domain to IP via DNS")
async def domain_to_ip(value, fields):
    import dns.resolver
    _validate_domain(value)
    out = []
    for rtype, etype in (("A","maltego.IPv4Address"),("AAAA","maltego.IPv6Address")):
        try:
            for rd in dns.resolver.resolve(value, rtype):
                out.append(_e(etype, str(rd)))
        except Exception:
            pass
    return out

@transform("DomainToDNSRecords","maltego.Domain",["maltego.DNSName","maltego.MXRecord"],"Enumerate DNS records")
async def domain_dns(value, fields):
    import dns.resolver
    _validate_domain(value)
    out = []
    for rtype in ("A","AAAA","MX","NS","TXT","CNAME","SOA"):
        try:
            for rd in dns.resolver.resolve(value, rtype):
                etype = "maltego.MXRecord" if rtype == "MX" else "maltego.DNSName"
                out.append(_e(etype, str(rd), {"record_type": rtype}))
        except Exception:
            pass
    return out

@transform("DomainToSubdomains","maltego.Domain",["maltego.Domain"],"Enumerate subdomains via certificate transparency")
async def domain_subdomains(value, fields):
    _validate_domain(value)
    data = await _get("https://crt.sh/", params={"q": f"%.{value}", "output": "json"}, timeout=20.0)
    if not isinstance(data, list):
        return []
    seen, out = set(), []
    for entry in data[:150]:
        for sub in entry.get("name_value","").splitlines():
            sub = sub.strip().lstrip("*.")
            if sub and sub.endswith(value) and sub not in seen:
                seen.add(sub)
                out.append(_e("maltego.Domain", sub))
    return out

@transform("DomainToWhois","maltego.Domain",["maltego.Organization","maltego.EmailAddress","maltego.Phrase"],"WHOIS lookup")
async def domain_whois(value, fields):
    _validate_domain(value)
    out = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "whois", "--", value,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        raw = stdout.decode(errors="replace")
        out.append(_e("maltego.Phrase", "WHOIS output", {"raw": raw[:4000]}))
        for email in set(re.findall(r"[\w.+-]+@[\w.-]+\.\w{2,}", raw)):
            out.append(_e("maltego.EmailAddress", email))
        for line in raw.splitlines():
            if line.lower().startswith("registrant organization:"):
                org = line.split(":",1)[-1].strip()
                if org:
                    out.append(_e("maltego.Organization", org))
                    break
    except Exception as e:
        log.debug("WHOIS %s: %s", value, e)
    return out

@transform("IPToGeolocation","maltego.IPv4Address",["maltego.Location"],"Geolocate an IP address")
async def ip_geo(value, fields):
    _validate_ip(value)
    data = await _get(f"https://ipapi.co/{value}/json/")
    if not data or "error" in data:
        return []
    city, country = data.get("city",""), data.get("country_name","")
    return [_e("maltego.Location", f"{city}, {country}".strip(", "), {
        "latitude": str(data.get("latitude","")), "longitude": str(data.get("longitude","")),
        "country": country, "city": city, "org": data.get("org",""), "asn": data.get("asn",""),
    })]

@transform("IPToASN","maltego.IPv4Address",["maltego.AS"],"Resolve IP to Autonomous System")
async def ip_asn(value, fields):
    _validate_ip(value)
    try:
        from ipwhois import IPWhois
        res = IPWhois(value).lookup_rdap(depth=1)
        asn = res.get("asn","")
        desc = res.get("asn_description","")
        return [_e("maltego.AS", f"AS{asn}", {"description": desc})]
    except Exception as e:
        log.debug("ASN %s: %s", value, e)
    return []

@transform("IPToShodan","maltego.IPv4Address",["maltego.Port","maltego.Banner"],"Shodan host lookup")
async def ip_shodan(value, fields):
    _validate_ip(value)
    if not SHODAN_API_KEY:
        return [_e("maltego.Phrase","SHODAN_API_KEY not configured",{})]
    data = await _get(f"https://api.shodan.io/shodan/host/{value}", params={"key": SHODAN_API_KEY})
    if not data:
        return []
    out = [_e("maltego.Port", str(p)) for p in data.get("ports",[])]
    for item in data.get("data",[])[:10]:
        banner = item.get("data","")[:200].strip()
        if banner:
            out.append(_e("maltego.Banner", banner, {
                "port": str(item.get("port","")), "transport": item.get("transport",""),
                "product": item.get("product",""),
            }))
    return out

@transform("EmailToDomain","maltego.EmailAddress",["maltego.Domain"],"Extract domain from email")
async def email_domain(value, fields):
    if "@" in value:
        return [_e("maltego.Domain", value.split("@",1)[1].strip())]
    return []

@transform("EmailToBreaches","maltego.EmailAddress",["maltego.Phrase"],"Check HaveIBeenPwned for breaches")
async def email_breaches(value, fields):
    if not HIBP_API_KEY:
        return [_e("maltego.Phrase","HIBP_API_KEY not configured",{})]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://haveibeenpwned.com/api/v3/breachedaccount/{value}",
                headers={"hibp-api-key": HIBP_API_KEY, "User-Agent": "MaltegoTransforms/1.0"},
            )
            if r.status_code == 200:
                return [_e("maltego.Phrase", b["Name"],
                           {"domain": b.get("Domain",""), "date": b.get("BreachDate","")})
                        for b in r.json()]
            if r.status_code == 404:
                return [_e("maltego.Phrase","No breaches found",{})]
    except Exception as e:
        log.debug("HIBP %s: %s", value, e)
    return []

@transform("DomainToCompanyProfile","maltego.Domain",["maltego.Organization","maltego.Person","maltego.Phrase"],
           "Enrich domain with company intelligence")
async def domain_company(value, fields):
    _validate_domain(value)
    try:
        async with httpx.AsyncClient(timeout=90.0) as c:
            r = await c.post(f"{RESEARCH_URL}/research", json={"domain": value})
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception as e:
        log.debug("Company research %s: %s", value, e)
        return []
    out = []
    if data.get("company_name"):
        out.append(_e("maltego.Organization", data["company_name"], {
            "industry": data.get("industry",""), "employees": data.get("employee_count",""),
            "revenue": data.get("annual_revenue",""), "hq": data.get("geographic_footprint",""),
        }))
    for leader in data.get("leadership",[])[:5]:
        out.append(_e("maltego.Person", leader))
    for news in data.get("recent_news",[])[:3]:
        out.append(_e("maltego.Phrase", news, {"category":"news"}))
    for pain in data.get("pain_points",[])[:3]:
        out.append(_e("maltego.Phrase", pain, {"category":"pain_point"}))
    return out

@app.get("/health")
def health():
    return {"status": "ok", "transforms": len(TRANSFORMS)}

@app.get("/api/transforms")
def list_transforms():
    return [{"name":t["name"],"input_type":t["input_type"],
             "output_types":t["output_types"],"description":t["description"]}
            for t in TRANSFORMS.values()]

@app.post("/api/{transform_name}")
async def api_run(transform_name: str, body: dict = Body(...)):
    t = TRANSFORMS.get(transform_name)
    if not t:
        return JSONResponse({"error": f"Unknown: {transform_name}"}, status_code=404)
    try:
        results = await t["fn"](body.get("value",""), body.get("fields",{}))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.error("Transform %s: %s", transform_name, e)
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"transform": transform_name, "input": body.get("value",""),
            "count": len(results), "results": results}

@app.post("/run/{transform_name}")
async def maltego_run(transform_name: str, request: Request):
    t = TRANSFORMS.get(transform_name)
    if not t:
        return PlainTextResponse(f"Transform not found: {transform_name}", status_code=404)
    raw = await request.body()
    value, ffields = "", {}
    if raw[:1] == b"<":
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(raw)
            for tag in (".//{http://www.paterva.com/xml/v3}Value", ".//Value"):
                el = root.find(tag)
                if el is not None:
                    value = el.text or ""
                    break
        except Exception:
            pass
    else:
        try:
            bd = json.loads(raw)
            value, ffields = bd.get("value",""), bd.get("fields",{})
        except Exception:
            pass
    try:
        results = await t["fn"](value, ffields)
    except Exception as e:
        log.error("Transform %s: %s", transform_name, e)
        results = []
    entities = "".join(
        f'<Entity Type="{r["type"]}"><Value>{_xml_escape(r["value"])}</Value>'
        f'<AdditionalFields>'
        + "".join(f'<Field Name="{k}" DisplayName="{k}">{_xml_escape(v)}</Field>'
                  for k,v in r.get("fields",{}).items())
        + f'</AdditionalFields></Entity>'
        for r in results
    )
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><MaltegoMessage>'
           f'<MaltegoTransformResponseMessage><Entities>{entities}</Entities>'
           f'<UIMessages/></MaltegoTransformResponseMessage></MaltegoMessage>')
    return PlainTextResponse(xml, media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8080, log_level="info")
