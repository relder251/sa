"""
Phase 9: Monitoring
Post-deployment health checks + Slack notification.
"""
import os
import re
import time
from pathlib import Path


LITELLM_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-vibe-coding-key-123")


def _read_endpoint_from_report(project_base: Path) -> str | None:
    """Try to parse the endpoint from phase8_deploy_report.md."""
    report_path = project_base / "phase8_deploy_report.md"
    if not report_path.exists():
        return None
    content = report_path.read_text(errors="replace")
    m = re.search(r"Endpoint.*?`(http[^`]+)`", content)
    if m:
        return m.group(1)
    return None


def _read_iterations_from_report(project_base: Path) -> int:
    """Try to parse iteration count from phase3/phase4 report."""
    for report_name in ("phase4_report.md", "phase3_report.md"):
        report_path = project_base / report_name
        if report_path.exists():
            content = report_path.read_text(errors="replace")
            m = re.search(r"\*\*Iterations\*\*:\s*(\d+)", content)
            if m:
                return int(m.group(1))
    return 0


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


def run_phase9(
    name: str,
    project_dir: Path,
    project_base: Path,
    endpoint: str = None,
    log_fn=None,
) -> dict:
    """
    Run post-deployment health checks and send Slack notification.
    Returns: { success: bool, healthy: bool, checks_performed: int }
    """
    project_dir = Path(project_dir)
    project_base = Path(project_base)

    SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

    def L(msg):
        if log_fn:
            log_fn(msg, phase=9)
        else:
            print(msg, flush=True)

    L(f"[Phase 9] Monitoring starting for {name}")

    report_lines = [f"# Phase 9: Monitoring — {name}\n"]
    healthy = False
    checks_performed = 0

    # ── Find endpoint ─────────────────────────────────────────────────────────
    if not endpoint:
        endpoint = _read_endpoint_from_report(project_base)

    if not endpoint:
        L("  No endpoint to monitor (phase 8 skipped or no endpoint detected)")
        report_lines.append("## Health Checks\n- No endpoint available — skipped\n")
    else:
        L(f"  Checking endpoint: {endpoint}")
        report_lines.append(f"## Health Checks\n- Endpoint: `{endpoint}`\n")

        # ── Health check loop ─────────────────────────────────────────────────
        max_checks = 5
        check_interval = 10
        check_results = []

        for i in range(1, max_checks + 1):
            try:
                import requests as http
                health_url = endpoint.rstrip("/") + "/health"
                resp = http.get(health_url, timeout=8)
                ok = resp.status_code < 400
                check_results.append({
                    "check": i,
                    "url": health_url,
                    "status_code": resp.status_code,
                    "ok": ok,
                })
                L(f"  Check {i}/{max_checks}: {health_url} → {resp.status_code}")
                checks_performed += 1
                if ok:
                    healthy = True
                    break
            except Exception as e:
                check_results.append({
                    "check": i,
                    "url": endpoint + "/health",
                    "error": str(e),
                    "ok": False,
                })
                L(f"  Check {i}/{max_checks}: error — {e}")
                checks_performed += 1

            if i < max_checks:
                time.sleep(check_interval)

        status_str = "HEALTHY" if healthy else "UNHEALTHY"
        L(f"  Health check result: {status_str} ({checks_performed} checks performed)")
        report_lines.append(
            f"- Checks performed: {checks_performed}\n"
            f"- Result: **{status_str}**\n"
        )
        for cr in check_results:
            if "error" in cr:
                report_lines.append(f"  - Check {cr['check']}: ERROR — {cr['error']}")
            else:
                report_lines.append(f"  - Check {cr['check']}: HTTP {cr['status_code']} ({'ok' if cr['ok'] else 'fail'})")

    # ── Read iteration count from earlier phases ──────────────────────────────
    iterations = _read_iterations_from_report(project_base)

    # ── Slack notification ────────────────────────────────────────────────────
    if SLACK_WEBHOOK_URL:
        L("  Sending Slack notification ...")
        health_icon = "✅" if healthy else "❌"
        endpoint_str = endpoint or "N/A"
        message = (
            f"{health_icon} *Agentic SDLC Pipeline Complete* — `{name}`\n"
            f"Health: {'HEALTHY' if healthy else 'UNHEALTHY/NOT CHECKED'}\n"
            f"Endpoint: {endpoint_str}\n"
            f"Fix iterations (phase 4): {iterations}\n"
            f"Checks performed: {checks_performed}"
        )
        slack_ok = _post_slack(SLACK_WEBHOOK_URL, message)
        if slack_ok:
            L("  Slack notification sent")
            report_lines.append("\n## Slack\n- Notification sent successfully\n")
        else:
            L("  Slack notification failed (non-fatal)")
            report_lines.append("\n## Slack\n- Notification failed\n")
    else:
        L("  SLACK_WEBHOOK_URL not set — skipping Slack notification")
        report_lines.append("\n## Slack\n- SLACK_WEBHOOK_URL not configured — skipped\n")

    # ── Write report ──────────────────────────────────────────────────────────
    report = "\n".join(report_lines)
    try:
        report_path = project_base / "phase9_monitoring.md"
        report_path.write_text(report)
        L(f"  Report written → {report_path}")
    except Exception as e:
        L(f"  Could not write phase9 report: {e}")

    L(f"[Phase 9] Done — healthy={healthy} checks={checks_performed}")

    return {
        "success": True,
        "healthy": healthy,
        "checks_performed": checks_performed,
        "endpoint": endpoint,
    }
