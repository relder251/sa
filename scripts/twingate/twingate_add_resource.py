#!/usr/bin/env python3
"""Add a Twingate Resource via the GraphQL Admin API.

This script uses the official Twingate GraphQL Admin API to:
  1) look up a Remote Network by name
  2) optionally look up a Group by name to grant access
  3) create a Resource in that Remote Network with ALLOW_ALL tcp/udp policies

Required environment variables:
  TWINGATE_API_KEY          Admin API token from Settings > API
  TWINGATE_NETWORK          subdomain only, e.g. "relder"
  TWINGATE_REMOTE_NETWORK   Remote Network display name, e.g. "Homelab Network"

Optional environment variables:
  DRY_RUN                   If set to 1/true, do not create; only validate and print
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class Config:
    api_key: str
    network_subdomain: str
    remote_network_name: str
    resource_name: str
    address: str
    group_name: str
    dry_run: bool
    verbose: bool


class TwingateError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_config() -> Config:
    parser = argparse.ArgumentParser(description="Add a Twingate Resource via the GraphQL Admin API.")
    parser.add_argument("--api-key", default=os.getenv("TWINGATE_API_KEY", ""))
    parser.add_argument("--network", default=os.getenv("TWINGATE_NETWORK", ""), help="Twingate network subdomain only, e.g. relder")
    parser.add_argument("--remote-network", default=os.getenv("TWINGATE_REMOTE_NETWORK", ""), help="Remote Network display name")
    parser.add_argument("--name", default="", help="Resource display name")
    parser.add_argument("--address", default="", help="Resource address: FQDN or CIDR, e.g. 192.168.1.0/24 or app.internal")
    parser.add_argument("--group", default="", help="Group display name to grant access to the resource (optional)")
    parser.add_argument("--dry-run", action="store_true", default=str(os.getenv("DRY_RUN", "")).lower() in {"1", "true", "yes"})
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    missing = []
    if not args.api_key:
        missing.append("TWINGATE_API_KEY / --api-key")
    if not args.network:
        missing.append("TWINGATE_NETWORK / --network")
    if not args.remote_network:
        missing.append("TWINGATE_REMOTE_NETWORK / --remote-network")
    if not args.name:
        missing.append("--name")
    if not args.address:
        missing.append("--address")
    if missing:
        raise SystemExit("Missing required settings: " + ", ".join(missing))

    return Config(
        api_key=args.api_key,
        network_subdomain=args.network.strip().removesuffix(".twingate.com").replace("https://", "").replace("http://", ""),
        remote_network_name=args.remote_network.strip(),
        resource_name=args.name.strip(),
        address=args.address.strip(),
        group_name=args.group.strip(),
        dry_run=args.dry_run,
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


def get_group(cfg: Config) -> Dict[str, Any]:
    query = """
    query ListGroups($first: Int!) {
      groups(first: $first) {
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
    matches = [e["node"] for e in data["groups"]["edges"] if e["node"]["name"] == cfg.group_name]
    if not matches:
        available = sorted(e["node"]["name"] for e in data["groups"]["edges"])
        raise TwingateError(
            f"Group '{cfg.group_name}' not found. Available: {', '.join(available) or '(none)'}"
        )
    return matches[0]


def create_resource(
    cfg: Config,
    remote_network_id: str,
    group_ids: Optional[List[str]],
) -> Dict[str, Any]:
    mutation = """
    mutation ResourceCreate(
      $name: String!
      $address: String!
      $remoteNetworkId: ID!
      $groupIds: [ID!]
    ) {
      resourceCreate(
        name: $name
        address: $address
        remoteNetworkId: $remoteNetworkId
        groupIds: $groupIds
        protocols: {
          allowIcmp: true
          tcp: { policy: ALLOW_ALL }
          udp: { policy: ALLOW_ALL }
        }
      ) {
        ok
        error
        entity {
          id
          name
          remoteNetwork { id name }
        }
      }
    }
    """
    variables: Dict[str, Any] = {
        "name": cfg.resource_name,
        "address": cfg.address,
        "remoteNetworkId": remote_network_id,
    }
    if group_ids:
        variables["groupIds"] = group_ids

    data = graphql_request(cfg, mutation, variables)
    result = data["resourceCreate"]
    if not result["ok"]:
        raise TwingateError(f"resourceCreate failed: {result.get('error') or 'unknown error'}")
    return result["entity"]


def main() -> int:
    cfg = load_config()
    try:
        remote_network = get_remote_network(cfg)
        if cfg.verbose:
            log(f"Using Remote Network: {remote_network['name']} ({remote_network['id']})")

        group: Optional[Dict[str, Any]] = None
        group_ids: Optional[List[str]] = None
        if cfg.group_name:
            group = get_group(cfg)
            group_ids = [group["id"]]
            if cfg.verbose:
                log(f"Using Group: {group['name']} ({group['id']})")

        if cfg.dry_run:
            print(json.dumps({
                "result": "dry_run",
                "resource_name": cfg.resource_name,
                "address": cfg.address,
                "remote_network": remote_network["name"],
                "remote_network_id": remote_network["id"],
                "group": group["name"] if group else None,
                "group_id": group["id"] if group else None,
                "dry_run": True,
            }, indent=2))
            log("DRY_RUN enabled; not creating resource.")
            return 0

        entity = create_resource(cfg, remote_network["id"], group_ids)

        print(json.dumps({
            "result": "ok",
            "id": entity["id"],
            "name": entity["name"],
            "address": cfg.address,
            "remote_network": entity["remoteNetwork"]["name"],
            "remote_network_id": entity["remoteNetwork"]["id"],
            "group": group["name"] if group else None,
            "group_id": group["id"] if group else None,
            "dry_run": False,
        }, indent=2))
        return 0
    except TwingateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
