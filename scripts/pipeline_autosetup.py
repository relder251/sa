#!/usr/bin/env python3
"""
pipeline_autosetup.py — SA Lead Pipeline Automated Setup
=========================================================
Automates ~78% of the pipeline setup.  Pauses at exactly 4 points that
require human interaction (Telegram, Twilio, Neo.space SMTP, Notion).

Run from the Agentic_SDLC directory:
    python scripts/pipeline_autosetup.py [--vps-host sovereignadvisory.ai] [--skip-deploy]

Flags:
    --vps-host <host>     VPS hostname or IP            (default: sovereignadvisory.ai)
    --vps-user <user>     SSH user                      (default: root)
    --vps-port <port>     SSH port                      (default: 22)
    --ssh-key  <path>     SSH private key               (default: ~/.ssh/id_rsa)
    --skip-deploy         Skip VPS sync + container ops
    --skip-notion         Skip Notion DB creation
    --resume-from <stage> Resume from a specific stage (see STAGES list)
    --dry-run             Print actions, make no changes

Stages (for --resume-from):
    env | schema | notion | n8n-workflows | n8n-credentials |
    n8n-variables | webhook-url | deploy | verify
"""

import argparse
import json
import os
import re
import secrets
import string
import subprocess
import sys
import time
from pathlib import Path

import httpx

# ── Paths ───────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
ROOT_DIR    = SCRIPT_DIR.parent
ENV_FILE    = ROOT_DIR / ".env"
INDEX_HTML  = ROOT_DIR.parent / "sovereign_advisory" / "index.html"
DEPLOY_SH   = ROOT_DIR.parent / "deploy_to_vps.sh"
SA_DEPLOY   = ROOT_DIR.parent / "sovereign_advisory" / "deploy"

# ── Colour helpers ──────────────────────────────────────────────────────────────
R = "\033[0;31m"; G = "\033[0;32m"; Y = "\033[0;33m"
B = "\033[0;34m"; C = "\033[0;36m"; W = "\033[1;37m"; N = "\033[0m"

def ok(msg):    print(f"  {G}✓{N}  {msg}")
def info(msg):  print(f"  {B}→{N}  {msg}")
def warn(msg):  print(f"  {Y}⚠{N}  {msg}")
def err(msg):   print(f"  {R}✗{N}  {msg}"); sys.exit(1)
def step(msg):  print(f"\n{W}{msg}{N}")
def hline():    print("─" * 70)


# ── .env helpers ────────────────────────────────────────────────────────────────

def read_env(path: Path) -> dict:
    """Read .env into a dict (preserves comments for rewrite)."""
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().split("#")[0].strip()
    return env


def set_env(path: Path, updates: dict, dry_run=False):
    """
    Update or append key=value pairs in .env.
    Preserves all existing lines (including comments).
    """
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()

    for key, value in updates.items():
        # Try to find existing line (with or without value)
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
        new_line = f"{key}={value}"
        if pattern.search(text):
            text = pattern.sub(new_line, text)
        else:
            text += f"\n{new_line}"

    if dry_run:
        for k, v in updates.items():
            info(f"[dry-run] Would set {k}={v[:8]}..." if len(v) > 8 else f"[dry-run] Would set {k}={v}")
        return

    path.write_text(text)


def get_env_value(key: str) -> str:
    """Get a value from the .env file."""
    return read_env(ENV_FILE).get(key, "")


def gen_password(length=32) -> str:
    """Generate a URL-safe random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ── n8n API helpers ─────────────────────────────────────────────────────────────

def n8n_headers(api_key: str) -> dict:
    return {"X-N8N-API-KEY": api_key, "Content-Type": "application/json"}


def n8n_list_credentials(base_url: str, api_key: str) -> list:
    resp = httpx.get(f"{base_url}/api/v1/credentials", headers=n8n_headers(api_key), timeout=10)
    resp.raise_for_status()
    return resp.json().get("data", [])


def n8n_create_credential(base_url: str, api_key: str, name: str, cred_type: str, data: dict) -> str:
    """Create a credential if it doesn't exist. Returns the credential ID."""
    existing = n8n_list_credentials(base_url, api_key)
    match = next((c for c in existing if c["name"] == name), None)
    if match:
        ok(f"Credential already exists: {name} [{match['id']}]")
        return match["id"]

    resp = httpx.post(
        f"{base_url}/api/v1/credentials",
        headers=n8n_headers(api_key),
        json={"name": name, "type": cred_type, "data": data},
        timeout=10,
    )
    resp.raise_for_status()
    cred_id = resp.json()["id"]
    ok(f"Created credential: {name} [{cred_id}]")
    return cred_id


def n8n_list_variables(base_url: str, api_key: str) -> dict:
    """Return {key: id} map of existing variables."""
    resp = httpx.get(f"{base_url}/api/v1/variables", headers=n8n_headers(api_key), timeout=10)
    if resp.status_code == 404:
        return {}   # older n8n without variables endpoint
    resp.raise_for_status()
    return {v["key"]: v["id"] for v in resp.json().get("data", [])}


def n8n_set_variable(base_url: str, api_key: str, key: str, value: str, existing: dict):
    if not value:
        warn(f"Skipping empty variable: {key}")
        return
    if key in existing:
        # Update
        httpx.patch(
            f"{base_url}/api/v1/variables/{existing[key]}",
            headers=n8n_headers(api_key),
            json={"value": value},
            timeout=10,
        ).raise_for_status()
        ok(f"Updated n8n variable: {key}")
    else:
        httpx.post(
            f"{base_url}/api/v1/variables",
            headers=n8n_headers(api_key),
            json={"key": key, "value": value},
            timeout=10,
        ).raise_for_status()
        ok(f"Created n8n variable: {key}")


def n8n_get_workflow_id(base_url: str, api_key: str, name: str) -> str | None:
    resp = httpx.get(f"{base_url}/api/v1/workflows", headers=n8n_headers(api_key), timeout=10)
    resp.raise_for_status()
    match = next((w for w in resp.json().get("data", []) if w["name"] == name), None)
    return match["id"] if match else None


def n8n_activate_workflow(base_url: str, api_key: str, wf_id: str):
    httpx.patch(
        f"{base_url}/api/v1/workflows/{wf_id}",
        headers=n8n_headers(api_key),
        json={"active": True},
        timeout=10,
    ).raise_for_status()


# ── Pause prompts ───────────────────────────────────────────────────────────────

def pause(title: str, instructions: list[str], inputs: list[tuple[str, str]]) -> dict:
    """
    Pause for manual user action.
    instructions: list of instruction strings
    inputs: list of (env_key, prompt_label) tuples
    Returns dict of {env_key: value}
    """
    print(f"\n{'═'*70}")
    print(f"{Y}  ⏸  MANUAL STEP REQUIRED: {title}{N}")
    print(f"{'═'*70}")
    for line in instructions:
        print(f"  {line}")
    print()

    results = {}
    for key, label in inputs:
        existing = get_env_value(key)
        if existing:
            ok(f"{key} already set — using existing value")
            results[key] = existing
            continue
        while True:
            val = input(f"  {W}{label}:{N} ").strip()
            if val:
                results[key] = val
                break
            print("  (required — cannot be empty)")

    # Always persist credentials even in dry-run — they're needed for subsequent stages.
    # Dry-run only skips destructive/remote operations, not credential storage.
    set_env(ENV_FILE, results, dry_run=False)

    return results


# ── VPS SSH helpers ─────────────────────────────────────────────────────────────

# Module-level ControlMaster socket — established once in stage_deploy, reused everywhere
_ssh_socket: str | None = None

def _ssh_base_opts(host: str, user: str, port: str, key: str | None) -> list[str]:
    """Common SSH options including ControlMaster reuse if socket exists."""
    opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        "-p", str(port),
    ]
    if _ssh_socket:
        opts += ["-o", f"ControlPath={_ssh_socket}", "-o", "ControlMaster=no"]
    if key and Path(key).exists():
        opts += ["-i", key]
    return opts


def ssh_establish_master(host: str, user: str, port: str, key: str | None):
    """Open a ControlMaster socket (called once; subsequent ssh_run calls reuse it)."""
    global _ssh_socket
    import tempfile
    _ssh_socket = tempfile.mktemp(prefix="/tmp/sa_ssh_")
    key_args = ["-i", key] if key and Path(key).exists() else []
    subprocess.run([
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ControlMaster=yes",
        "-o", f"ControlPath={_ssh_socket}",
        "-o", "ControlPersist=yes",
        "-o", "ConnectTimeout=20",
        "-p", str(port),
        *key_args,
        f"{user}@{host}",
        "echo '[ControlMaster established]'",
    ], capture_output=False)


def ssh_close_master(host: str, user: str, port: str):
    """Close the ControlMaster socket."""
    if _ssh_socket:
        subprocess.run([
            "ssh", "-O", "exit",
            "-o", f"ControlPath={_ssh_socket}",
            f"{user}@{host}",
        ], capture_output=True)


def ssh_run(host: str, user: str, port: str, key: str | None, cmd: str, check=True) -> int:
    full_cmd = ["ssh", *_ssh_base_opts(host, user, port, key), f"{user}@{host}", cmd]
    result = subprocess.run(full_cmd, capture_output=False)
    if check and result.returncode != 0:
        warn(f"SSH command failed (exit {result.returncode})")
    return result.returncode


# ══════════════════════════════════════════════════════════════════════════════
# STAGES
# ══════════════════════════════════════════════════════════════════════════════

STAGE_ORDER = [
    "env", "schema", "telegram", "twilio", "neosmtp", "notion",
    "n8n-workflows", "n8n-credentials", "n8n-variables",
    "webhook-url", "deploy", "verify",
]


def stage_env(args, dry_run):
    step("STAGE 1/12 — Generate & inject .env values")

    env = read_env(ENV_FILE)
    updates = {}

    # ── Passwords ──────────────────────────────────────────────────────────
    if not env.get("LEAD_REVIEW_PASSWORD"):
        pwd = gen_password(32)
        updates["LEAD_REVIEW_PASSWORD"] = pwd
        ok(f"Generated LEAD_REVIEW_PASSWORD: {pwd[:6]}{'*'*20}")
    else:
        ok("LEAD_REVIEW_PASSWORD already set — unchanged")

    # ── Fix DATABASE_URL ───────────────────────────────────────────────────
    current_db = env.get("DATABASE_URL", "")
    if "user:pass" in current_db or "localhost" in current_db:
        updates["DATABASE_URL"] = "postgresql://litellm:litellm_password@postgres:5432/litellm"
        ok("Fixed DATABASE_URL → postgres container")
    else:
        ok(f"DATABASE_URL already set: {current_db[:40]}...")

    # ── Static values ──────────────────────────────────────────────────────
    static = {
        "N8N_BASE_URL":        "http://n8n:5678",
        "LITELLM_BASE_URL":    "http://litellm:4000",
        "LITELLM_API_KEY":     env.get("LITELLM_API_KEY", "sk-vibe-coding-key-123"),
        "LEAD_REVIEW_BASE_URL":"https://sovereignadvisory.ai/review",
        "NOTIFY_EMAIL":        "relder@sovereignadvisory.ai",
        "NOTIFY_SMS_EMAIL":    "+12768805651@vtext.com",
        "NEO_SMTP_HOST":       "smtp.neo.space",
        "NEO_SMTP_PORT":       "587",
        "NEO_SMTP_USER":       "relder@sovereignadvisory.ai",
        "TWILIO_WHATSAPP_FROM":"whatsapp:+14155238886",
        "WHATSAPP_NOTIFY_TO":  "whatsapp:+12768805651",
    }
    for k, v in static.items():
        if not env.get(k):
            updates[k] = v
            ok(f"Set {k}")
        else:
            ok(f"{k} already set — unchanged")

    if updates and not dry_run:
        set_env(ENV_FILE, updates)
        ok(f"Wrote {len(updates)} values to {ENV_FILE}")
    elif dry_run:
        info("[dry-run] Would update .env")


def stage_schema(args, dry_run):
    step("STAGE 2/12 — Apply PostgreSQL schema")

    schema_file = ROOT_DIR / "sql" / "leads_schema.sql"
    if not schema_file.exists():
        err(f"Schema file not found: {schema_file}")
        return

    # Check if tables already exist
    check = subprocess.run(
        ["docker", "exec", "litellm_db", "psql", "-U", "litellm", "-d", "litellm",
         "-c", "SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'sa_%';"],
        capture_output=True, text=True,
    )
    if check.returncode == 0:
        count = int([l.strip() for l in check.stdout.splitlines() if l.strip().lstrip("-").isdigit()][0])
        if count >= 4:
            ok(f"Schema already applied ({count} SA tables found)")
            return

    if dry_run:
        info("[dry-run] Would apply schema via docker exec litellm_db psql")
        return

    # Apply via docker exec — no network dependency, no Python deps needed
    result = subprocess.run(
        ["docker", "exec", "-i", "litellm_db", "psql", "-U", "litellm", "-d", "litellm"],
        stdin=open(schema_file),
        capture_output=False,
    )
    if result.returncode != 0:
        warn("Schema application had errors — check output above")
    else:
        ok("Schema applied (4 SA tables created)")


def stage_telegram(args, dry_run):
    step("STAGE 3/12 — Telegram Bot  [MANUAL — ~3 minutes]")
    env = read_env(ENV_FILE)

    token_set = bool(env.get("TELEGRAM_BOT_TOKEN"))
    chat_set  = bool(env.get("TELEGRAM_CHAT_ID"))

    if token_set and chat_set:
        ok("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID already set — skipping")
        return

    # Get the token first so we can auto-detect the chat ID
    token_existing = get_env_value("TELEGRAM_BOT_TOKEN")
    chat_existing  = get_env_value("TELEGRAM_CHAT_ID")

    if not token_existing:
        pause(
            title="Create Telegram Bot via @BotFather",
            instructions=[
                "1. Open Telegram → search @BotFather (verified, blue tick)",
                "2. Send:  /newbot",
                "3. Name:      SA Notifications",
                "4. Username:  sa_notifications_bot  (or any available name ending in 'bot')",
                "5. Copy the token BotFather gives you (format: 1234567890:AAH...)",
                "6. Open your new bot in Telegram and send:  /start",
                "   (This is REQUIRED before the bot can message you)",
            ],
            inputs=[("TELEGRAM_BOT_TOKEN", "Telegram bot token")],
        )
        token_existing = get_env_value("TELEGRAM_BOT_TOKEN")

    token = token_existing or ""

    # Auto-detect chat ID from getUpdates
    if token and not chat_existing:
        info("Auto-detecting Telegram chat ID from getUpdates...")
        info("Make sure you sent '/start' to the bot in Telegram first.")
        try:
            resp = httpx.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                timeout=10,
            )
            updates = resp.json().get("result", [])
            if updates:
                chat_id = str(updates[-1]["message"]["chat"]["id"])
                ok(f"Auto-detected chat ID: {chat_id}")
                set_env(ENV_FILE, {"TELEGRAM_CHAT_ID": chat_id})
                chat_existing = chat_id
            else:
                warn("No updates found — the bot has received no messages yet.")
                warn("Open the bot in Telegram, send '/start', then re-run.")
                chat_existing = input(f"  {W}Or paste chat ID manually (press Enter to skip):{N} ").strip()
                if chat_existing:
                    set_env(ENV_FILE, {"TELEGRAM_CHAT_ID": chat_existing})
        except Exception as e:
            warn(f"getUpdates failed: {e}")
            chat_existing = input(f"  {W}Paste chat ID manually (press Enter to skip):{N} ").strip()
            if chat_existing:
                set_env(ENV_FILE, {"TELEGRAM_CHAT_ID": chat_existing})

    chat = get_env_value("TELEGRAM_CHAT_ID") or ""

    if token and chat:
        info("Testing Telegram bot...")
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": "SA Notifications bot is live ✓ — pipeline setup in progress"},
                timeout=10,
            )
            if resp.status_code == 200:
                ok("Telegram test message sent successfully")
            else:
                warn(f"Telegram test failed: {resp.text[:200]}")
                warn("Most common fix: open the bot in Telegram and send '/start', then re-run with --resume-from telegram")
        except Exception as e:
            warn(f"Telegram test error: {e}")


def stage_twilio(args, dry_run):
    step("STAGE 4/12 — Twilio  [MANUAL — ~5 minutes if no account]")
    env = read_env(ENV_FILE)

    if env.get("TWILIO_ACCOUNT_SID") and env.get("TWILIO_AUTH_TOKEN"):
        ok("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN already set — skipping")
        return

    vals = pause(
        title="Twilio Account + WhatsApp Sandbox",
        instructions=[
            "1. Sign up at https://twilio.com (free, ~$15 trial credit)",
            "2. From the Console Dashboard copy:",
            "   - Account SID  (starts with AC...)",
            "   - Auth Token   (click the eye icon to reveal)",
            "",
            "3. WhatsApp Sandbox — from your phone (+12768805651):",
            "   - Open WhatsApp → new message to: +1 415 523 8886",
            "   - Send:  join <keyword>   (keyword shown on Twilio console under",
            "     Develop → Messaging → Try it out → Send a WhatsApp message)",
            "   - Twilio will reply confirming you joined",
        ],
        inputs=[
            ("TWILIO_ACCOUNT_SID", "Twilio Account SID (AC...)"),
            ("TWILIO_AUTH_TOKEN",  "Twilio Auth Token"),
        ],
    )

    if not dry_run:
        set_env(ENV_FILE, vals)

    # Validate SID/token against the Twilio API (no message send — just account lookup)
    sid   = get_env_value("TWILIO_ACCOUNT_SID")
    token = get_env_value("TWILIO_AUTH_TOKEN")
    if sid and token:
        info("Validating Twilio credentials...")
        try:
            resp = httpx.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                auth=(sid, token),
                timeout=15,
            )
            if resp.status_code == 200:
                acct_name = resp.json().get("friendly_name", "")
                ok(f"Twilio credentials valid — account: {acct_name}")
            else:
                warn(f"Twilio validation failed: {resp.text[:200]}")
        except Exception as e:
            warn(f"Twilio validation error: {e}")

    info("WhatsApp sandbox test skipped — join sandbox first (see instructions below),")
    info("then run:  python scripts/pipeline_autosetup.py --resume-from twilio")


def stage_neosmtp(args, dry_run):
    step("STAGE 5/12 — Neo.space SMTP Password  [MANUAL — ~1 minute]")
    env = read_env(ENV_FILE)

    if env.get("NEO_SMTP_PASS"):
        ok("NEO_SMTP_PASS already set — skipping")
        return

    vals = pause(
        title="Neo.space SMTP Password",
        instructions=[
            "1. Log in to your neo.space control panel",
            "2. Find the SMTP / email password for relder@sovereignadvisory.ai",
            "   (usually under Email → Mailboxes → Settings / Change Password)",
            "3. Paste it below — it will be stored in .env and n8n credential",
        ],
        inputs=[
            ("NEO_SMTP_PASS", "Neo.space SMTP password for relder@sovereignadvisory.ai"),
        ],
    )

    if not dry_run:
        set_env(ENV_FILE, vals)


def stage_notion(args, dry_run):
    step("STAGE 6/12 — Notion Integration  [MANUAL — ~3 minutes, or --skip-notion]")

    if args.skip_notion:
        info("--skip-notion flag set — skipping Notion setup")
        return

    env = read_env(ENV_FILE)

    if env.get("NOTION_API_KEY") and env.get("NOTION_PARENT_PAGE_ID"):
        ok("Notion credentials already set — skipping manual step")
    else:
        vals = pause(
            title="Notion Integration Token + Parent Page",
            instructions=[
                "1. Go to https://www.notion.so/my-integrations → New Integration",
                "2. Name: Sovereign Advisory Pipeline",
                "   Capabilities: Read, Insert, Update content → Submit",
                "3. Copy the Internal Integration Secret (secret_xxxxx...)",
                "",
                "4. In Notion, open or create the page where SA Leads DB will live",
                "   (e.g. a page called 'Sovereign Advisory CRM')",
                "5. Share → Add connections → select 'Sovereign Advisory Pipeline'",
                "6. Copy the page ID from its URL:",
                "   notion.so/My-Page-Title-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
                "                           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^",
                "   That 32-char hex string is the page ID",
            ],
            inputs=[
                ("NOTION_API_KEY",        "Notion Internal Integration Secret (secret_...)"),
                ("NOTION_PARENT_PAGE_ID", "Notion parent page ID (32-char hex)"),
            ],
        )
        if not dry_run:
            set_env(ENV_FILE, vals)

    # Now run the Notion DB creation via setup script
    env = read_env(ENV_FILE)
    if dry_run:
        info("[dry-run] Would create Notion SA Leads database")
        return

    info("Creating SA Leads database in Notion...")
    try:
        headers = {
            "Authorization": f"Bearer {env['NOTION_API_KEY']}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        }
        # Check if DB already exists
        resp = httpx.post(
            "https://api.notion.com/v1/search",
            headers=headers,
            json={"query": "SA Leads", "filter": {"value": "database", "property": "object"}},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        existing = next(
            (r for r in results
             if r.get("title") and r["title"][0].get("plain_text") == "SA Leads"),
            None,
        )
        if existing:
            db_id = existing["id"]
            ok(f"SA Leads DB already exists: {db_id}")
        else:
            # Create it
            parent_id = env["NOTION_PARENT_PAGE_ID"].replace("-", "")
            # Re-format as UUID if needed
            if len(parent_id) == 32:
                parent_id = f"{parent_id[:8]}-{parent_id[8:12]}-{parent_id[12:16]}-{parent_id[16:20]}-{parent_id[20:]}"

            payload = {
                "parent": {"type": "page_id", "page_id": parent_id},
                "title": [{"type": "text", "text": {"content": "SA Leads"}}],
                "icon": {"type": "emoji", "emoji": "📋"},
                "properties": {
                    "Name":         {"title": {}},
                    "Email":        {"email": {}},
                    "Domain":       {"url": {}},
                    "Service Area": {"select": {"options": [
                        {"name": "Fractional CTO", "color": "blue"},
                        {"name": "AI Strategy", "color": "purple"},
                        {"name": "Technology Advisory", "color": "orange"},
                        {"name": "Other", "color": "gray"},
                    ]}},
                    "Status":       {"select": {"options": [
                        {"name": "Pending Review",   "color": "red"},
                        {"name": "Approved",         "color": "green"},
                        {"name": "Sent",             "color": "blue"},
                        {"name": "Queued",           "color": "purple"},
                        {"name": "Do Not Follow Up", "color": "gray"},
                    ]}},
                    "Lead ID":      {"rich_text": {}},
                    "Submitted":    {"date": {}},
                    "Summary":      {"rich_text": {}},
                    "Review Link":  {"url": {}},
                    "Sent At":      {"date": {}},
                },
            }
            resp = httpx.post(
                "https://api.notion.com/v1/databases",
                headers=headers,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            db_id = resp.json()["id"]
            ok(f"Created SA Leads database: {db_id}")

        set_env(ENV_FILE, {"NOTION_LEADS_DB_ID": db_id})
        ok(f"NOTION_LEADS_DB_ID saved to .env")

    except Exception as e:
        warn(f"Notion setup failed: {e} — continuing without Notion")


def stage_n8n_workflows(args, dry_run):
    step("STAGE 7/12 — Import n8n workflows via n8n REST API")
    env = read_env(ENV_FILE)

    n8n_url = "http://localhost:5678"
    api_key = env.get("N8N_API_KEY", "")
    if not api_key:
        warn("N8N_API_KEY not set — skipping workflow import")
        return
    if dry_run:
        info("[dry-run] Would import SA Contact Lead Pipeline + SA Lead Reminder via n8n API")
        return

    # Check if workflows already exist — if so, skip
    try:
        existing_names = {w["name"] for w in httpx.get(f"{n8n_url}/api/v1/workflows", headers=headers, timeout=10).json().get("data", [])}
    except Exception as e:
        warn(f"Could not query n8n workflows: {e}"); return

    if "SA Contact Lead Pipeline" in existing_names and "SA Lead Reminder (Business Day Check)" in existing_names:
        ok("Both n8n workflows already exist — skipping import")
        return

    # Import directly via n8n REST API — no Docker networking needed
    # Lazy import: mock psycopg2 if missing since we only need the build functions
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        import importlib, unittest.mock
        with unittest.mock.patch.dict("sys.modules", {"psycopg2": unittest.mock.MagicMock()}):
            spec = importlib.util.spec_from_file_location("setup_lead_pipeline", SCRIPT_DIR / "setup_lead_pipeline.py")
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        build_n8n_workflow    = mod.build_n8n_workflow
        build_reminder_workflow = mod.build_reminder_workflow
    except Exception as e:
        warn(f"Could not load workflow definitions: {e}"); return

    workflows = [
        ("SA Contact Lead Pipeline",            build_n8n_workflow()),
        ("SA Lead Reminder (Business Day Check)", build_reminder_workflow()),
    ]
    headers = {"X-N8N-API-KEY": api_key, "Content-Type": "application/json"}

    for name, wf_data in workflows:
        try:
            resp = httpx.get(f"{n8n_url}/api/v1/workflows", headers=headers, timeout=10)
            resp.raise_for_status()
            existing = next((w for w in resp.json().get("data", []) if w["name"] == name), None)

            if existing:
                wf_id = existing["id"]
                httpx.put(f"{n8n_url}/api/v1/workflows/{wf_id}", headers=headers, json=wf_data, timeout=30).raise_for_status()
                ok(f"Updated workflow [{wf_id}]: {name}")
            else:
                r = httpx.post(f"{n8n_url}/api/v1/workflows", headers=headers, json=wf_data, timeout=30)
                r.raise_for_status()
                wf_id = r.json()["id"]
                ok(f"Created workflow [{wf_id}]: {name}")

            httpx.patch(f"{n8n_url}/api/v1/workflows/{wf_id}", headers=headers, json={"active": True}, timeout=10)
            ok(f"Activated: {name}")
        except Exception as e:
            warn(f"Workflow '{name}' failed: {e}")


def stage_n8n_credentials(args, dry_run):
    step("STAGE 8/12 — Create n8n credentials via API")
    env = read_env(ENV_FILE)

    n8n_url = "http://localhost:5678"
    api_key = env.get("N8N_API_KEY", "")
    if not api_key:
        warn("N8N_API_KEY not set — skipping credential creation")
        return
    if dry_run:
        info("[dry-run] Would create SA PostgreSQL, SA Notion, SA Neo.space SMTP, SA Telegram Bot, SA Twilio credentials")
        return

    # ── SA PostgreSQL ──────────────────────────────────────────────────────
    try:
        n8n_create_credential(n8n_url, api_key, "SA PostgreSQL", "postgres", {
            "host": "postgres",
            "port": 5432,
            "database": "litellm",
            "user": "litellm",
            "password": "litellm_password",
            "ssl": "disable",
            "sshTunnel": False,
            "allowUnauthorizedCerts": False,
        })
    except Exception as e:
        warn(f"SA PostgreSQL credential failed: {e}")

    # ── SA Notion ──────────────────────────────────────────────────────────
    if env.get("NOTION_API_KEY") and not args.skip_notion:
        try:
            n8n_create_credential(n8n_url, api_key, "SA Notion", "notionApi", {
                "apiKey": env["NOTION_API_KEY"],
            })
        except Exception as e:
            warn(f"SA Notion credential failed: {e}")

    # ── SA Neo.space SMTP ──────────────────────────────────────────────────
    if env.get("NEO_SMTP_PASS"):
        try:
            n8n_create_credential(n8n_url, api_key, "SA Neo.space SMTP", "smtp", {
                "host":            env.get("NEO_SMTP_HOST", "smtp.neo.space"),
                "port":            int(env.get("NEO_SMTP_PORT", "587")),
                "user":            env.get("NEO_SMTP_USER", "relder@sovereignadvisory.ai"),
                "password":        env["NEO_SMTP_PASS"],
                "secure":          False,   # STARTTLS on port 587
                "disableStartTls": False,   # required by n8n schema when secure=false
            })
        except Exception as e:
            warn(f"SA Neo.space SMTP credential failed: {e}")
    else:
        warn("NEO_SMTP_PASS not set — SA Neo.space SMTP credential not created (run again after setting it)")

    # ── SA Telegram Bot ────────────────────────────────────────────────────
    if env.get("TELEGRAM_BOT_TOKEN"):
        try:
            n8n_create_credential(n8n_url, api_key, "SA Telegram Bot", "telegramApi", {
                "accessToken": env["TELEGRAM_BOT_TOKEN"],
            })
        except Exception as e:
            warn(f"SA Telegram Bot credential failed: {e}")
    else:
        warn("TELEGRAM_BOT_TOKEN not set — SA Telegram Bot credential not created")

    # ── SA Twilio (HTTP Basic Auth) ────────────────────────────────────────
    if env.get("TWILIO_ACCOUNT_SID") and env.get("TWILIO_AUTH_TOKEN"):
        try:
            n8n_create_credential(n8n_url, api_key, "SA Twilio", "httpBasicAuth", {
                "user":     env["TWILIO_ACCOUNT_SID"],
                "password": env["TWILIO_AUTH_TOKEN"],
            })
        except Exception as e:
            warn(f"SA Twilio credential failed: {e}")
    else:
        warn("TWILIO credentials not set — SA Twilio credential not created")


def stage_n8n_variables(args, dry_run):
    step("STAGE 9/12 — Inject pipeline variables into n8n (via docker compose restart)")

    info("n8n Community edition does not expose the Variables API.")
    info("Variables are injected as environment variables into the n8n container.")
    info("docker-compose.yml already has them wired; restarting n8n to load them.")

    if dry_run:
        info("[dry-run] Would run: docker compose up -d --force-recreate n8n")
        return

    result = subprocess.run(
        ["docker", "compose", "up", "-d", "--force-recreate", "n8n"],
        cwd=str(ROOT_DIR),
        capture_output=False,
    )
    if result.returncode != 0:
        warn("docker compose restart returned non-zero — check output above")
        return

    # Wait for n8n to become healthy
    info("Waiting for n8n to become healthy (up to 60s)...")
    for _ in range(12):
        time.sleep(5)
        try:
            r = httpx.get("http://localhost:5678/healthz", timeout=5)
            if r.status_code in (200, 204):
                ok("n8n is healthy — environment variables injected")
                return
        except Exception:
            pass
    warn("n8n health check timed out — variables may not yet be loaded; check: docker compose logs n8n")


def stage_webhook_url(args, dry_run):
    step("STAGE 10/12 — Patch webhook URL in index.html")
    env = read_env(ENV_FILE)

    n8n_url = "http://localhost:5678"
    api_key = env.get("N8N_API_KEY", "")

    # Discover the workflow's production webhook URL via n8n API
    webhook_url = ""
    if api_key:
        try:
            wf_id = n8n_get_workflow_id(n8n_url, api_key, "SA Contact Lead Pipeline")
            if wf_id:
                # The webhook path is sa-lead-intake (set in the workflow node)
                # Production URL pattern: {WEBHOOK_URL}/webhook/{path}
                webhook_url = f"https://sovereignadvisory.ai/n8n/webhook/sa-lead-intake"
                ok(f"Webhook URL: {webhook_url}")
            else:
                warn("SA Contact Lead Pipeline workflow not found in n8n — did stage 7 succeed?")
        except Exception as e:
            warn(f"Could not query n8n: {e}")

    if not webhook_url:
        warn("Could not determine webhook URL automatically")
        if dry_run:
            info("[dry-run] Would prompt for webhook URL — skipping")
            return
        webhook_url = input(
            "  Paste the webhook URL from n8n (or press Enter to skip): "
        ).strip()
        if not webhook_url:
            warn("Skipping index.html patch — do it manually later")
            return

    if not INDEX_HTML.exists():
        warn(f"index.html not found at {INDEX_HTML}")
        return

    html = INDEX_HTML.read_text()
    original = html

    # Replace: const WEBHOOK_URL = '...';
    html = re.sub(
        r"const WEBHOOK_URL\s*=\s*['\"].*?['\"];",
        f"const WEBHOOK_URL = '{webhook_url}';",
        html,
    )

    if html == original:
        # May already be patched — check if correct URL is present
        if webhook_url in original:
            ok(f"index.html already has the correct WEBHOOK_URL")
        else:
            warn("WEBHOOK_URL pattern not found in index.html — check the file manually")
        return

    if not dry_run:
        INDEX_HTML.write_text(html)
        ok(f"Patched index.html: WEBHOOK_URL = '{webhook_url}'")
    else:
        info(f"[dry-run] Would patch WEBHOOK_URL in index.html to: {webhook_url}")


def stage_deploy(args, dry_run):
    step("STAGE 11/12 — Deploy to VPS")

    if args.skip_deploy:
        info("--skip-deploy flag set — skipping VPS operations")
        return
    if dry_run:
        info(f"[dry-run] Would run deploy_to_vps.sh --host {args.vps_host} --skip-install")
        return

    if not DEPLOY_SH.exists():
        warn(f"deploy_to_vps.sh not found at {DEPLOY_SH}")
        return

    # ── 11a. Establish ControlMaster (single password prompt) ─────────────
    info("Establishing SSH ControlMaster (one password prompt for all operations)...")
    ssh_establish_master(args.vps_host, args.vps_user, str(args.vps_port), args.ssh_key)
    ok("SSH ControlMaster established — no further password prompts")

    # ── 11b. Sync files via deploy_to_vps.sh ──────────────────────────────
    info(f"Syncing files to {args.vps_user}@{args.vps_host}...")
    deploy_cmd = [
        "bash", str(DEPLOY_SH),
        "--host", args.vps_host,
        "--user", args.vps_user,
        "--port", str(args.vps_port),
        "--skip-install",
    ]
    if args.ssh_key and Path(args.ssh_key).exists():
        deploy_cmd += ["--key", args.ssh_key]

    result = subprocess.run(deploy_cmd, cwd=str(ROOT_DIR.parent))
    if result.returncode != 0:
        warn("deploy_to_vps.sh returned non-zero — check output above")
    else:
        ok("Files synced to VPS")

    # ── 11c. Build + start lead-review container ───────────────────────────
    info("Building lead-review container on VPS...")
    remote_cmds = (
        "set -e; "
        "cd /opt/sovereignadvisory && "
        "docker compose build lead-review && "
        "docker compose up -d lead-review && "
        "sleep 5 && "
        "docker compose ps lead-review && "
        "docker exec sa_nginx nginx -t && "
        "docker exec sa_nginx nginx -s reload && "
        "echo 'VPS containers updated and nginx reloaded'"
    )
    rc = ssh_run(args.vps_host, args.vps_user, str(args.vps_port), args.ssh_key, remote_cmds)
    if rc == 0:
        ok("VPS containers updated, nginx reloaded")
    else:
        warn("VPS container update had errors — check SSH output above")

    ssh_close_master(args.vps_host, args.vps_user, str(args.vps_port))


def stage_verify(args, dry_run):
    step("STAGE 12/12 — End-to-end verification")

    if dry_run:
        info("[dry-run] Would run verification checks")
        return

    env = read_env(ENV_FILE)
    base = "https://sovereignadvisory.ai"
    errors = []

    # ── 1. Website reachable ───────────────────────────────────────────────
    info("Checking website...")
    try:
        r = httpx.get(f"{base}/", timeout=10, follow_redirects=True)
        if r.status_code == 200:
            ok(f"Website: {base}/ → 200 OK")
        else:
            errors.append(f"Website returned {r.status_code}")
    except Exception as e:
        errors.append(f"Website unreachable: {e}")

    # ── 2. n8n reachable ───────────────────────────────────────────────────
    info("Checking n8n health...")
    try:
        r = httpx.get("http://localhost:5678/healthz", timeout=10)
        if r.status_code in (200, 204):
            ok("n8n: healthy")
        else:
            errors.append(f"n8n returned {r.status_code}")
    except Exception as e:
        errors.append(f"n8n unreachable: {e}")

    # ── 3. Lead review server (via nginx proxy on VPS) ─────────────────────
    if not args.skip_deploy:
        info("Checking lead-review server via nginx...")
        try:
            r = httpx.get(f"{base}/review/healthcheck-test-token", timeout=10, follow_redirects=True)
            # 404 means the proxy reached the server (server responded)
            if r.status_code in (200, 404):
                ok(f"Lead review proxy: reachable (HTTP {r.status_code})")
            else:
                errors.append(f"Lead review proxy: HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"Lead review proxy unreachable: {e}")

    # ── 4. Webhook test ────────────────────────────────────────────────────
    info("Sending test lead submission...")
    try:
        r = httpx.post(
            f"{base}/n8n/webhook/sa-lead-intake",
            json={
                "first_name": "Setup",
                "last_name":  "Test",
                "email":      "setuptest@example.com",
                "service_area": "Technology Advisory",
                "message": "Automated pipeline setup verification — please ignore.",
            },
            timeout=15,
        )
        if r.status_code == 200:
            ok("Webhook: test submission accepted — watch Telegram for notification")
        else:
            errors.append(f"Webhook returned {r.status_code}: {r.text[:100]}")
    except Exception as e:
        errors.append(f"Webhook unreachable: {e}")

    # ── Summary ────────────────────────────────────────────────────────────
    print()
    if errors:
        print(f"{Y}  Verification completed with warnings:{N}")
        for e in errors:
            print(f"  {R}✗{N}  {e}")
    else:
        print(f"{G}  All checks passed.{N}")

    print(f"""
{W}  Setup Summary{N}
  ─────────────────────────────────────────────────────────
  Review Portal:    {base}/review/<token>
  n8n Workflows:    http://localhost:5678 (or {base}/n8n/)
  Lead PDF output:  /data/output/lead_pdfs/ (on VPS)
  Password stored:  {ENV_FILE}  (LEAD_REVIEW_PASSWORD)
  ─────────────────────────────────────────────────────────
  Next: Submit a real form submission from {base}
        and verify the full pipeline end-to-end.
""")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="SA Lead Pipeline Automated Setup")
    parser.add_argument("--vps-host",     default="sovereignadvisory.ai")
    parser.add_argument("--vps-user",     default="root")
    parser.add_argument("--vps-port",     default="22")
    parser.add_argument("--ssh-key",      default=str(Path.home() / ".ssh" / "id_rsa"))
    parser.add_argument("--skip-deploy",  action="store_true")
    parser.add_argument("--skip-notion",  action="store_true")
    parser.add_argument("--dry-run",      action="store_true")
    parser.add_argument("--resume-from",  choices=STAGE_ORDER, default=None,
                        help="Skip all stages before this one")
    args = parser.parse_args()

    dry_run = args.dry_run

    print(f"""
{W}╔══════════════════════════════════════════════════════════════╗
║   SA Lead Pipeline — Automated Setup                        ║
║   78%% automated · 4 manual pauses · ~15 min total           ║
╚══════════════════════════════════════════════════════════════╝{N}
  .env:     {ENV_FILE}
  VPS:      {args.vps_user}@{args.vps_host}:{args.vps_port}
  Dry-run:  {dry_run}
""")

    resume_idx = STAGE_ORDER.index(args.resume_from) if args.resume_from else 0

    stage_fns = [
        ("env",             stage_env),
        ("schema",          stage_schema),
        ("telegram",        stage_telegram),
        ("twilio",          stage_twilio),
        ("neosmtp",         stage_neosmtp),
        ("notion",          stage_notion),
        ("n8n-workflows",   stage_n8n_workflows),
        ("n8n-credentials", stage_n8n_credentials),
        ("n8n-variables",   stage_n8n_variables),
        ("webhook-url",     stage_webhook_url),
        ("deploy",          stage_deploy),
        ("verify",          stage_verify),
    ]

    for i, (name, fn) in enumerate(stage_fns):
        if i < resume_idx:
            info(f"Skipping stage: {name}")
            continue
        try:
            fn(args, dry_run)
        except KeyboardInterrupt:
            print(f"\n\n{Y}  Interrupted. Resume with:{N}")
            print(f"  python scripts/pipeline_autosetup.py --resume-from {name}\n")
            sys.exit(1)
        except Exception as exc:
            warn(f"Stage '{name}' raised an exception: {exc}")
            print(f"  {Y}Resume after fixing with:{N}")
            print(f"  python scripts/pipeline_autosetup.py --resume-from {name}\n")
            raise

    print(f"\n{G}{W}  ✓ All stages complete.{N}\n")


if __name__ == "__main__":
    main()
