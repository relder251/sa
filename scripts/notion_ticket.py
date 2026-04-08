"""
notion_ticket.py — shared utility for creating and deduplicating TODO Tasks in Notion.
Reads NOTION_API_KEY from /opt/agentic-sdlc/.env.prod at runtime.
"""
import json, os, urllib.request, urllib.error
from datetime import datetime, timezone

TODO_DB_ID = "3d8db6519bad4f98b3ee2ecdf60aac54"
NOTION_VERSION = "2022-06-28"
ENV_PATH = "/opt/agentic-sdlc/.env.prod"

_api_key: str | None = None


def _load_key() -> str:
    global _api_key
    if _api_key:
        return _api_key
    with open(ENV_PATH) as f:
        for line in f:
            if line.startswith("NOTION_API_KEY="):
                _api_key = line.strip().split("=", 1)[1]
                return _api_key
    raise RuntimeError("NOTION_API_KEY not found in " + ENV_PATH)


def _request(method: str, path: str, body: dict | None = None) -> dict:
    key = _load_key()
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"https://api.notion.com/v1{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Notion API {method} {path} → {e.code}: {e.read().decode()}") from e


def task_exists(title: str) -> bool:
    """Return True if a Pending or In Progress task with this exact title already exists."""
    result = _request("POST", "/databases/" + TODO_DB_ID + "/query", {
        "filter": {
            "and": [
                {"property": "Name", "title": {"equals": title}},
                {"or": [
                    {"property": "Status", "select": {"equals": "Pending"}},
                    {"property": "Status", "select": {"equals": "In Progress"}},
                ]},
            ]
        },
        "page_size": 1,
    })
    return len(result.get("results", [])) > 0


def create_task(
    name: str,
    justification: str,
    expected_outcome: str,
    group: str = "Group 4 - Security Hardening",
    impact: str = "Medium",
    loe: str = "Low",
    roi: str = "Medium",
    revert_path: str = "",
) -> str | None:
    """Create a Pending task in the TODO Tasks DB. Skips if an open task with this name exists.
    Returns the new page ID, or None if skipped."""
    if task_exists(name):
        print(f"  [notion] task already open: {name!r} — skipping")
        return None

    props: dict = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Status": {"select": {"name": "Pending"}},
        "Group": {"select": {"name": group}},
        "Impact": {"select": {"name": impact}},
        "LOE": {"select": {"name": loe}},
        "ROI": {"select": {"name": roi}},
        "Justification": {"rich_text": [{"text": {"content": justification}}]},
        "Expected Outcome": {"rich_text": [{"text": {"content": expected_outcome}}]},
    }
    if revert_path:
        props["Revert Path"] = {"rich_text": [{"text": {"content": revert_path}}]}

    page = _request("POST", "/pages", {"parent": {"database_id": TODO_DB_ID}, "properties": props})
    page_id = page["id"]
    print(f"  [notion] created task: {name!r} → {page_id}")
    return page_id
