"""
Phase 7: Git Push / Registry
Commits generated code to git, optionally pushes to remote, optionally builds+pushes Docker image.
"""
import os
import subprocess
from pathlib import Path


def _run(cmd: list, cwd: str = None, timeout: int = 300, env: dict = None) -> tuple:
    """Run subprocess, returns (returncode, stdout+stderr)."""
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
        env=run_env,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def run_phase7(
    name: str,
    project_dir: Path,
    project_base: Path,
    log_fn=None,
) -> dict:
    """
    Commit generated code to git, optionally push to remote and/or build Docker image.
    Returns: { success: bool, commit_hash: str, docker_pushed: bool }
    """
    project_dir = Path(project_dir)
    project_base = Path(project_base)

    DOCKER_REGISTRY = os.environ.get("DOCKER_REGISTRY", "")
    GIT_REMOTE_URL = os.environ.get("GIT_REMOTE_URL", "")

    def L(msg):
        if log_fn:
            log_fn(msg, phase=7)
        else:
            print(msg, flush=True)

    L(f"[Phase 7] Git push starting for {name}")

    commit_hash = ""
    docker_pushed = False
    report_lines = [f"# Phase 7: Git Push — {name}\n"]

    git_env = {
        "GIT_AUTHOR_NAME": "Agentic SDLC",
        "GIT_AUTHOR_EMAIL": "pipeline@agentic-sdlc.local",
        "GIT_COMMITTER_NAME": "Agentic SDLC",
        "GIT_COMMITTER_EMAIL": "pipeline@agentic-sdlc.local",
        "HOME": "/tmp",
    }

    try:
        # ── Init git if needed ───────────────────────────────────────────────
        git_dir = project_dir / ".git"
        if not git_dir.exists():
            L("  Initializing git repository ...")
            rc, out = _run(["git", "init"], cwd=str(project_dir), env=git_env)
            L(f"  git init: {out[:200]}")
            if rc != 0:
                raise RuntimeError(f"git init failed: {out}")

        # ── Configure git user ───────────────────────────────────────────────
        _run(
            ["git", "config", "user.email", "pipeline@agentic-sdlc.local"],
            cwd=str(project_dir),
            env=git_env,
        )
        _run(
            ["git", "config", "user.name", "Agentic SDLC"],
            cwd=str(project_dir),
            env=git_env,
        )

        # ── Stage all files ──────────────────────────────────────────────────
        L("  Staging files ...")
        rc, out = _run(["git", "add", "-A"], cwd=str(project_dir), env=git_env)
        if rc != 0:
            L(f"  git add warning: {out[:200]}")

        # ── Commit ───────────────────────────────────────────────────────────
        L("  Committing ...")
        rc, out = _run(
            [
                "git",
                "commit",
                "-m",
                "feat: initial generated release v1.0.0 [agentic-sdlc]",
            ],
            cwd=str(project_dir),
            env=git_env,
        )
        L(f"  git commit: {out[:300]}")

        if rc != 0 and "nothing to commit" not in out:
            raise RuntimeError(f"git commit failed: {out}")

        # ── Get commit hash ──────────────────────────────────────────────────
        rc, hash_out = _run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_dir),
            env=git_env,
        )
        if rc == 0:
            commit_hash = hash_out.strip()
            L(f"  Commit hash: {commit_hash}")
        else:
            L(f"  Could not get commit hash: {hash_out}")

        report_lines.append(f"## Git\n- Commit: `{commit_hash}`\n- Status: success\n")

    except Exception as e:
        L(f"  [Phase 7] Git operations failed: {e}")
        report_lines.append(f"## Git\n- Status: FAILED\n- Error: {e}\n")
        # Write report and return partial result
        try:
            report_path = project_base / "phase7_git_report.md"
            report_path.write_text("\n".join(report_lines))
        except Exception:
            pass
        return {"success": False, "commit_hash": commit_hash, "docker_pushed": False}

    # ── Docker build+push (optional) ─────────────────────────────────────────
    dockerfile = project_dir / "Dockerfile"
    if DOCKER_REGISTRY and dockerfile.exists():
        L(f"  DOCKER_REGISTRY={DOCKER_REGISTRY} — building Docker image ...")
        image_tag = f"{DOCKER_REGISTRY}/{name}:v1.0.0"
        image_latest = f"{DOCKER_REGISTRY}/{name}:latest"

        try:
            L(f"  docker build -t {image_tag} ...")
            rc, out = _run(
                ["docker", "build", "-t", image_tag, str(project_dir)],
                timeout=600,
            )
            L(f"  docker build: rc={rc} {out[:300]}")
            if rc != 0:
                raise RuntimeError(f"docker build failed: {out}")

            rc, out = _run(["docker", "push", image_tag], timeout=300)
            L(f"  docker push {image_tag}: rc={rc} {out[:200]}")
            if rc != 0:
                raise RuntimeError(f"docker push failed: {out}")

            # Tag and push as latest
            rc, out = _run(["docker", "tag", image_tag, image_latest])
            if rc == 0:
                rc, out = _run(["docker", "push", image_latest], timeout=300)
                L(f"  docker push latest: rc={rc}")

            docker_pushed = True
            report_lines.append(
                f"## Docker\n- Image: `{image_tag}`\n- Pushed: yes\n"
            )
            L(f"  Docker image pushed: {image_tag}")

        except Exception as e:
            L(f"  Docker build/push failed: {e}")
            report_lines.append(f"## Docker\n- Status: FAILED\n- Error: {e}\n")
    elif DOCKER_REGISTRY and not dockerfile.exists():
        L("  DOCKER_REGISTRY set but no Dockerfile found — skipping Docker build")
        report_lines.append("## Docker\n- No Dockerfile found — skipped\n")
    else:
        L("  DOCKER_REGISTRY not set — skipping Docker build")
        report_lines.append("## Docker\n- DOCKER_REGISTRY not configured — skipped\n")

    # ── Git remote push (optional) ────────────────────────────────────────────
    if GIT_REMOTE_URL:
        remote_url = f"{GIT_REMOTE_URL.rstrip('/')}/{name}.git"
        L(f"  GIT_REMOTE_URL set — pushing to {remote_url} ...")
        try:
            # Check if remote already exists
            rc, remotes = _run(
                ["git", "remote", "-v"],
                cwd=str(project_dir),
                env=git_env,
            )
            if "origin" not in remotes:
                rc, out = _run(
                    ["git", "remote", "add", "origin", remote_url],
                    cwd=str(project_dir),
                    env=git_env,
                )
                L(f"  git remote add: {out[:200]}")

            # Attempt push
            rc, out = _run(
                ["git", "push", "-u", "origin", "main"],
                cwd=str(project_dir),
                timeout=120,
                env=git_env,
            )
            if rc == 0:
                L(f"  git push to {remote_url}: success")
                report_lines.append(f"## Remote Push\n- URL: `{remote_url}`\n- Status: pushed\n")
            else:
                # Try with master branch
                rc2, out2 = _run(
                    ["git", "push", "-u", "origin", "master"],
                    cwd=str(project_dir),
                    timeout=120,
                    env=git_env,
                )
                if rc2 == 0:
                    L(f"  git push (master) to {remote_url}: success")
                    report_lines.append(f"## Remote Push\n- URL: `{remote_url}`\n- Status: pushed (master)\n")
                else:
                    L(f"  git push failed (non-fatal): {out[:200]}")
                    report_lines.append(
                        f"## Remote Push\n- URL: `{remote_url}`\n- Status: FAILED (non-fatal)\n- Error: {out[:300]}\n"
                    )
        except Exception as e:
            L(f"  Git remote push failed (non-fatal): {e}")
            report_lines.append(f"## Remote Push\n- Status: FAILED (non-fatal)\n- Error: {e}\n")
    else:
        L("  GIT_REMOTE_URL not set — skipping remote push")
        report_lines.append("## Remote Push\n- GIT_REMOTE_URL not configured — skipped\n")

    # ── Write report ──────────────────────────────────────────────────────────
    report = "\n".join(report_lines)
    try:
        report_path = project_base / "phase7_git_report.md"
        report_path.write_text(report)
        L(f"  Report written → {report_path}")
    except Exception as e:
        L(f"  Could not write phase7 report: {e}")

    L(f"[Phase 7] Done — commit={commit_hash} docker_pushed={docker_pushed}")

    return {
        "success": True,
        "commit_hash": commit_hash,
        "docker_pushed": docker_pushed,
    }
