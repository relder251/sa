#!/usr/bin/env python3
"""Rotate or provision a Twingate connector and update a Docker Compose deployment.

This script uses the official Twingate GraphQL Admin API to:
  1) look up a Remote Network by name
  2) find or create a Connector in that Remote Network
  3) generate a fresh Access/Refresh token pair for that Connector
  4) update a local .env file used by docker compose
  5) recreate the connector service

Required environment variables:
  TWINGATE_API_KEY          Admin API token from Settings > API
  TWINGATE_NETWORK          subdomain only, e.g. "relder"
  TWINGATE_REMOTE_NETWORK   Remote Network display name, e.g. "Homelab Network"

Optional environment variables:
  TWINGATE_CONNECTOR_NAME   Connector display name to create or rotate
  ENV_FILE                  Path to .env file (default: ./.env)
  COMPOSE_DIR               Path to compose project dir (default: current dir)
  COMPOSE_SERVICE           Compose service name for connector (default: twingate)
  TWINGATE_LABEL_HOSTNAME   Optional metadata label value
  DRY_RUN                   If set to 1/true, do not write or deploy
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class Config:
    api_key: str
    network_subdomain: str
    remote_network_name: str
    connector_name: str
    env_file: Path
    compose_dir: Path
    compose_service: str
    label_hostname: str
    dry_run: bool
    create_if_missing: bool
    no_redeploy: bool
    verbose: bool


class TwingateError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_config() -> Config:
    parser = argparse.ArgumentParser(description="Provision/rotate a Twingate connector and update docker-compose env.")
    parser.add_argument("--api-key", default=os.getenv("TWINGATE_API_KEY", ""))
    parser.add_argument("--network", default=os.getenv("TWINGATE_NETWORK", ""), help="Twingate network subdomain only, e.g. relder")
    parser.add_argument("--remote-network", default=os.getenv("TWINGATE_REMOTE_NETWORK", ""), help="Remote Network display name")
    parser.add_argument("--connector-name", default=os.getenv("TWINGATE_CONNECTOR_NAME", "vps-docker-connector"))
    parser.add_argument("--env-file", default=os.getenv("ENV_FILE", ".env"))
    parser.add_argument("--compose-dir", default=os.getenv("COMPOSE_DIR", "."))
    parser.add_argument("--compose-service", default=os.getenv("COMPOSE_SERVICE", "twingate"))
    parser.add_argument("--label-hostname", default=os.getenv("TWINGATE_LABEL_HOSTNAME", ""))
    parser.add_argument("--dry-run", action="store_true", default=str(os.getenv("DRY_RUN", "")).lower() in {"1", "true", "yes"})
    parser.add_argument("--no-create", action="store_true", help="Do not create connector if missing")
    parser.add_argument("--no-redeploy", action="store_true", help="Update env file only; do not restart docker compose")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    missing = []
    if not args.api_key:
        missing.append("TWINGATE_API_KEY / --api-key")
    if not args.network:
        missing.append("TWINGATE_NETWORK / --network")
    if not args.remote_network:
        missing.append("TWINGATE_REMOTE_NETWORK / --remote-network")
    if missing:
        raise SystemExit("Missing required settings: " + ", ".join(missing))

    return Config(
        api_key=args.api_key,
        network_subdomain=args.network.strip().removesuffix(".twingate.com").replace("https://", "").replace("http://", ""),
        remote_network_name=args.remote_network.strip(),
        connector_name=args.connector_name.strip(),
        env_file=Path(args.env_file).expanduser().resolve(),
        compose_dir=Path(args.compose_dir).expanduser().resolve(),
        compose_service=args.compose_service.strip(),
        label_hostname=args.label_hostname.strip(),
        dry_run=args.dry_run,
        create_if_missing=not args.no_create,
        no_redeploy=args.no_redeploy,
        verbose=args.verbose,
    )


def graphql_request(cfg: Config, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"https://{cfg.network_subdomain}.twingate.com/api/graphql/"
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-API-KEY": cfg.api_key,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TwingateError(f"HTTP {exc.code} from Twingate API: {body}") from exc
    except URLError as exc:
        raise TwingateError(f"Network error calling Twingate API: {exc}") from exc

    data = json.loads(body)
    if data.get("errors"):
        raise TwingateError(f"GraphQL error: {json.dumps(data['errors'], indent=2)}")
    return data["data"]


def get_remote_network(cfg: Config) -> Dict[str, Any]:
    query = """
    query ListRemoteNetworks($first: Int!) {
      remoteNetworks(first: $first) {
        edges {
          node {
            id
            name
          }
        }
      }
    }
    """
    data = graphql_request(cfg, query, {"first": 100})
    matches = [e["node"] for e in data["remoteNetworks"]["edges"] if e["node"]["name"] == cfg.remote_network_name]
    if not matches:
        available = sorted(e["node"]["name"] for e in data["remoteNetworks"]["edges"])
        raise TwingateError(
            f"Remote Network '{cfg.remote_network_name}' not found. Available: {', '.join(available) or '(none)'}"
        )
    return matches[0]


def get_connector(cfg: Config, connector_name: str, remote_network_id: str) -> Optional[Dict[str, Any]]:
    query = """
    query ListConnectors($first: Int!) {
      connectors(first: $first) {
        edges {
          node {
            id
            name
            state
            remoteNetwork {
              id
              name
            }
          }
        }
      }
    }
    """
    data = graphql_request(cfg, query, {"first": 200})
    for edge in data["connectors"]["edges"]:
        node = edge["node"]
        if node["name"] == connector_name and node["remoteNetwork"]["id"] == remote_network_id:
            return node
    return None


def create_connector(cfg: Config, connector_name: str, remote_network_id: str) -> Dict[str, Any]:
    mutation = """
    mutation CreateConnector($name: String!, $remoteNetworkId: ID!) {
      connectorCreate(name: $name, remoteNetworkId: $remoteNetworkId) {
        ok
        error
        entity {
          id
          name
          state
          remoteNetwork { id name }
        }
      }
    }
    """
    data = graphql_request(cfg, mutation, {"name": connector_name, "remoteNetworkId": remote_network_id})
    result = data["connectorCreate"]
    if not result["ok"]:
        raise TwingateError(f"connectorCreate failed: {result.get('error') or 'unknown error'}")
    return result["entity"]


def generate_connector_tokens(cfg: Config, connector_id: str) -> Dict[str, str]:
    mutation = """
    mutation GenerateTokens($connectorId: ID!) {
      connectorGenerateTokens(connectorId: $connectorId) {
        ok
        error
        connectorTokens {
          accessToken
          refreshToken
        }
      }
    }
    """
    data = graphql_request(cfg, mutation, {"connectorId": connector_id})
    result = data["connectorGenerateTokens"]
    if not result["ok"]:
        raise TwingateError(f"connectorGenerateTokens failed: {result.get('error') or 'unknown error'}")
    tokens = result.get("connectorTokens") or {}
    if not tokens.get("accessToken") or not tokens.get("refreshToken"):
        raise TwingateError("connectorGenerateTokens succeeded but did not return both tokens")
    return tokens


def update_env_file(env_path: Path, updates: Dict[str, str]) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, str] = {}
    raw_lines = []
    if env_path.exists():
        raw_lines = env_path.read_text(encoding="utf-8").splitlines()
        for line in raw_lines:
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            existing[k] = v

    existing.update(updates)
    order = []
    seen = set()
    new_lines = []
    for line in raw_lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        k, _ = line.split("=", 1)
        if k in seen:
            continue
        seen.add(k)
        new_lines.append(f"{k}={existing[k]}")
        order.append(k)
    for k in updates:
        if k not in seen:
            new_lines.append(f"{k}={existing[k]}")
    content = "\n".join(new_lines).rstrip() + "\n"
    backup = env_path.with_suffix(env_path.suffix + ".bak")
    if env_path.exists():
        backup.write_text(env_path.read_text(encoding="utf-8"), encoding="utf-8")
    env_path.write_text(content, encoding="utf-8")


def run_compose_redeploy(cfg: Config) -> None:
    compose_file = cfg.compose_dir / "docker-compose.yml"
    if not compose_file.exists():
        raise TwingateError(f"docker-compose.yml not found in {cfg.compose_dir}")

    cmds = [
        ["docker", "compose", "up", "-d", "--force-recreate", cfg.compose_service],
        ["docker", "compose", "ps", cfg.compose_service],
        ["docker", "compose", "logs", "--tail=30", cfg.compose_service],
    ]
    for cmd in cmds:
        if cfg.verbose:
            log("+ " + shlex.join(cmd))
        proc = subprocess.run(cmd, cwd=str(cfg.compose_dir), text=True, capture_output=True)
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        if proc.returncode != 0:
            raise TwingateError(f"Command failed ({proc.returncode}): {shlex.join(cmd)}")


def main() -> int:
    cfg = load_config()
    try:
        remote_network = get_remote_network(cfg)
        if cfg.verbose:
            log(f"Using Remote Network: {remote_network['name']} ({remote_network['id']})")

        connector = get_connector(cfg, cfg.connector_name, remote_network["id"])
        if connector:
            action = "rotating existing"
        else:
            if not cfg.create_if_missing:
                raise TwingateError(
                    f"Connector '{cfg.connector_name}' was not found in Remote Network '{cfg.remote_network_name}'"
                )
            connector = create_connector(cfg, cfg.connector_name, remote_network["id"])
            action = "created new"

        tokens = generate_connector_tokens(cfg, connector["id"])
        updates = {
            "TWINGATE_NETWORK": cfg.network_subdomain,
            "TWINGATE_ACCESS_TOKEN": tokens["accessToken"],
            "TWINGATE_REFRESH_TOKEN": tokens["refreshToken"],
        }
        if cfg.label_hostname:
            updates["TWINGATE_LABEL_HOSTNAME"] = cfg.label_hostname

        print(json.dumps({
            "result": "ok",
            "action": action,
            "remote_network": remote_network["name"],
            "connector_name": connector["name"],
            "connector_id": connector["id"],
            "env_file": str(cfg.env_file),
            "compose_dir": str(cfg.compose_dir),
            "compose_service": cfg.compose_service,
            "dry_run": cfg.dry_run,
            "redeploy": not cfg.no_redeploy,
        }, indent=2))

        if cfg.dry_run:
            print("DRY_RUN enabled; not writing .env or redeploying.", file=sys.stderr)
            return 0

        update_env_file(cfg.env_file, updates)
        print(f"Updated {cfg.env_file}")
        if not cfg.no_redeploy:
            run_compose_redeploy(cfg)
        return 0
    except TwingateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
