#!/usr/bin/env python3
"""
check-security-headers.py — scan all private nginx services for duplicate or
missing security headers, then create Notion tasks for any issues found.

Run manually after nginx changes, or via cron as a daily safety net.
Usage: python3 scripts/check-security-headers.py [--dry-run]
"""
import subprocess, sys, re
from collections import defaultdict

sys.path.insert(0, "/opt/agentic-sdlc/scripts")
import notion_ticket

DRY_RUN = "--dry-run" in sys.argv
NGINX_CONTAINER = "sa_nginx_private"

# All private services and the hostnames they serve
SERVICES = [
    "n8n.private.sovereignadvisory.ai",
    "webui.private.sovereignadvisory.ai",
    "litellm.private.sovereignadvisory.ai",
    "jupyter.private.sovereignadvisory.ai",
    "ollama.private.sovereignadvisory.ai",
    "ollama-ui.private.sovereignadvisory.ai",
    "secrets.private.sovereignadvisory.ai",
    "vault.private.sovereignadvisory.ai",
    "kc.private.sovereignadvisory.ai",
    "a0.private.sovereignadvisory.ai",
    "home.private.sovereignadvisory.ai",
    "terminal.private.sovereignadvisory.ai",
    "grafana.private.sovereignadvisory.ai",
    "prometheus.private.sovereignadvisory.ai",
    "qdrant.private.sovereignadvisory.ai",
    "maltego.private.sovereignadvisory.ai",
    "mirofish.private.sovereignadvisory.ai",
]

SECURITY_HEADERS = [
    "content-security-policy",
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
]


def curl_headers(hostname: str) -> dict[str, list[str]]:
    """Return a dict of header-name → [values] for a private service."""
    result = subprocess.run(
        [
            "docker", "exec", NGINX_CONTAINER,
            "curl", "-sk", "-o", "/dev/null", "-D", "-",
            "--connect-timeout", "5",
            "--resolve", f"{hostname}:443:127.0.0.1",
            f"https://{hostname}/",
        ],
        capture_output=True, text=True, timeout=15,
    )
    headers: dict[str, list[str]] = defaultdict(list)
    for line in result.stdout.splitlines():
        line = line.strip()
        if ":" in line:
            name, _, value = line.partition(":")
            name = name.strip().lower()
            if name in SECURITY_HEADERS:
                headers[name].append(value.strip())
    return dict(headers)


def check_service(hostname: str) -> list[str]:
    """Return a list of issue descriptions for this service, or []."""
    try:
        headers = curl_headers(hostname)
    except Exception as e:
        return [f"curl failed: {e}"]

    issues = []

    # Duplicate header check
    for h, values in headers.items():
        if len(values) > 1:
            issues.append(
                f"Duplicate {h} header ({len(values)} copies): "
                + " | ".join(v[:80] for v in values)
            )

    # Missing security header check
    for h in ["content-security-policy", "strict-transport-security",
               "x-content-type-options", "x-frame-options"]:
        if h not in headers:
            issues.append(f"Missing header: {h}")

    return issues


def main():
    print(f"[header-scan] scanning {len(SERVICES)} private services"
          + (" (dry-run)" if DRY_RUN else "") + "\n")

    all_clear = True
    for hostname in SERVICES:
        issues = check_service(hostname)
        if not issues:
            print(f"  ✅  {hostname}")
            continue

        all_clear = False
        print(f"  ❌  {hostname}")
        for issue in issues:
            print(f"       • {issue}")
            task_name = f"[auto] {hostname}: {issue[:80]}"
            justification = (
                f"Detected by check-security-headers.py on {hostname}.\n\n"
                f"Issue: {issue}\n\n"
                f"Run: docker exec {NGINX_CONTAINER} curl -sk -o /dev/null -D - "
                f"--resolve {hostname}:443:127.0.0.1 https://{hostname}/ "
                f"| grep -i 'security\\|csp\\|transport\\|frame\\|referrer'"
            )
            expected = (
                "Single instance of each security header in the response; "
                "no duplicates; all required headers present."
            )
            revert = f"git revert nginx-private config commit + docker exec {NGINX_CONTAINER} nginx -s reload"
            if not DRY_RUN:
                notion_ticket.create_task(
                    name=task_name,
                    justification=justification,
                    expected_outcome=expected,
                    revert_path=revert,
                    impact="Medium",
                    loe="Low",
                    roi="Medium",
                )

    print()
    if all_clear:
        print("[header-scan] ✅  all services clean")
    else:
        print("[header-scan] ❌  issues found" + (" — Notion tasks created" if not DRY_RUN else " — dry-run, no tasks created"))
        sys.exit(1)


if __name__ == "__main__":
    main()
