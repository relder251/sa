"""
Phase 6: Documentation Generation
Generates README.md, CHANGELOG.md, optionally API docs.
"""
from pathlib import Path

from phases.utils import call_llm as _call_llm, read_source_files as _read_source_files


def run_phase6(
    name: str,
    project_dir: Path,
    project_base: Path,
    log_fn=None,
) -> dict:
    """
    Generate documentation for the project.
    Returns: { success: bool, docs_written: list[str] }
    """
    project_dir = Path(project_dir)
    project_base = Path(project_base)

    def L(msg):
        if log_fn:
            log_fn(msg, phase=6)
        else:
            print(msg, flush=True)

    L(f"[Phase 6] Documentation generation starting for {name}")

    docs_written = []
    docs_dir = project_base / "phase6_docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    # ── Read source files ────────────────────────────────────────────────────
    L("  Reading source files ...")
    source_files = _read_source_files(project_dir)
    file_blocks = "\n\n".join(
        f"===FILE: {path}===\n{content}\n===END FILE==="
        for path, content in source_files.items()
    )
    L(f"  Read {len(source_files)} source files")

    # ── Read project plan ────────────────────────────────────────────────────
    plan = ""
    plan_path = project_base / "project_plan.md"
    if plan_path.exists():
        plan = plan_path.read_text(errors="replace")
        L(f"  Read project_plan.md ({len(plan)} chars)")
    else:
        L("  project_plan.md not found — proceeding without plan")

    # ── Generate README.md ───────────────────────────────────────────────────
    L("  Generating README.md ...")
    readme_content = ""
    try:
        readme_content = _call_llm(
            model="cloud/fast",
            system=(
                "Generate a professional README.md for this project. "
                "Include: project description, features, installation, usage, "
                "API reference if applicable, testing. "
                "Use GitHub Markdown with badges placeholder. "
                "Make it comprehensive and developer-friendly."
            ),
            user=f"PROJECT PLAN:\n{plan}\n\nSOURCE FILES:\n{file_blocks[:12000]}",
            max_tokens=2048,
        )
        readme_path = docs_dir / "README.md"
        readme_path.write_text(readme_content)
        docs_written.append(str(readme_path))
        L(f"  README.md written ({len(readme_content)} chars)")
    except Exception as e:
        L(f"  README.md generation failed: {e}")
        # Write a minimal README as fallback
        readme_content = f"# {name}\n\nGenerated project.\n\n## Installation\n\n```bash\npip install -r requirements.txt\n```\n\n## Usage\n\nSee source files for details.\n"
        readme_path = docs_dir / "README.md"
        readme_path.write_text(readme_content)
        docs_written.append(str(readme_path))
        L("  Wrote minimal fallback README.md")

    # ── Generate CHANGELOG.md ────────────────────────────────────────────────
    L("  Generating CHANGELOG.md ...")
    try:
        changelog_content = _call_llm(
            model="free/fast",
            system=(
                "Generate a CHANGELOG.md in Keep a Changelog format for an initial v1.0.0 release. "
                "Use the standard format with sections: Added, Changed, Deprecated, Removed, Fixed, Security."
            ),
            user=f"PROJECT: {name}\n\nFEATURES FROM PLAN:\n{plan[:2000]}",
            max_tokens=1024,
        )
        changelog_path = docs_dir / "CHANGELOG.md"
        changelog_path.write_text(changelog_content)
        docs_written.append(str(changelog_path))
        L(f"  CHANGELOG.md written ({len(changelog_content)} chars)")
    except Exception as e:
        L(f"  CHANGELOG.md generation failed: {e}")
        # Write a minimal CHANGELOG as fallback
        changelog_content = (
            "# Changelog\n\nAll notable changes to this project will be documented in this file.\n\n"
            "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).\n\n"
            "## [1.0.0] - Unreleased\n\n### Added\n- Initial release\n"
        )
        changelog_path = docs_dir / "CHANGELOG.md"
        changelog_path.write_text(changelog_content)
        docs_written.append(str(changelog_path))
        L("  Wrote minimal fallback CHANGELOG.md")

    # ── Copy README.md to project root ───────────────────────────────────────
    try:
        project_readme = project_dir / "README.md"
        project_readme.write_text(readme_content)
        L(f"  README.md copied to project root → {project_readme}")
    except Exception as e:
        L(f"  Could not copy README.md to project root: {e}")

    # ── Write phase6 report ───────────────────────────────────────────────────
    report_lines = [
        f"# Phase 6: Documentation Generation — {name}\n",
        f"## Summary\n",
        f"- Docs directory: `{docs_dir}`\n",
        f"- Files generated: {len(docs_written)}\n",
        "## Generated Files\n",
    ]
    for d in docs_written:
        report_lines.append(f"- `{d}`")
    report_lines.append("\n## README Preview\n")
    report_lines.append(f"```markdown\n{readme_content[:1000]}\n...\n```\n")
    report = "\n".join(report_lines)

    try:
        report_path = project_base / "phase6_report.md"
        report_path.write_text(report)
        L(f"  Report written → {report_path}")
    except Exception as e:
        L(f"  Could not write phase6 report: {e}")

    L(f"[Phase 6] Done — {len(docs_written)} docs written")

    return {
        "success": True,
        "docs_written": docs_written,
        "docs_dir": str(docs_dir),
    }
