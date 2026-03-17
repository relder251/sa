"""
setup_lead_pipeline.py — One-time setup for the SA Lead Pipeline

Runs:
  1. PostgreSQL schema migration (idempotent)
  2. Creates or finds the Notion "SA Leads" database
  3. Creates / updates the n8n lead pipeline workflow via REST API
  4. Prints Telegram bot setup instructions

Usage:
  python setup_lead_pipeline.py [--skip-notion] [--skip-n8n] [--dry-run]

Required env vars (from .env):
  DATABASE_URL
  NOTION_API_KEY
  NOTION_PARENT_PAGE_ID    (page under which the SA Leads DB will be created)
  N8N_BASE_URL             (default: http://localhost:5678)
  N8N_API_KEY              (Settings > API in n8n UI)
  NEO_SMTP_HOST / NEO_SMTP_PORT / NEO_SMTP_USER / NEO_SMTP_PASS
  NOTIFY_EMAIL
  NOTIFY_SMS_EMAIL         (+12768805651@vtext.com)
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
  TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN
  TWILIO_WHATSAPP_FROM     (whatsapp:+14155238886  — Twilio sandbox)
  WHATSAPP_NOTIFY_TO       (whatsapp:+12768805651)
  LEAD_REVIEW_BASE_URL     (https://sovereignadvisory.ai/review)
  LEAD_REVIEW_PASSWORD
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

# Load .env from Agentic_SDLC root
_root = Path(__file__).resolve().parents[1]
load_dotenv(_root / ".env")

DATABASE_URL          = os.environ.get("DATABASE_URL", "")
NOTION_API_KEY        = os.environ.get("NOTION_API_KEY", "")
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "")
N8N_BASE_URL          = os.environ.get("N8N_BASE_URL", "http://localhost:5678")
N8N_API_KEY           = os.environ.get("N8N_API_KEY", "")
LEAD_REVIEW_BASE_URL  = os.environ.get("LEAD_REVIEW_BASE_URL", "https://sovereignadvisory.ai/review")


# ── 1. PostgreSQL schema ────────────────────────────────────────────────────────

def run_postgres_schema():
    schema_file = _root / "sql" / "leads_schema.sql"
    print(f"\n[1/4] Applying PostgreSQL schema from {schema_file} ...")
    if not schema_file.exists():
        print("  ERROR: schema file not found"); sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute(schema_file.read_text())
    conn.close()
    print("  ✓ Schema applied (idempotent — safe to re-run)")


# ── 2. Notion database ─────────────────────────────────────────────────────────

NOTION_HEADERS = lambda: {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

NOTION_DB_SCHEMA = {
    "Name": {"title": {}},
    "Email": {"email": {}},
    "Domain": {"url": {}},
    "Service Area": {"select": {"options": [
        {"name": "Fractional CTO", "color": "blue"},
        {"name": "AI Strategy", "color": "purple"},
        {"name": "M&A / Due Diligence", "color": "green"},
        {"name": "Technology Advisory", "color": "orange"},
        {"name": "Other", "color": "gray"},
    ]}},
    "Status": {"select": {"options": [
        {"name": "Pending Research", "color": "yellow"},
        {"name": "Pending Draft",    "color": "orange"},
        {"name": "Pending Review",   "color": "red"},
        {"name": "Approved",         "color": "green"},
        {"name": "Sent",             "color": "blue"},
        {"name": "Queued",           "color": "purple"},
        {"name": "Do Not Follow Up", "color": "gray"},
        {"name": "Spam",             "color": "brown"},
    ]}},
    "Lead ID": {"rich_text": {}},
    "Submitted": {"date": {}},
    "Summary": {"rich_text": {}},
    "Approach": {"rich_text": {}},
    "Review Link": {"url": {}},
    "PDF Link": {"url": {}},
    "Sent At": {"date": {}},
}


def setup_notion(dry_run=False):
    if not NOTION_API_KEY:
        print("\n[2/4] Skipping Notion — NOTION_API_KEY not set")
        return None

    print("\n[2/4] Checking for existing 'SA Leads' Notion database ...")

    # Search for existing DB
    resp = httpx.post(
        "https://api.notion.com/v1/search",
        headers=NOTION_HEADERS(),
        json={"query": "SA Leads", "filter": {"value": "database", "property": "object"}},
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    existing = next(
        (r for r in results if r.get("title", [{}])[0].get("plain_text", "") == "SA Leads"),
        None,
    )
    if existing:
        db_id = existing["id"]
        print(f"  ✓ Found existing database: {db_id}")
        env_hint(db_id)
        return db_id

    if dry_run:
        print("  [dry-run] Would create SA Leads database")
        return None

    if not NOTION_PARENT_PAGE_ID:
        print("  ERROR: NOTION_PARENT_PAGE_ID not set — cannot create database")
        return None

    print("  Creating 'SA Leads' database ...")
    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        "title": [{"type": "text", "text": {"content": "SA Leads"}}],
        "icon": {"type": "emoji", "emoji": "📋"},
        "properties": NOTION_DB_SCHEMA,
    }
    resp = httpx.post(
        "https://api.notion.com/v1/databases",
        headers=NOTION_HEADERS(),
        json=payload,
    )
    resp.raise_for_status()
    db_id = resp.json()["id"]
    print(f"  ✓ Created database: {db_id}")
    env_hint(db_id, var="NOTION_LEADS_DB_ID")
    return db_id


def env_hint(value: str, var="NOTION_LEADS_DB_ID"):
    print(f"\n  ╔══════════════════════════════════════════════════╗")
    print(f"  ║  Add to .env:  {var}={value}")
    print(f"  ╚══════════════════════════════════════════════════╝\n")


# ── 3. n8n workflow ─────────────────────────────────────────────────────────────

def _n8n_headers():
    if not N8N_API_KEY:
        raise ValueError("N8N_API_KEY not set")
    return {"X-N8N-API-KEY": N8N_API_KEY, "Content-Type": "application/json"}


def build_n8n_workflow() -> dict:
    """
    Return the n8n workflow JSON definition.

    Phases:
      Webhook → Parse input → Research (company + person)
      → AI analysis (summary, approach, starters, questions, scenarios)
      → Generate draft email
      → Save to PostgreSQL + Notion
      → Generate PDF
      → Create review token
      → Send notifications (email, SMS, Telegram, WhatsApp)
      → Wait (HITM) → Resume handler
        approve   → Send email via SMTP → update DB/Notion
        regenerate→ Re-run AI analysis
        queue     → Update DB/Notion
        dnfu      → Update DB/Notion (do_not_follow_up)

    NOTE: This is a simplified structural JSON.  Full implementation requires
    the n8n node IDs, credential references, and expression syntax.
    Import this file into n8n and wire credentials manually, or use the
    provided credential names which map to the .env variables.
    """
    return {
        "name": "SA Contact Lead Pipeline",
        "nodes": [
            # ── Entry ──────────────────────────────────────────────────────────
            {
                "id": "webhook-entry",
                "name": "Contact Form Webhook",
                "type": "n8n-nodes-base.webhook",
                "position": [0, 300],
                "parameters": {
                    "path": "sa-lead-intake",
                    "responseMode": "responseNode",
                    "options": {}
                }
            },
            {
                "id": "respond-ok",
                "name": "Respond 200 OK",
                "type": "n8n-nodes-base.respondToWebhook",
                "position": [200, 300],
                "parameters": {
                    "respondWith": "json",
                    "responseBody": "={{ JSON.stringify({status: 'received'}) }}"
                }
            },
            # ── Parse + store ──────────────────────────────────────────────────
            {
                "id": "parse-input",
                "name": "Parse & Save Lead to PG",
                "type": "n8n-nodes-base.postgres",
                "position": [400, 300],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "INSERT INTO sa_leads "
                        "(first_name, last_name, email, domain, service_area, message, status) "
                        "VALUES ($1,$2,$3,$4,$5,$6,'pending_research') RETURNING id"
                    ),
                    "additionalFields": {
                        "queryParams": (
                            "={{ [\n"
                            "  $json.body.first_name,\n"
                            "  $json.body.last_name,\n"
                            "  $json.body.email,\n"
                            "  $json.body.email.split('@')[1] || '',\n"
                            "  $json.body.service_area,\n"
                            "  $json.body.message\n"
                            "] }}"
                        )
                    }
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            # ── Research ───────────────────────────────────────────────────────
            {
                "id": "company-research",
                "name": "Company Research (LLM)",
                "type": "n8n-nodes-base.httpRequest",
                "position": [600, 200],
                "parameters": {
                    "method": "POST",
                    "url": "={{ $env.LITELLM_BASE_URL }}/v1/chat/completions",
                    "authentication": "genericCredentialType",
                    "genericAuthType": "httpHeaderAuth",
                    "sendHeaders": True,
                    "headerParameters": {"parameters": [
                        {"name": "Authorization", "value": "=Bearer {{ $env.LITELLM_API_KEY }}"}
                    ]},
                    "sendBody": True,
                    "bodyParameters": {"parameters": [
                        {"name": "model", "value": "cloud/search"},
                        {"name": "messages", "value": (
                            "=[{\n"
                            "  role: 'system',\n"
                            "  content: 'You are a business intelligence researcher. Return JSON with fields: summary (2-3 paragraphs), industry, size, funding_stage, key_products, recent_news.'\n"
                            "},{\n"
                            "  role: 'user',\n"
                            "  content: `Research the company at domain: ${$('parse-input').item.json[0].id}. "
                            "Focus on: business model, size, recent news, technology stack, leadership.`\n"
                            "}]"
                        )},
                        {"name": "response_format", "value": "={ type: 'json_object' }"},
                    ]}
                }
            },
            {
                "id": "update-research",
                "name": "Save Research to PG",
                "type": "n8n-nodes-base.postgres",
                "position": [800, 200],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "UPDATE sa_leads SET company_research=$1, status='pending_draft', "
                        "research_completed_at=NOW() WHERE id=$2"
                    ),
                    "additionalFields": {
                        "queryParams": (
                            "={{ [\n"
                            "  JSON.stringify($json.choices[0].message.content),\n"
                            "  $('parse-input').item.json[0].id\n"
                            "] }}"
                        )
                    }
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            # ── AI Analysis ────────────────────────────────────────────────────
            {
                "id": "ai-analysis",
                "name": "AI Lead Analysis (LLM)",
                "type": "n8n-nodes-base.httpRequest",
                "position": [1000, 300],
                "parameters": {
                    "method": "POST",
                    "url": "={{ $env.LITELLM_BASE_URL }}/v1/chat/completions",
                    "authentication": "genericCredentialType",
                    "genericAuthType": "httpHeaderAuth",
                    "sendHeaders": True,
                    "headerParameters": {"parameters": [
                        {"name": "Authorization", "value": "=Bearer {{ $env.LITELLM_API_KEY }}"}
                    ]},
                    "sendBody": True,
                    "bodyParameters": {"parameters": [
                        {"name": "model", "value": "cloud/smart"},
                        {"name": "messages", "value": (
                            "=[{\n"
                            "  role: 'system',\n"
                            "  content: 'You are a senior business advisor at Sovereign Advisory. "
                            "Analyse the lead and return JSON with exactly these fields:\\n"
                            "summary: string (2-3 paragraphs strategic overview)\\n"
                            "approach: string (recommended engagement approach, 1-2 paragraphs)\\n"
                            "conversation_starters: array of 3 strings\\n"
                            "questions: array of 3 strings (discovery questions to ask)\\n"
                            "scenarios: array of 3 strings (support scenarios we could offer)'\n"
                            "},{\n"
                            "  role: 'user',\n"
                            "  content: `Lead Details:\\nName: ${$('parse-input').item.json[0].first_name}\\n"
                            "Email: ${$('parse-input').item.json[0].email}\\n"
                            "Domain: ${$('parse-input').item.json[0].domain}\\n"
                            "Service Interest: ${$('parse-input').item.json[0].service_area}\\n"
                            "Message: ${$('parse-input').item.json[0].message}\\n\\n"
                            "Company Research:\\n${JSON.stringify($('company-research').item.json.choices[0].message.content)}`\n"
                            "}]"
                        )},
                        {"name": "response_format", "value": "={ type: 'json_object' }"},
                    ]}
                }
            },
            {
                "id": "save-analysis",
                "name": "Save Analysis to PG",
                "type": "n8n-nodes-base.postgres",
                "position": [1200, 300],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "UPDATE sa_leads SET summary=$1, approach=$2, "
                        "conversation_starters=$3, questions=$4, scenarios=$5, "
                        "status='pending_draft' WHERE id=$6"
                    ),
                    "additionalFields": {
                        "queryParams": (
                            "={{ (() => {\n"
                            "  const a = JSON.parse($json.choices[0].message.content);\n"
                            "  return [\n"
                            "    a.summary, a.approach,\n"
                            "    JSON.stringify(a.conversation_starters),\n"
                            "    JSON.stringify(a.questions),\n"
                            "    JSON.stringify(a.scenarios),\n"
                            "    $('parse-input').item.json[0].id\n"
                            "  ];\n"
                            "})() }}"
                        )
                    }
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            # ── Email Draft ────────────────────────────────────────────────────
            {
                "id": "draft-email",
                "name": "Generate Email Draft (LLM)",
                "type": "n8n-nodes-base.httpRequest",
                "position": [1400, 300],
                "parameters": {
                    "method": "POST",
                    "url": "={{ $env.LITELLM_BASE_URL }}/v1/chat/completions",
                    "authentication": "genericCredentialType",
                    "genericAuthType": "httpHeaderAuth",
                    "sendHeaders": True,
                    "headerParameters": {"parameters": [
                        {"name": "Authorization", "value": "=Bearer {{ $env.LITELLM_API_KEY }}"}
                    ]},
                    "sendBody": True,
                    "bodyParameters": {"parameters": [
                        {"name": "model", "value": "cloud/smart"},
                        {"name": "messages", "value": (
                            "=[{\n"
                            "  role: 'system',\n"
                            "  content: 'You are writing on behalf of Robert Elder, CEO of Sovereign Advisory. "
                            "Write a warm, professional follow-up email to a prospective client who submitted a contact form. "
                            "Tone: direct, confident, non-salesy — mirrors the sovereign advisory website voice. "
                            "Propose a 30-minute introductory call. No fluff. No bullet points in the email. "
                            "Return JSON with fields: subject (string), body_text (plain text email).'\n"
                            "},{\n"
                            "  role: 'user',\n"
                            "  content: `Prospect: ${$('parse-input').item.json[0].first_name} ${$('parse-input').item.json[0].last_name}\\n"
                            "Company: ${$('parse-input').item.json[0].domain}\\n"
                            "Message: ${$('parse-input').item.json[0].message}\\n\\n"
                            "Strategic summary:\\n${$('ai-analysis').item.json.choices[0].message.content}`\n"
                            "}]"
                        )},
                        {"name": "response_format", "value": "={ type: 'json_object' }"},
                    ]}
                }
            },
            {
                "id": "save-draft",
                "name": "Save Draft to PG",
                "type": "n8n-nodes-base.postgres",
                "position": [1600, 300],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "INSERT INTO sa_lead_drafts (lead_id, subject, body_text, is_current) "
                        "VALUES ($1,$2,$3,TRUE) RETURNING id"
                    ),
                    "additionalFields": {
                        "queryParams": (
                            "={{ (() => {\n"
                            "  const d = JSON.parse($('draft-email').item.json.choices[0].message.content);\n"
                            "  return [\n"
                            "    $('parse-input').item.json[0].id,\n"
                            "    d.subject, d.body_text\n"
                            "  ];\n"
                            "})() }}"
                        )
                    }
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            # ── Notion ─────────────────────────────────────────────────────────
            {
                "id": "create-notion-page",
                "name": "Create Notion Lead Page",
                "type": "n8n-nodes-base.notion",
                "position": [1800, 200],
                "parameters": {
                    "resource": "databasePage",
                    "operation": "create",
                    "databaseId": "={{ $env.NOTION_LEADS_DB_ID }}",
                    "propertiesUi": {"propertyValues": [
                        {"key": "Name",         "type": "title",       "titleValue": "={{ $('parse-input').item.json[0].first_name + ' ' + $('parse-input').item.json[0].last_name }}"},
                        {"key": "Email",        "type": "email",       "emailValue": "={{ $('parse-input').item.json[0].email }}"},
                        {"key": "Domain",       "type": "url",         "urlValue": "={{ 'https://' + $('parse-input').item.json[0].domain }}"},
                        {"key": "Status",       "type": "select",      "selectValue": "Pending Review"},
                        {"key": "Lead ID",      "type": "richText",    "textContent": "={{ $('parse-input').item.json[0].id }}"},
                        {"key": "Submitted",    "type": "date",        "date": {"start": "={{ new Date().toISOString() }}"}},
                    ]}
                },
                "credentials": {"notionApi": {"id": "notion-sa", "name": "SA Notion"}}
            },
            {
                "id": "save-notion-id",
                "name": "Save Notion ID to PG",
                "type": "n8n-nodes-base.postgres",
                "position": [2000, 200],
                "parameters": {
                    "operation": "executeQuery",
                    "query": "UPDATE sa_leads SET notion_page_id=$1 WHERE id=$2",
                    "additionalFields": {
                        "queryParams": "={{ [$json.id, $('parse-input').item.json[0].id] }}"
                    }
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            # ── Review token + HITM ────────────────────────────────────────────
            {
                "id": "create-review-token",
                "name": "Create Review Token",
                "type": "n8n-nodes-base.postgres",
                "position": [1800, 400],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "INSERT INTO sa_review_tokens (lead_id, n8n_resume_url) "
                        "VALUES ($1, $2) RETURNING token"
                    ),
                    "additionalFields": {
                        "queryParams": (
                            "={{ [\n"
                            "  $('parse-input').item.json[0].id,\n"
                            "  $execution.resumeUrl\n"
                            "] }}"
                        )
                    }
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            {
                "id": "update-status-review",
                "name": "Set Status: Pending Review",
                "type": "n8n-nodes-base.postgres",
                "position": [2000, 400],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "UPDATE sa_leads SET status='pending_review', "
                        "draft_generated_at=NOW(), first_notified_at=NOW() WHERE id=$1"
                    ),
                    "additionalFields": {
                        "queryParams": "={{ [$('parse-input').item.json[0].id] }}"
                    }
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            # ── Notifications ──────────────────────────────────────────────────
            {
                "id": "send-notify-email",
                "name": "Email Notification",
                "type": "n8n-nodes-base.emailSend",
                "position": [2200, 100],
                "parameters": {
                    "fromEmail": "relder@sovereignadvisory.ai",
                    "toEmail": "={{ $env.NOTIFY_EMAIL }}",
                    "subject": "=New SA Lead: {{ $('parse-input').item.json[0].first_name }} {{ $('parse-input').item.json[0].last_name }} — {{ $('parse-input').item.json[0].service_area }}",
                    "emailFormat": "html",
                    "message": (
                        "=<p>A new lead has been processed and is ready for your review.</p>"
                        "<p><b>Prospect:</b> {{ $('parse-input').item.json[0].first_name }} {{ $('parse-input').item.json[0].last_name }}<br>"
                        "<b>Email:</b> {{ $('parse-input').item.json[0].email }}<br>"
                        "<b>Domain:</b> {{ $('parse-input').item.json[0].domain }}<br>"
                        "<b>Service:</b> {{ $('parse-input').item.json[0].service_area }}</p>"
                        "<p><b>Review link:</b> <a href='{{ $env.LEAD_REVIEW_BASE_URL }}/{{ $('create-review-token').item.json[0].token }}'>"
                        "{{ $env.LEAD_REVIEW_BASE_URL }}/{{ $('create-review-token').item.json[0].token }}</a></p>"
                    ),
                    "options": {}
                },
                "credentials": {"smtp": {"id": "smtp-neo", "name": "SA Neo.space SMTP"}}
            },
            {
                "id": "send-sms",
                "name": "SMS (Verizon email gateway)",
                "type": "n8n-nodes-base.emailSend",
                "position": [2200, 250],
                "parameters": {
                    "fromEmail": "relder@sovereignadvisory.ai",
                    "toEmail": "={{ $env.NOTIFY_SMS_EMAIL }}",
                    "subject": "New SA Lead",
                    "message": (
                        "=New lead: {{ $('parse-input').item.json[0].first_name }} "
                        "{{ $('parse-input').item.json[0].last_name }} "
                        "({{ $('parse-input').item.json[0].service_area }}). "
                        "Review: {{ $env.LEAD_REVIEW_BASE_URL }}/{{ $('create-review-token').item.json[0].token }}"
                    ),
                    "options": {}
                },
                "credentials": {"smtp": {"id": "smtp-neo", "name": "SA Neo.space SMTP"}}
            },
            {
                "id": "send-telegram",
                "name": "Telegram Notification",
                "type": "n8n-nodes-base.telegram",
                "position": [2200, 400],
                "parameters": {
                    "chatId": "={{ $env.TELEGRAM_CHAT_ID }}",
                    "text": (
                        "=🔔 *New SA Lead*\n\n"
                        "*Prospect:* {{ $('parse-input').item.json[0].first_name }} {{ $('parse-input').item.json[0].last_name }}\n"
                        "*Email:* {{ $('parse-input').item.json[0].email }}\n"
                        "*Domain:* {{ $('parse-input').item.json[0].domain }}\n"
                        "*Service:* {{ $('parse-input').item.json[0].service_area }}\n\n"
                        "🔗 [Review Lead]({{ $env.LEAD_REVIEW_BASE_URL }}/{{ $('create-review-token').item.json[0].token }})"
                    ),
                    "additionalFields": {"parse_mode": "Markdown"}
                },
                "credentials": {"telegramApi": {"id": "tg-sa", "name": "SA Telegram Bot"}}
            },
            {
                "id": "send-whatsapp",
                "name": "WhatsApp (Twilio)",
                "type": "n8n-nodes-base.httpRequest",
                "position": [2200, 550],
                "parameters": {
                    "method": "POST",
                    "url": "=https://api.twilio.com/2010-04-01/Accounts/{{ $env.TWILIO_ACCOUNT_SID }}/Messages.json",
                    "authentication": "genericCredentialType",
                    "genericAuthType": "httpBasicAuth",
                    "sendBody": True,
                    "contentType": "form-urlencoded",
                    "bodyParameters": {"parameters": [
                        {"name": "From", "value": "={{ $env.TWILIO_WHATSAPP_FROM }}"},
                        {"name": "To",   "value": "={{ $env.WHATSAPP_NOTIFY_TO }}"},
                        {"name": "Body", "value": (
                            "=New SA Lead: {{ $('parse-input').item.json[0].first_name }} "
                            "{{ $('parse-input').item.json[0].last_name }} "
                            "({{ $('parse-input').item.json[0].service_area }}). "
                            "Review: {{ $env.LEAD_REVIEW_BASE_URL }}/{{ $('create-review-token').item.json[0].token }}"
                        )},
                    ]}
                },
                "credentials": {"httpBasicAuth": {"id": "twilio-sa", "name": "SA Twilio"}}
            },
            # ── HITM Wait node ─────────────────────────────────────────────────
            {
                "id": "wait-review",
                "name": "Wait for Review Decision",
                "type": "n8n-nodes-base.wait",
                "position": [2400, 350],
                "parameters": {
                    "resume": "webhook",
                    "options": {}
                }
            },
            # ── Decision router ────────────────────────────────────────────────
            {
                "id": "route-action",
                "name": "Route by Action",
                "type": "n8n-nodes-base.switch",
                "position": [2600, 350],
                "parameters": {
                    "mode": "expression",
                    "output": "={{ $json.action }}",
                    "routing": [
                        {"output": 0, "conditions": {"string": [{"value1": "={{ $json.action }}", "value2": "approve"}]}},
                        {"output": 1, "conditions": {"string": [{"value1": "={{ $json.action }}", "value2": "regenerate"}]}},
                        {"output": 2, "conditions": {"string": [{"value1": "={{ $json.action }}", "value2": "queue"}]}},
                        {"output": 3, "conditions": {"string": [{"value1": "={{ $json.action }}", "value2": "dnfu"}]}},
                    ]
                }
            },
            # ── Approve path: send email ───────────────────────────────────────
            {
                "id": "send-outreach-email",
                "name": "Send Outreach Email (SMTP)",
                "type": "n8n-nodes-base.emailSend",
                "position": [2800, 150],
                "parameters": {
                    "fromEmail": "Robert Elder <relder@sovereignadvisory.ai>",
                    "toEmail": "={{ $('parse-input').item.json[0].email }}",
                    "subject": "={{ $('save-draft').item.json[0].subject }}",
                    "emailFormat": "text",
                    "message": "={{ $('save-draft').item.json[0].body_text }}",
                    "options": {}
                },
                "credentials": {"smtp": {"id": "smtp-neo", "name": "SA Neo.space SMTP"}}
            },
            {
                "id": "mark-sent",
                "name": "Mark Lead Sent",
                "type": "n8n-nodes-base.postgres",
                "position": [3000, 150],
                "parameters": {
                    "operation": "executeQuery",
                    "query": "UPDATE sa_leads SET status='sent', sent_at=NOW() WHERE id=$1",
                    "additionalFields": {"queryParams": "={{ [$('parse-input').item.json[0].id] }}"}
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            # ── Regenerate path ────────────────────────────────────────────────
            {
                "id": "regen-analysis",
                "name": "Re-run AI Analysis",
                "type": "n8n-nodes-base.httpRequest",
                "position": [2800, 350],
                "parameters": {
                    "method": "POST",
                    "url": "={{ $env.LITELLM_BASE_URL }}/v1/chat/completions",
                    "authentication": "genericCredentialType",
                    "genericAuthType": "httpHeaderAuth",
                    "sendHeaders": True,
                    "headerParameters": {"parameters": [
                        {"name": "Authorization", "value": "=Bearer {{ $env.LITELLM_API_KEY }}"}
                    ]},
                    "sendBody": True,
                    "bodyParameters": {"parameters": [
                        {"name": "model", "value": "cloud/smart"},
                        {"name": "messages", "value": (
                            "=[{\n"
                            "  role: 'system',\n"
                            "  content: 'You are a senior business advisor at Sovereign Advisory. "
                            "Regenerate the email draft, incorporating reviewer feedback. "
                            "Return JSON with: subject, body_text.'\n"
                            "},{\n"
                            "  role: 'user',\n"
                            "  content: `Previous draft:\\n${$('save-draft').item.json[0].body_text}\\n\\n"
                            "Reviewer notes:\\n${$json.notes}`\n"
                            "}]"
                        )},
                        {"name": "response_format", "value": "={ type: 'json_object' }"},
                    ]}
                }
            },
            {
                "id": "save-new-draft",
                "name": "Save New Draft Version",
                "type": "n8n-nodes-base.postgres",
                "position": [3000, 350],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "UPDATE sa_lead_drafts SET is_current=FALSE WHERE lead_id=$1;\n"
                        "INSERT INTO sa_lead_drafts (lead_id, version, subject, body_text, is_current) "
                        "SELECT $1, COALESCE(MAX(version),0)+1, $2, $3, TRUE "
                        "FROM sa_lead_drafts WHERE lead_id=$1;"
                    ),
                    "additionalFields": {
                        "queryParams": (
                            "={{ (() => {\n"
                            "  const d = JSON.parse($('regen-analysis').item.json.choices[0].message.content);\n"
                            "  return [$('parse-input').item.json[0].id, d.subject, d.body_text];\n"
                            "})() }}"
                        )
                    }
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
        ],
        "connections": {
            "Contact Form Webhook":         {"main": [[{"node": "Respond 200 OK", "type": "main", "index": 0}]]},
            "Respond 200 OK":               {"main": [[{"node": "Parse & Save Lead to PG", "type": "main", "index": 0}]]},
            "Parse & Save Lead to PG":      {"main": [[{"node": "Company Research (LLM)", "type": "main", "index": 0}]]},
            "Company Research (LLM)":       {"main": [[{"node": "Save Research to PG", "type": "main", "index": 0}]]},
            "Save Research to PG":          {"main": [[{"node": "AI Lead Analysis (LLM)", "type": "main", "index": 0}]]},
            "AI Lead Analysis (LLM)":       {"main": [[{"node": "Save Analysis to PG", "type": "main", "index": 0}]]},
            "Save Analysis to PG":          {"main": [[{"node": "Generate Email Draft (LLM)", "type": "main", "index": 0}]]},
            "Generate Email Draft (LLM)":   {"main": [[{"node": "Save Draft to PG", "type": "main", "index": 0}]]},
            "Save Draft to PG":             {"main": [[
                {"node": "Create Notion Lead Page", "type": "main", "index": 0},
                {"node": "Create Review Token",     "type": "main", "index": 0},
            ]]},
            "Create Notion Lead Page":      {"main": [[{"node": "Save Notion ID to PG", "type": "main", "index": 0}]]},
            "Create Review Token":          {"main": [[{"node": "Set Status: Pending Review", "type": "main", "index": 0}]]},
            "Set Status: Pending Review":   {"main": [[
                {"node": "Email Notification",           "type": "main", "index": 0},
                {"node": "SMS (Verizon email gateway)",  "type": "main", "index": 0},
                {"node": "Telegram Notification",        "type": "main", "index": 0},
                {"node": "WhatsApp (Twilio)",            "type": "main", "index": 0},
                {"node": "Wait for Review Decision",     "type": "main", "index": 0},
            ]]},
            "Wait for Review Decision":     {"main": [[{"node": "Route by Action", "type": "main", "index": 0}]]},
            "Route by Action":              {"main": [
                [{"node": "Send Outreach Email (SMTP)",  "type": "main", "index": 0}],
                [{"node": "Re-run AI Analysis",          "type": "main", "index": 0}],
                [],  # queue — no further action needed
                [],  # dnfu  — no further action needed
            ]},
            "Send Outreach Email (SMTP)":   {"main": [[{"node": "Mark Lead Sent", "type": "main", "index": 0}]]},
            "Re-run AI Analysis":           {"main": [[{"node": "Save New Draft Version", "type": "main", "index": 0}]]},
        },
        "settings": {
            "executionOrder": "v1",
            "saveManualExecutions": True,
            "callerPolicy": "workflowsFromSameOwner",
            "errorWorkflow": ""
        },
        "staticData": None,
    }


def build_reminder_workflow() -> dict:
    """Scheduled workflow: check for unreviewed leads and send reminders."""
    return {
        "name": "SA Lead Reminder (Business Day Check)",
        "nodes": [
            {
                "id": "schedule",
                "name": "Every Business Morning (9am)",
                "type": "n8n-nodes-base.scheduleTrigger",
                "position": [0, 300],
                "parameters": {
                    "rule": {"interval": [{"field": "cronExpression", "expression": "0 9 * * 1-5"}]}
                }
            },
            {
                "id": "fetch-overdue-1",
                "name": "Fetch Leads Due 1st Reminder",
                "type": "n8n-nodes-base.postgres",
                "position": [200, 200],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "SELECT l.id, l.first_name, l.last_name, l.email, l.service_area,\n"
                        "       rt.token\n"
                        "FROM sa_leads l\n"
                        "JOIN sa_review_tokens rt ON rt.lead_id = l.id AND rt.is_active = TRUE\n"
                        "WHERE l.status = 'pending_review'\n"
                        "  AND l.first_reminder_sent = FALSE\n"
                        "  AND l.first_notified_at < NOW() - INTERVAL '1 business day'\n"
                        "  AND EXTRACT(DOW FROM NOW()) NOT IN (0,6)"  # Mon-Fri
                    )
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            {
                "id": "fetch-overdue-2",
                "name": "Fetch Leads Due 2nd Reminder",
                "type": "n8n-nodes-base.postgres",
                "position": [200, 400],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "SELECT l.id, l.first_name, l.last_name, l.email, l.service_area,\n"
                        "       rt.token\n"
                        "FROM sa_leads l\n"
                        "JOIN sa_review_tokens rt ON rt.lead_id = l.id AND rt.is_active = TRUE\n"
                        "WHERE l.status = 'pending_review'\n"
                        "  AND l.first_reminder_sent = TRUE\n"
                        "  AND l.second_reminder_sent = FALSE\n"
                        "  AND l.first_reminder_at < NOW() - INTERVAL '2 business days'\n"
                        "  AND EXTRACT(DOW FROM NOW()) NOT IN (0,6)"
                    )
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            {
                "id": "notify-1st",
                "name": "1st Reminder: Telegram",
                "type": "n8n-nodes-base.telegram",
                "position": [500, 200],
                "parameters": {
                    "chatId": "={{ $env.TELEGRAM_CHAT_ID }}",
                    "text": (
                        "=⏰ *Lead Reminder (1 day)*\n\n"
                        "{{ $json.first_name }} {{ $json.last_name }} ({{ $json.service_area }}) "
                        "is still awaiting review.\n\n"
                        "🔗 [Review Now]({{ $env.LEAD_REVIEW_BASE_URL }}/{{ $json.token }})"
                    ),
                    "additionalFields": {"parse_mode": "Markdown"}
                },
                "credentials": {"telegramApi": {"id": "tg-sa", "name": "SA Telegram Bot"}}
            },
            {
                "id": "mark-1st-sent",
                "name": "Mark 1st Reminder Sent",
                "type": "n8n-nodes-base.postgres",
                "position": [700, 200],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "UPDATE sa_leads SET first_reminder_sent=TRUE, first_reminder_at=NOW() WHERE id=$1"
                    ),
                    "additionalFields": {"queryParams": "={{ [$json.id] }}"}
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
            {
                "id": "notify-2nd",
                "name": "2nd Reminder: Telegram + Email",
                "type": "n8n-nodes-base.telegram",
                "position": [500, 400],
                "parameters": {
                    "chatId": "={{ $env.TELEGRAM_CHAT_ID }}",
                    "text": (
                        "=🚨 *Urgent: Lead Unreviewed 3+ Days*\n\n"
                        "{{ $json.first_name }} {{ $json.last_name }} ({{ $json.service_area }}) "
                        "has been waiting for 3+ business days.\n\n"
                        "🔗 [Review Now]({{ $env.LEAD_REVIEW_BASE_URL }}/{{ $json.token }})"
                    ),
                    "additionalFields": {"parse_mode": "Markdown"}
                },
                "credentials": {"telegramApi": {"id": "tg-sa", "name": "SA Telegram Bot"}}
            },
            {
                "id": "mark-2nd-sent",
                "name": "Mark 2nd Reminder Sent",
                "type": "n8n-nodes-base.postgres",
                "position": [700, 400],
                "parameters": {
                    "operation": "executeQuery",
                    "query": (
                        "UPDATE sa_leads SET second_reminder_sent=TRUE, second_reminder_at=NOW() WHERE id=$1"
                    ),
                    "additionalFields": {"queryParams": "={{ [$json.id] }}"}
                },
                "credentials": {"postgres": {"id": "pg-sa", "name": "SA PostgreSQL"}}
            },
        ],
        "connections": {
            "Every Business Morning (9am)": {"main": [[
                {"node": "Fetch Leads Due 1st Reminder", "type": "main", "index": 0},
                {"node": "Fetch Leads Due 2nd Reminder", "type": "main", "index": 0},
            ]]},
            "Fetch Leads Due 1st Reminder": {"main": [[{"node": "1st Reminder: Telegram",  "type": "main", "index": 0}]]},
            "1st Reminder: Telegram":       {"main": [[{"node": "Mark 1st Reminder Sent",  "type": "main", "index": 0}]]},
            "Fetch Leads Due 2nd Reminder": {"main": [[{"node": "2nd Reminder: Telegram + Email", "type": "main", "index": 0}]]},
            "2nd Reminder: Telegram + Email": {"main": [[{"node": "Mark 2nd Reminder Sent", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
        "staticData": None,
    }


def setup_n8n_workflows(dry_run=False):
    if not N8N_API_KEY:
        print("\n[3/4] Skipping n8n — N8N_API_KEY not set")
        return

    print(f"\n[3/4] Importing n8n workflows to {N8N_BASE_URL} ...")

    workflows = [
        ("SA Contact Lead Pipeline", build_n8n_workflow()),
        ("SA Lead Reminder (Business Day Check)", build_reminder_workflow()),
    ]

    headers = _n8n_headers()

    for name, wf_data in workflows:
        if dry_run:
            print(f"  [dry-run] Would import: {name}")
            continue

        # Check if workflow already exists
        resp = httpx.get(f"{N8N_BASE_URL}/api/v1/workflows", headers=headers)
        resp.raise_for_status()
        existing = next(
            (w for w in resp.json().get("data", []) if w["name"] == name),
            None,
        )

        if existing:
            wf_id = existing["id"]
            resp = httpx.put(
                f"{N8N_BASE_URL}/api/v1/workflows/{wf_id}",
                headers=headers,
                json=wf_data,
            )
            resp.raise_for_status()
            print(f"  ✓ Updated workflow [{wf_id}]: {name}")
        else:
            resp = httpx.post(
                f"{N8N_BASE_URL}/api/v1/workflows",
                headers=headers,
                json=wf_data,
            )
            resp.raise_for_status()
            wf_id = resp.json()["id"]
            print(f"  ✓ Created workflow [{wf_id}]: {name}")

        # Activate the workflow
        httpx.patch(
            f"{N8N_BASE_URL}/api/v1/workflows/{wf_id}",
            headers=headers,
            json={"active": True},
        )


# ── 4. Instructions ─────────────────────────────────────────────────────────────

def print_setup_instructions():
    print("""
[4/4] Manual setup steps required
══════════════════════════════════════════════════════════════════════════

▶ TELEGRAM BOT (one-time)
  1. Open Telegram → search @BotFather → /newbot
  2. Name: SA Notifications  |  Username: sa_notifications_bot (or similar)
  3. Copy the token → add to .env:  TELEGRAM_BOT_TOKEN=<token>
  4. Send a message to your bot, then call:
       curl https://api.telegram.org/bot<TOKEN>/getUpdates
  5. Copy your chat_id → add to .env:  TELEGRAM_CHAT_ID=<chat_id>
  6. In n8n: Credentials → New → Telegram API → paste token

▶ TWILIO (SMS + WhatsApp)
  1. Sign up at twilio.com (free $15 trial credit)
  2. Console → Account SID + Auth Token → add to .env
  3. WhatsApp Sandbox (Develop → Messaging → WhatsApp Sandbox):
       - Send "join <sandbox-keyword>" to +1-415-523-8886 from +12768805651
       - Add to .env:  TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
                       WHATSAPP_NOTIFY_TO=whatsapp:+12768805651
  4. In n8n: Credentials → New → HTTP Basic Auth
       Username: <ACCOUNT_SID>  |  Password: <AUTH_TOKEN>
       Name: SA Twilio

▶ NEO.SPACE SMTP (when credentials available)
  Add to .env:
    NEO_SMTP_HOST=smtp.neo.space
    NEO_SMTP_PORT=587
    NEO_SMTP_USER=relder@sovereignadvisory.ai
    NEO_SMTP_PASS=<password>
  In n8n: Credentials → New → SMTP
    Name: SA Neo.space SMTP
    Host: smtp.neo.space  |  Port: 587  |  User/Pass as above

▶ n8n CREDENTIALS to wire up (Settings → Credentials):
  • SA PostgreSQL  (Postgres: host=postgres user=sa_user db=sa_db etc.)
  • SA Notion      (Notion API: integration token from notion.so/my-integrations)
  • SA Telegram Bot
  • SA Neo.space SMTP
  • SA Twilio      (HTTP Basic Auth)

▶ n8n ENVIRONMENT VARIABLES (Settings → Variables):
  LITELLM_BASE_URL, LITELLM_API_KEY,
  LEAD_REVIEW_BASE_URL, NOTION_LEADS_DB_ID,
  NOTIFY_EMAIL, NOTIFY_SMS_EMAIL,
  TELEGRAM_CHAT_ID, TWILIO_ACCOUNT_SID,
  TWILIO_WHATSAPP_FROM, WHATSAPP_NOTIFY_TO

▶ WEBHOOK URL for index.html
  After activating the "SA Contact Lead Pipeline" workflow in n8n:
  Copy the production webhook URL (looks like:
    https://sovereignadvisory.ai/n8n/webhook/sa-lead-intake)
  Open sovereign_advisory/index.html and set:
    const WEBHOOK_URL = 'https://sovereignadvisory.ai/n8n/webhook/sa-lead-intake';

══════════════════════════════════════════════════════════════════════════
""")


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SA Lead Pipeline setup")
    parser.add_argument("--skip-notion", action="store_true")
    parser.add_argument("--skip-n8n",    action="store_true")
    parser.add_argument("--dry-run",     action="store_true", help="Print actions without executing")
    args = parser.parse_args()

    run_postgres_schema()
    if not args.skip_notion:
        setup_notion(dry_run=args.dry_run)
    if not args.skip_n8n:
        setup_n8n_workflows(dry_run=args.dry_run)
    print_setup_instructions()
    print("\n✓ Setup complete.\n")


if __name__ == "__main__":
    main()
