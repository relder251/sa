#!/usr/bin/env python3
"""
csp-scan.py — static CSP coverage analysis for all nginx-served HTML files.
Verifies that every inline style="..." attribute value has a corresponding
SHA-256 hash in the CSP, and that every <style> block hash is current.
Creates Notion tasks for any drift detected.

Run after HTML or CSP config changes, or via daily cron as a safety net.
Usage: python3 scripts/csp-scan.py [--dry-run]
"""
import hashlib, base64, re, sys
from pathlib import Path

sys.path.insert(0, "/opt/agentic-sdlc/scripts")
import notion_ticket

DRY_RUN = "--dry-run" in sys.argv

# Map of HTML file → the nginx CSP config that covers it
SCAN_TARGETS = [
    {
        "label": "public site (sovereignadvisory.ai)",
        "html": "/opt/agentic-sdlc/www/index.html",
        "csp_conf": "/opt/agentic-sdlc/nginx-public/conf.d/snippets/security-headers.conf",
    },
]


def sha256_b64(s: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()


def extract_csp(conf_path: str) -> str:
    """Pull the CSP value from an nginx add_header Content-Security-Policy line."""
    text = Path(conf_path).read_text()
    m = re.search(r'add_header Content-Security-Policy\s+"([^"]+)"', text)
    if not m:
        raise ValueError(f"No CSP found in {conf_path}")
    return m.group(1)


def check_target(target: dict) -> list[str]:
    html_path = target["html"]
    csp_conf = target["csp_conf"]
    label = target["label"]
    issues = []

    html = Path(html_path).read_text()
    csp = extract_csp(csp_conf)

    has_unsafe_hashes = "'unsafe-hashes'" in csp

    # 1. Check <style> block hashes
    for style_block in re.findall(r"<style>(.*?)</style>", html, re.DOTALL):
        h = sha256_b64(style_block)
        if h not in csp:
            issues.append(
                f"<style> block hash mismatch in {html_path}\n"
                f"  Computed: '{h}'\n"
                f"  Not found in CSP in {csp_conf}\n"
                f"  (HTML or CSP was edited without recomputing the hash)"
            )

    # 2. Check inline style attribute coverage
    attr_values = set(re.findall(r'style="([^"]+)"', html))
    if attr_values:
        if not has_unsafe_hashes:
            issues.append(
                f"{len(attr_values)} inline style attributes in {html_path} "
                f"but CSP in {csp_conf} lacks 'unsafe-hashes' — all will be blocked."
            )
        else:
            uncovered = []
            for val in attr_values:
                h = sha256_b64(val)
                if h not in csp:
                    uncovered.append((val, h))
            if uncovered:
                lines = "\n".join(f"  '{h}'  # {v[:60]}" for v, h in uncovered)
                issues.append(
                    f"{len(uncovered)} inline style attribute value(s) in {html_path} "
                    f"not covered by hashes in {csp_conf}:\n{lines}"
                )

    # 3. Warn if JS .style. assignments remain (they can't be statically hashed)
    js_style_assigns = re.findall(r"\.style\.\w+\s*=", html)
    if js_style_assigns:
        issues.append(
            f"{len(js_style_assigns)} JS element.style assignment(s) in {html_path} — "
            f"these cannot be hash-covered; refactor to classList or they will trigger CSP violations: "
            + ", ".join(js_style_assigns[:5])
        )

    return issues


def main():
    print(f"[csp-scan] scanning {len(SCAN_TARGETS)} target(s)"
          + (" (dry-run)" if DRY_RUN else "") + "\n")

    all_clear = True
    for target in SCAN_TARGETS:
        label = target["label"]
        try:
            issues = check_target(target)
        except Exception as e:
            print(f"  ❌  {label}: scan error — {e}")
            all_clear = False
            continue

        if not issues:
            print(f"  ✅  {label}")
            continue

        all_clear = False
        print(f"  ❌  {label}")
        for issue in issues:
            first_line = issue.splitlines()[0]
            print(f"       • {first_line}")
            task_name = f"[auto] CSP drift: {label} — {first_line[:80]}"
            justification = (
                f"Detected by csp-scan.py.\n\n{issue}\n\n"
                f"HTML: {target['html']}\nCSP config: {target['csp_conf']}"
            )
            expected = (
                "All inline style attributes and <style> blocks are covered by "
                "valid SHA-256 hashes in the nginx CSP config; zero CSP violations on page load."
            )
            revert = (
                f"Revert HTML change: git revert <commit>\n"
                f"Or recompute hashes: python3 scripts/csp-scan.py --dry-run to identify missing hashes, "
                f"then update {target['csp_conf']} and reload nginx."
            )
            if not DRY_RUN:
                notion_ticket.create_task(
                    name=task_name,
                    justification=justification,
                    expected_outcome=expected,
                    revert_path=revert,
                    impact="Low",
                    loe="Low",
                    roi="Medium",
                )

    print()
    if all_clear:
        print("[csp-scan] ✅  all targets clean")
    else:
        print("[csp-scan] ❌  drift detected"
              + (" — Notion tasks created" if not DRY_RUN else " — dry-run, no tasks created"))
        sys.exit(1)


if __name__ == "__main__":
    main()
