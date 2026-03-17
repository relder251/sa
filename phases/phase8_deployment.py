"""
Phase 8: Deployment
Deploys the containerized project based on DEPLOY_TARGET env var.
Supports: local (docker run), ssh (remote docker compose), skip (default when no target configured).
"""
import json
import os
import subprocess
import time
from pathlib import Path


def _run(cmd: list, cwd: str = None, timeout: int = 120, input_data: str = None) -> tuple:
    """Run subprocess, returns (returncode, stdout+stderr)."""
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
        input=input_data,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def _get_image_name(name: str, project_base: Path) -> str:
    """Try to read image name from phase7 report, otherwise construct it."""
    DOCKER_REGISTRY = os.environ.get("DOCKER_REGISTRY", "")
    report_path = project_base / "phase7_git_report.md"
    if report_path.exists():
        content = report_path.read_text(errors="replace")
        import re
        m = re.search(r"`([^`]+:[^`]+)`", content)
        if m:
            image = m.group(1)
            # Only return if it looks like an image name (contains colon for tag)
            if ":" in image and "/" in image:
                return image
    if DOCKER_REGISTRY:
        return f"{DOCKER_REGISTRY}/{name}:v1.0.0"
    return f"{name}:v1.0.0"


def run_phase8(
    name: str,
    project_dir: Path,
    project_base: Path,
    log_fn=None,
) -> dict:
    """
    Deploy the project based on DEPLOY_TARGET.
    Returns: { success: bool, endpoint: str|None, deploy_target: str }
    """
    project_dir = Path(project_dir)
    project_base = Path(project_base)

    DEPLOY_TARGET = os.environ.get("DEPLOY_TARGET", "skip").strip().lower()
    DEPLOY_SSH_HOST = os.environ.get("DEPLOY_SSH_HOST", "")
    DEPLOY_SSH_USER = os.environ.get("DEPLOY_SSH_USER", "")
    DEPLOY_SSH_KEY_PATH = os.environ.get("DEPLOY_SSH_KEY_PATH", "")

    def L(msg):
        if log_fn:
            log_fn(msg, phase=8)
        else:
            print(msg, flush=True)

    L(f"[Phase 8] Deployment starting for {name} (target={DEPLOY_TARGET})")

    report_lines = [f"# Phase 8: Deployment — {name}\n", f"- Deploy target: `{DEPLOY_TARGET}`\n"]
    endpoint = None

    # ── Skip ─────────────────────────────────────────────────────────────────
    if not DEPLOY_TARGET or DEPLOY_TARGET == "skip":
        L("  Deployment skipped — DEPLOY_TARGET not configured")
        report_lines.append("## Result\n- Skipped (DEPLOY_TARGET not set or 'skip')\n")
        try:
            report_path = project_base / "phase8_deploy_report.md"
            report_path.write_text("\n".join(report_lines))
        except Exception:
            pass
        return {"success": True, "endpoint": None, "deploy_target": DEPLOY_TARGET}

    # ── Local Docker ──────────────────────────────────────────────────────────
    if DEPLOY_TARGET == "local":
        dockerfile = project_dir / "Dockerfile"
        if not dockerfile.exists():
            L("  No Dockerfile found — skipping local deployment")
            report_lines.append("## Result\n- No Dockerfile found — skipped\n")
            try:
                report_path = project_base / "phase8_deploy_report.md"
                report_path.write_text("\n".join(report_lines))
            except Exception:
                pass
            return {"success": True, "endpoint": None, "deploy_target": DEPLOY_TARGET}

        image = _get_image_name(name, project_base)
        container_name = f"{name}_deployed"
        L(f"  Starting container {container_name} from image {image} ...")

        try:
            # Remove existing container if present
            _run(["docker", "rm", "-f", container_name], timeout=30)

            # Run container
            rc, out = _run(
                [
                    "docker", "run", "-d",
                    "--name", container_name,
                    "-p", "0:8000",
                    image,
                ],
                timeout=60,
            )
            if rc != 0:
                raise RuntimeError(f"docker run failed: {out}")

            container_id = out.strip().splitlines()[-1] if out.strip() else "unknown"
            L(f"  Container started: {container_id[:12]}")

            # Poll for up to 30s
            host_port = None
            deadline = time.time() + 30
            while time.time() < deadline:
                rc2, inspect_out = _run(
                    ["docker", "inspect", container_name],
                    timeout=10,
                )
                if rc2 == 0 and inspect_out.strip():
                    try:
                        inspect_data = json.loads(inspect_out)
                        ports_raw = (
                            inspect_data[0]
                            .get("NetworkSettings", {})
                            .get("Ports", {})
                        )
                        for port_key, bindings in ports_raw.items():
                            if bindings:
                                host_port = bindings[0].get("HostPort")
                                if host_port:
                                    break
                    except Exception:
                        pass

                if host_port:
                    break
                time.sleep(2)

            if host_port:
                endpoint = f"http://localhost:{host_port}"
                L(f"  Container running at {endpoint}")
                report_lines.append(
                    f"## Result\n"
                    f"- Container ID: `{container_id[:12]}`\n"
                    f"- Host port: `{host_port}`\n"
                    f"- Endpoint: `{endpoint}`\n"
                    f"- Status: running\n"
                )
            else:
                L("  Container started but could not determine host port")
                report_lines.append(
                    f"## Result\n"
                    f"- Container ID: `{container_id[:12]}`\n"
                    f"- Host port: unknown\n"
                    f"- Status: started (port detection failed)\n"
                )

        except Exception as e:
            L(f"  Local deployment failed: {e}")
            report_lines.append(f"## Result\n- Status: FAILED\n- Error: {e}\n")
            try:
                report_path = project_base / "phase8_deploy_report.md"
                report_path.write_text("\n".join(report_lines))
            except Exception:
                pass
            return {"success": False, "endpoint": None, "deploy_target": DEPLOY_TARGET}

    # ── SSH ───────────────────────────────────────────────────────────────────
    elif DEPLOY_TARGET == "ssh":
        if not DEPLOY_SSH_HOST:
            L("  DEPLOY_TARGET=ssh but DEPLOY_SSH_HOST not set — skipping")
            report_lines.append("## Result\n- DEPLOY_SSH_HOST not configured — skipped\n")
            try:
                report_path = project_base / "phase8_deploy_report.md"
                report_path.write_text("\n".join(report_lines))
            except Exception:
                pass
            return {"success": True, "endpoint": None, "deploy_target": DEPLOY_TARGET}

        ssh_user = DEPLOY_SSH_USER or "ubuntu"
        ssh_target = f"{ssh_user}@{DEPLOY_SSH_HOST}"
        remote_path = f"/opt/agentic-sdlc/{name}"
        ssh_opts = ["-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
        if DEPLOY_SSH_KEY_PATH:
            ssh_opts += ["-i", DEPLOY_SSH_KEY_PATH]

        L(f"  SSH deployment to {ssh_target}:{remote_path} ...")

        try:
            # Create remote directory
            rc, out = _run(
                ["ssh"] + ssh_opts + [ssh_target, f"mkdir -p {remote_path}"],
                timeout=30,
            )
            if rc != 0:
                raise RuntimeError(f"SSH mkdir failed: {out}")

            # SCP project directory
            L("  Copying project files via scp ...")
            rc, out = _run(
                ["scp", "-r"] + ssh_opts + [str(project_dir), f"{ssh_target}:{remote_path}/"],
                timeout=120,
            )
            if rc != 0:
                raise RuntimeError(f"SCP failed: {out}")
            L("  Files copied")

            # Run docker compose up -d on remote
            project_subdir = project_dir.name
            rc, out = _run(
                [
                    "ssh",
                ] + ssh_opts + [
                    ssh_target,
                    f"cd {remote_path}/{project_subdir} && docker compose up -d 2>&1 || docker-compose up -d 2>&1",
                ],
                timeout=120,
            )
            L(f"  docker compose: rc={rc} {out[:300]}")
            if rc != 0:
                raise RuntimeError(f"docker compose up failed: {out}")

            endpoint = f"http://{DEPLOY_SSH_HOST}:8000"
            L(f"  SSH deployment successful — endpoint: {endpoint}")
            report_lines.append(
                f"## Result\n"
                f"- SSH host: `{DEPLOY_SSH_HOST}`\n"
                f"- Remote path: `{remote_path}`\n"
                f"- Endpoint: `{endpoint}`\n"
                f"- Status: deployed\n"
            )

        except Exception as e:
            L(f"  SSH deployment failed: {e}")
            report_lines.append(f"## Result\n- Status: FAILED\n- Error: {e}\n")
            try:
                report_path = project_base / "phase8_deploy_report.md"
                report_path.write_text("\n".join(report_lines))
            except Exception:
                pass
            return {"success": False, "endpoint": None, "deploy_target": DEPLOY_TARGET}

    else:
        L(f"  Unknown DEPLOY_TARGET={DEPLOY_TARGET!r} — skipping")
        report_lines.append(f"## Result\n- Unknown target `{DEPLOY_TARGET}` — skipped\n")
        try:
            report_path = project_base / "phase8_deploy_report.md"
            report_path.write_text("\n".join(report_lines))
        except Exception:
            pass
        return {"success": True, "endpoint": None, "deploy_target": DEPLOY_TARGET}

    # ── Write report ──────────────────────────────────────────────────────────
    report = "\n".join(report_lines)
    try:
        report_path = project_base / "phase8_deploy_report.md"
        report_path.write_text(report)
        L(f"  Report written → {report_path}")
    except Exception as e:
        L(f"  Could not write phase8 report: {e}")

    L(f"[Phase 8] Done — endpoint={endpoint}")

    return {
        "success": True,
        "endpoint": endpoint,
        "deploy_target": DEPLOY_TARGET,
    }
