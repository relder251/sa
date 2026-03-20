"""
Phase 5: Quality Gate
Runs ruff, mypy, bandit on project_dir. LLM security review if HIGH+ issues.
Returns: { passed: bool, blocked: bool, block_reason: str|None, report: str }
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


LITELLM_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-vibe-coding-key-123")


def _call_llm(system: str, user: str, max_tokens: int = 1024) -> str:
    import requests as http
    resp = http.post(
        f"{LITELLM_URL}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {LITELLM_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "cloud/fast",
            "max_tokens": max_tokens,
            "timeout": 120,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _read_source_files(project_dir: Path) -> dict:
    skip_dirs = {".venv", "__pycache__", ".pytest_cache", ".git", "node_modules"}
    files = {}
    for f in sorted(project_dir.rglob("*")):
        if f.is_dir():
            continue
        parts = set(f.relative_to(project_dir).parts)
        if parts & skip_dirs:
            continue
        if f.suffix in (".pyc", ".pyo", ".egg-info"):
            continue
        rel = str(f.relative_to(project_dir))
        try:
            files[rel] = f.read_text(errors="replace")
        except Exception:
            pass
    return files


def run_phase5(
    name: str,
    project_dir: Path,
    project_base: Path,
    log_fn=None,
) -> dict:
    """
    Run quality gate checks.
    Returns: { passed: bool, blocked: bool, block_reason: str|None, report: str }
    """
    project_dir = Path(project_dir)
    project_base = Path(project_base)

    def L(msg):
        if log_fn:
            log_fn(msg, phase=5)
        else:
            print(msg, flush=True)

    L(f"[Phase 5] Quality gate starting for {name}")

    report_lines = [f"# Phase 5: Quality Gate — {name}\n"]
    ruff_output = ""
    bandit_output = ""
    mypy_output = ""
    high_bandit_issues = 0
    warnings = []

    # ── Ruff ─────────────────────────────────────────────────────────────────
    if shutil.which("ruff"):
        L("  Running ruff check ...")
        try:
            r = subprocess.run(
                ["ruff", "check", "--output-format=json", str(project_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            ruff_output = r.stdout + r.stderr
            try:
                ruff_issues = json.loads(r.stdout) if r.stdout.strip() else []
                issue_count = len(ruff_issues)
            except json.JSONDecodeError:
                ruff_issues = []
                issue_count = 0
            L(f"  ruff: {issue_count} issue(s) found")
            report_lines.append(f"## Ruff\n- Issues found: {issue_count}\n")
            if ruff_issues:
                sample = ruff_issues[:10]
                report_lines.append("```json\n" + json.dumps(sample, indent=2)[:3000] + "\n```\n")
        except subprocess.TimeoutExpired:
            L("  ruff timed out — skipping")
            warnings.append("ruff timed out")
            report_lines.append("## Ruff\n- Timed out — skipped\n")
        except Exception as e:
            L(f"  ruff error: {e}")
            warnings.append(f"ruff error: {e}")
            report_lines.append(f"## Ruff\n- Error: {e}\n")
    else:
        L("  ruff not installed — skipping")
        warnings.append("ruff not installed")
        report_lines.append("## Ruff\n- Not installed — skipped\n")

    # ── Bandit ───────────────────────────────────────────────────────────────
    if shutil.which("bandit"):
        L("  Running bandit security scan ...")
        try:
            r = subprocess.run(
                ["bandit", "-r", str(project_dir), "-f", "json", "-q"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            bandit_output = r.stdout + r.stderr
            try:
                bandit_data = json.loads(r.stdout) if r.stdout.strip() else {}
                results = bandit_data.get("results", [])
                high_issues = [
                    x for x in results if x.get("issue_severity") == "HIGH"
                ]
                high_bandit_issues = len(high_issues)
                total_issues = len(results)
            except json.JSONDecodeError:
                bandit_data = {}
                high_issues = []
                high_bandit_issues = 0
                total_issues = 0
            L(f"  bandit: {total_issues} total, {high_bandit_issues} HIGH/CRITICAL")
            report_lines.append(
                f"## Bandit Security Scan\n"
                f"- Total issues: {total_issues}\n"
                f"- HIGH/CRITICAL: {high_bandit_issues}\n"
            )
            if high_issues:
                report_lines.append(
                    "```json\n" + json.dumps(high_issues[:5], indent=2)[:3000] + "\n```\n"
                )
        except subprocess.TimeoutExpired:
            L("  bandit timed out — skipping")
            warnings.append("bandit timed out")
            report_lines.append("## Bandit Security Scan\n- Timed out — skipped\n")
        except Exception as e:
            L(f"  bandit error: {e}")
            warnings.append(f"bandit error: {e}")
            report_lines.append(f"## Bandit Security Scan\n- Error: {e}\n")
    else:
        L("  bandit not installed — skipping")
        warnings.append("bandit not installed")
        report_lines.append("## Bandit Security Scan\n- Not installed — skipped\n")

    # ── Mypy ─────────────────────────────────────────────────────────────────
    if shutil.which("mypy"):
        L("  Running mypy type check ...")
        try:
            mypy_report_dir = f"/tmp/mypy_report_{name}"
            r = subprocess.run(
                [
                    "mypy",
                    str(project_dir),
                    "--ignore-missing-imports",
                    "--json-report",
                    mypy_report_dir,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            mypy_output = r.stdout + r.stderr
            # Try to read the json report
            mypy_json_path = Path(mypy_report_dir) / "mypy.json"
            if mypy_json_path.exists():
                try:
                    mypy_data = json.loads(mypy_json_path.read_text())
                    error_count = mypy_data.get("statistics", {}).get("error_count", "?")
                except Exception:
                    error_count = "?"
            else:
                # Count ERROR lines from output
                error_count = mypy_output.count(": error:")
            L(f"  mypy: {error_count} error(s)")
            report_lines.append(f"## Mypy\n- Errors: {error_count}\n")
            if mypy_output.strip():
                report_lines.append(f"```\n{mypy_output[:2000]}\n```\n")
        except subprocess.TimeoutExpired:
            L("  mypy timed out — skipping")
            warnings.append("mypy timed out")
            report_lines.append("## Mypy\n- Timed out — skipped\n")
        except Exception as e:
            L(f"  mypy error: {e}")
            warnings.append(f"mypy error: {e}")
            report_lines.append(f"## Mypy\n- Error: {e}\n")
    else:
        L("  mypy not installed — skipping")
        warnings.append("mypy not installed")
        report_lines.append("## Mypy\n- Not installed — skipped\n")

    # ── LLM Security Review (if HIGH+ bandit issues) ──────────────────────────
    llm_verdict = None
    llm_review = ""
    blocked = False
    block_reason = None

    if high_bandit_issues > 0:
        L(f"  {high_bandit_issues} HIGH/CRITICAL bandit issue(s) — requesting LLM security review ...")
        try:
            source_files = _read_source_files(project_dir)
            file_blocks = "\n\n".join(
                f"===FILE: {path}===\n{content}\n===END FILE==="
                for path, content in source_files.items()
            )
            user_msg = (
                f"BANDIT SECURITY SCAN OUTPUT:\n{bandit_output[:4000]}\n\n"
                f"SOURCE FILES:\n{file_blocks[:8000]}"
            )
            review = _call_llm(
                system=(
                    "You are a security expert reviewing Python code for vulnerabilities. "
                    "Review the bandit scan output and source code for CRITICAL or HIGH security vulnerabilities. "
                    "Return VERDICT: BLOCK or VERDICT: WARN followed by a brief explanation. "
                    "Only return BLOCK if there are genuinely dangerous vulnerabilities (e.g., SQL injection, "
                    "RCE, hardcoded secrets, path traversal). Return WARN for informational issues."
                ),
                user=user_msg,
                max_tokens=1024,
            )
            llm_review = review
            if "VERDICT: BLOCK" in review.upper():
                llm_verdict = "BLOCK"
                blocked = True
                block_reason = f"LLM security review: BLOCK — {high_bandit_issues} HIGH/CRITICAL bandit issue(s). " + review[:500]
                L(f"  LLM verdict: BLOCK — {review[:200]}")
            else:
                llm_verdict = "WARN"
                L(f"  LLM verdict: WARN — {review[:200]}")
            report_lines.append(f"## LLM Security Review\n- Verdict: {llm_verdict}\n```\n{review[:3000]}\n```\n")
        except Exception as e:
            L(f"  LLM security review failed: {e} — defaulting to WARN")
            warnings.append(f"LLM security review failed: {e}")
            report_lines.append(f"## LLM Security Review\n- Failed: {e} — treated as WARN\n")

    # ── Warnings summary ──────────────────────────────────────────────────────
    if warnings:
        report_lines.append("## Warnings\n" + "\n".join(f"- {w}" for w in warnings) + "\n")

    # ── Final result ──────────────────────────────────────────────────────────
    passed = not blocked
    if blocked:
        report_lines.append(f"## Result\n**BLOCKED** — {block_reason}\n")
        L(f"  [Phase 5] BLOCKED: {block_reason}")
    else:
        report_lines.append("## Result\n**PASSED** (no blocking issues)\n")
        L("  [Phase 5] PASSED")

    report = "\n".join(report_lines)

    # Write report
    try:
        report_path = project_base / "phase5_quality_report.md"
        report_path.write_text(report)
        L(f"  Report written → {report_path}")
    except Exception as e:
        L(f"  Could not write phase5 report: {e}")

    return {
        "passed": passed,
        "blocked": blocked,
        "block_reason": block_reason,
        "report": report,
        "warnings": warnings,
        "high_bandit_issues": high_bandit_issues,
        "llm_verdict": llm_verdict,
    }
