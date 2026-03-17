"""
Phase 10: Human Approval Gate
Sends notification and waits for approval before proceeding to deployment phases.
Runs between phase 6 and phase 7.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_slack(webhook_url: str, message: str) -> bool:
    """Post a message to Slack webhook. Returns True on success."""
    try:
        import requests as http
        resp = http.post(
            webhook_url,
            json={"text": message},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _update_state_approval(project_base: Path, approval_updates: dict) -> None:
    """Update only the approval section of run_state.json."""
    state_path = project_base / "run_state.json"
    if not state_path.exists():
        return
    try:
        state = json.loads(state_path.read_text())
        if "approval" not in state:
            state["approval"] = {}
        state["approval"].update(approval_updates)
        state_path.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


async def run_phase10(
    name: str,
    run_id: str,
    project_base: Path,
    approval_event: asyncio.Event,
    litellm_url: str = None,
    litellm_key: str = None,
    webui_base_url: str = None,
    log_fn=None,
) -> dict:
    """
    Wait for human approval before allowing deployment phases to proceed.
    Returns: { approved: bool, reason: str|None, approved_by: str|None, comment: str|None }
    """
    project_base = Path(project_base)

    ENABLE_APPROVAL_GATE = os.environ.get("ENABLE_APPROVAL_GATE", "false").lower() == "true"
    APPROVAL_TIMEOUT_HOURS = float(os.environ.get("APPROVAL_TIMEOUT_HOURS", "24"))
    SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
    _webui_base_url = webui_base_url or os.environ.get("WEBUI_BASE_URL", "http://localhost:3000")

    def L(msg):
        if log_fn:
            log_fn(msg, phase=10)
        else:
            print(msg, flush=True)

    L(f"[Phase 10] Approval gate for {name} (run_id={run_id})")

    # ── Disabled ──────────────────────────────────────────────────────────────
    if not ENABLE_APPROVAL_GATE:
        L("  ENABLE_APPROVAL_GATE not set — approval gate bypassed")
        return {
            "approved": True,
            "reason": "approval_gate_disabled",
            "approved_by": None,
            "comment": None,
        }

    # ── Send Slack notification ───────────────────────────────────────────────
    if SLACK_WEBHOOK_URL:
        L("  Sending approval request Slack notification ...")
        approve_url = f"{_webui_base_url}/approvals/{run_id}/approve"
        reject_url = f"{_webui_base_url}/approvals/{run_id}/reject"
        timeout_str = f"{APPROVAL_TIMEOUT_HOURS:.0f}h" if APPROVAL_TIMEOUT_HOURS == int(APPROVAL_TIMEOUT_HOURS) else f"{APPROVAL_TIMEOUT_HOURS}h"
        message = (
            f":mag: *Approval Required* — `{name}`\n"
            f"Phase 5 (Quality Gate): :white_check_mark:  Phase 6 (Docs): :white_check_mark:\n"
            f"Approve: {approve_url}\n"
            f"Reject: {reject_url}\n"
            f"Pipeline will auto-reject after {timeout_str}"
        )
        slack_ok = _post_slack(SLACK_WEBHOOK_URL, message)
        if slack_ok:
            L("  Slack approval request sent")
        else:
            L("  Slack notification failed (non-fatal) — continuing to wait")
    else:
        L("  SLACK_WEBHOOK_URL not set — skipping Slack notification")

    # ── Update run_state.json: approval pending ───────────────────────────────
    _update_state_approval(project_base, {
        "status": "pending_approval",
        "requested_at": _now_iso(),
    })
    L(f"  Approval pending — timeout in {APPROVAL_TIMEOUT_HOURS}h")

    # ── Wait for approval event ───────────────────────────────────────────────
    timeout_seconds = APPROVAL_TIMEOUT_HOURS * 3600
    try:
        await asyncio.wait_for(approval_event.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        L(f"  Approval timed out after {APPROVAL_TIMEOUT_HOURS}h")
        _update_state_approval(project_base, {"status": "timed_out"})
        return {
            "approved": False,
            "reason": "timed_out",
            "approved_by": None,
            "comment": None,
        }

    # ── Read approval result from run_state.json ──────────────────────────────
    state_path = project_base / "run_state.json"
    approval_status = "unknown"
    approved_by = None
    comment = None

    try:
        state = json.loads(state_path.read_text())
        approval = state.get("approval", {})
        approval_status = approval.get("status", "unknown")
        approved_by = approval.get("approved_by")
        comment = approval.get("comment")
    except Exception as e:
        L(f"  Could not read run_state.json: {e}")

    if approval_status == "approved":
        L(f"  Approved by {approved_by or 'unknown'}")
        return {
            "approved": True,
            "reason": None,
            "approved_by": approved_by,
            "comment": comment,
        }
    else:
        rejection_reason = comment or approval_status
        L(f"  Rejected/declined — status={approval_status} reason={rejection_reason}")
        return {
            "approved": False,
            "reason": rejection_reason,
            "approved_by": approved_by,
            "comment": comment,
        }
