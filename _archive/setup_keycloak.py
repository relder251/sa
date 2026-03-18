"""
setup_keycloak.py — Idempotent Keycloak realm + client provisioning

Run once after deploying Keycloak (or to re-sync if clients change).
Works against both local (localhost:8080) and production (via SSH tunnel).

Usage:
  python setup_keycloak.py [--kc-url http://localhost:8080] [--dry-run]

Required env vars (from .env):
  KEYCLOAK_ADMIN_PASSWORD
  N8N_OIDC_CLIENT_SECRET
  LEAD_REVIEW_OIDC_CLIENT_SECRET
  LITELLM_OIDC_CLIENT_SECRET
  JUPYTER_OIDC_CLIENT_SECRET

Optional env vars:
  EXTERNAL_BASE_URL   Base URL of your stack as seen by browsers
                      (default: http://localhost — for local dev)
                      (set to https://yourhost.example.com for prod)
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

_root = Path(__file__).resolve().parents[1]
load_dotenv(_root / ".env")

KEYCLOAK_ADMIN_PASSWORD     = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "")
N8N_OIDC_CLIENT_SECRET      = os.environ.get("N8N_OIDC_CLIENT_SECRET", "")
LEAD_REVIEW_OIDC_CLIENT_SECRET = os.environ.get("LEAD_REVIEW_OIDC_CLIENT_SECRET", "")
LITELLM_OIDC_CLIENT_SECRET  = os.environ.get("LITELLM_OIDC_CLIENT_SECRET", "")
JUPYTER_OIDC_CLIENT_SECRET  = os.environ.get("JUPYTER_OIDC_CLIENT_SECRET", "")


def _token(kc_url: str) -> str:
    data = urllib.parse.urlencode({
        "client_id": "admin-cli",
        "username": "admin",
        "password": KEYCLOAK_ADMIN_PASSWORD,
        "grant_type": "password",
    }).encode()
    req = urllib.request.Request(
        f"{kc_url}/realms/master/protocol/openid-connect/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["access_token"]


def _req(method: str, kc_url: str, path: str, data=None, token: str = None):
    url = f"{kc_url}{path}"
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            resp = r.read()
            return r.status, json.loads(resp) if resp else {}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def run(kc_url: str, external_base: str, dry_run: bool):
    print(f"\nKeycloak URL : {kc_url}")
    print(f"External base: {external_base}")
    print(f"Dry run      : {dry_run}\n")

    if dry_run:
        print("[dry-run] Would configure Keycloak — no changes made.")
        return

    tok = _token(kc_url)
    print("[OK] Admin token obtained")

    # ── Realm ─────────────────────────────────────────────────────────────────
    realm_payload = {
        "realm": "agentic-sdlc",
        "displayName": "Agentic SDLC",
        "enabled": True,
        "registrationAllowed": False,
        "resetPasswordAllowed": True,
        "rememberMe": True,
        "verifyEmail": False,
        "loginWithEmailAllowed": True,
        "accessTokenLifespan": 3600,
        "ssoSessionMaxLifespan": 86400,
    }
    code, _ = _req("POST", kc_url, "/admin/realms", realm_payload, tok)
    print(f"[{'OK' if code in (201, 409) else 'FAIL'}] Realm 'agentic-sdlc': "
          f"{'created' if code == 201 else 'exists' if code == 409 else f'error {code}'}")

    # ── Clients ───────────────────────────────────────────────────────────────
    clients = [
        {
            "clientId": "n8n",
            "name": "n8n Workflow Engine",
            "secret": N8N_OIDC_CLIENT_SECRET,
            "redirectUris": [
                f"{external_base}:5678/*",
                "http://localhost:5678/*",
                "http://n8n:5678/*",
            ],
            "webOrigins": [f"{external_base}:5678", "http://localhost:5678"],
        },
        {
            "clientId": "litellm",
            "name": "LiteLLM Proxy UI",
            "secret": LITELLM_OIDC_CLIENT_SECRET,
            "redirectUris": [
                f"{external_base}:4001/*",
                "http://localhost:4001/*",
                "http://localhost:4000/*",
            ],
            "webOrigins": [f"{external_base}:4001", "http://localhost:4001"],
        },
        {
            "clientId": "jupyter",
            "name": "JupyterLab",
            "secret": JUPYTER_OIDC_CLIENT_SECRET,
            "redirectUris": [
                f"{external_base}:8889/*",
                "http://localhost:8889/*",
                "http://localhost:8888/*",
            ],
            "webOrigins": [f"{external_base}:8889", "http://localhost:8889"],
        },
        {
            "clientId": "lead-review",
            "name": "SA Lead Review Portal",
            "secret": LEAD_REVIEW_OIDC_CLIENT_SECRET,
            "redirectUris": [
                f"{external_base}:5003/*",
                "http://localhost:5003/*",
                "https://sovereignadvisory.ai/*",
            ],
            "webOrigins": [
                f"{external_base}:5003",
                "http://localhost:5003",
                "https://sovereignadvisory.ai",
            ],
        },
    ]

    for c in clients:
        payload = {
            "clientId": c["clientId"],
            "name": c["name"],
            "enabled": True,
            "clientAuthenticatorType": "client-secret",
            "secret": c["secret"],
            "redirectUris": c["redirectUris"],
            "webOrigins": c["webOrigins"],
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled": False,
            "publicClient": False,
            "protocol": "openid-connect",
            "attributes": {"pkce.code.challenge.method": "S256"},
        }
        code, _ = _req("POST", kc_url, "/admin/realms/agentic-sdlc/clients", payload, tok)
        status = "created" if code == 201 else "exists" if code == 409 else f"error {code}"
        print(f"[{'OK' if code in (201, 409) else 'FAIL'}] Client '{c['clientId']}': {status}")

    # ── Summary ───────────────────────────────────────────────────────────────
    _, clients_resp = _req("GET", kc_url, "/admin/realms/agentic-sdlc/clients?max=20", token=tok)
    custom = [c["clientId"] for c in clients_resp
              if isinstance(clients_resp, list) and not c["clientId"].startswith("realm-")
              and c["clientId"] not in ("account", "account-console", "admin-cli", "broker",
                                        "security-admin-console")]
    print(f"\n[OK] Custom clients in agentic-sdlc realm: {custom}")
    print("\nNext steps:")
    print("  1. Create users at http://localhost:8080/admin  (realm: agentic-sdlc)")
    print("  2. Set OIDC_ENABLED=true in .env when ready to enable SSO")
    print("  3. Set N8N_SSO_OIDC_ENABLED=true in .env to enable n8n SSO")
    print("  4. docker compose up -d  (restarts affected services)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Provision Keycloak realm and clients")
    parser.add_argument("--kc-url", default="http://localhost:8080", help="Keycloak base URL")
    parser.add_argument("--external-base", default="http://localhost", help="External base URL seen by browsers")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.kc_url, args.external_base, args.dry_run)
