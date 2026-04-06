#!/usr/bin/env python3
import json
import os
import sys
from typing import Optional

import requests

NETWORK = os.environ["TWINGATE_NETWORK"]
API_KEY = os.environ["TWINGATE_API_KEY"]

REMOTE_NETWORK_NAME = os.environ.get("TG_REMOTE_NETWORK", "Homelab Network")
RESOURCE_NAME = os.environ.get("TG_RESOURCE_NAME", "Private Apps")
RESOURCE_ADDRESS = os.environ.get("TG_RESOURCE_ADDRESS", "127.0.0.1")
RESOURCE_ALIAS = os.environ.get("TG_RESOURCE_ALIAS", "private.sovereignadvisory.ai")
DEFAULT_GROUP_NAME = os.environ.get("TG_DEFAULT_GROUP", "Everyone")

API_URL = f"https://{NETWORK}.twingate.com/api/graphql/"
HEADERS = {
    "X-API-KEY": API_KEY,
    "Content-Type": "application/json",
}


def gql(query: str, variables: dict) -> dict:
    r = requests.post(
        API_URL,
        headers=HEADERS,
        json={"query": query, "variables": variables},
        timeout=30,
    )
    try:
        payload = r.json()
    except Exception:
        print(r.text, file=sys.stderr)
        r.raise_for_status()
        raise

    if r.status_code >= 400:
        print(json.dumps(payload, indent=2), file=sys.stderr)
        r.raise_for_status()

    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], indent=2))
    return payload["data"]


def get_remote_network_id(name: str) -> str:
    query = """
    query($name: String!) {
      remoteNetworks(filter: { name: { eq: $name } }) {
        edges {
          node {
            id
            name
          }
        }
      }
    }
    """
    data = gql(query, {"name": name})
    edges = data["remoteNetworks"]["edges"]
    if not edges:
        raise RuntimeError(f"Remote Network not found: {name}")
    return edges[0]["node"]["id"]


def get_group_id(name: str) -> str:
    query = """
    query($name: String!) {
      groups(filter: { name: { eq: $name } }) {
        edges {
          node {
            id
            name
          }
        }
      }
    }
    """
    data = gql(query, {"name": name})
    edges = data["groups"]["edges"]
    if not edges:
        raise RuntimeError(f"Group not found: {name}")
    return edges[0]["node"]["id"]


def get_resource_by_name(name: str) -> Optional[dict]:
    query = """
    query($name: String!) {
      resources(filter: { name: { eq: $name } }) {
        edges {
          node {
            id
            name
            alias
            groups(first: 100) {
              edges {
                node {
                  id
                  name
                }
              }
            }
          }
        }
      }
    }
    """
    data = gql(query, {"name": name})
    edges = data["resources"]["edges"]
    return edges[0]["node"] if edges else None


def create_resource(remote_network_id: str, default_group_id: str) -> dict:
    mutation = """
    mutation(
      $name: String!,
      $address: String!,
      $alias: String,
      $remoteNetworkId: ID!,
      $groupIds: [ID!]
    ) {
      resourceCreate(
        name: $name,
        address: $address,
        alias: $alias,
        remoteNetworkId: $remoteNetworkId,
        groupIds: $groupIds
      ) {
        ok
        error
        entity {
          id
          name
          alias
        }
      }
    }
    """
    variables = {
        "name": RESOURCE_NAME,
        "address": RESOURCE_ADDRESS,
        "alias": RESOURCE_ALIAS,
        "remoteNetworkId": remote_network_id,
        "groupIds": [default_group_id],
    }
    return gql(mutation, variables)["resourceCreate"]


def update_resource(resource_id: str, remote_network_id: str, add_group_id: str) -> dict:
    mutation = """
    mutation(
      $id: ID!,
      $name: String!,
      $address: String!,
      $alias: String,
      $remoteNetworkId: ID!,
      $addedGroupIds: [ID!]
    ) {
      resourceUpdate(
        id: $id,
        name: $name,
        address: $address,
        alias: $alias,
        remoteNetworkId: $remoteNetworkId,
        addedGroupIds: $addedGroupIds
      ) {
        ok
        error
        entity {
          id
          name
          alias
        }
      }
    }
    """
    variables = {
        "id": resource_id,
        "name": RESOURCE_NAME,
        "address": RESOURCE_ADDRESS,
        "alias": RESOURCE_ALIAS,
        "remoteNetworkId": remote_network_id,
        "addedGroupIds": [add_group_id],
    }
    return gql(mutation, variables)["resourceUpdate"]


def main():
    rn_id = get_remote_network_id(REMOTE_NETWORK_NAME)
    everyone_id = get_group_id(DEFAULT_GROUP_NAME)
    existing = get_resource_by_name(RESOURCE_NAME)

    if existing:
        existing_group_ids = {
            edge["node"]["id"]
            for edge in existing.get("groups", {}).get("edges", [])
        }
        result = update_resource(existing["id"], rn_id, add_group_id=everyone_id)
        action = "updated"
        access = "already had Everyone" if everyone_id in existing_group_ids else "added Everyone"
    else:
        result = create_resource(rn_id, everyone_id)
        action = "created"
        access = "assigned Everyone"

    if not result["ok"]:
        raise RuntimeError(result.get("error") or "Unknown API error")

    print(json.dumps({
        "action": action,
        "resource": result["entity"],
        "default_group": DEFAULT_GROUP_NAME,
        "access": access,
    }, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
