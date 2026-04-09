#!/usr/bin/env python3
"""Budget Alert Script for LiteLLM Proxy.

Queries LiteLLM spend data and alerts when any tier key is approaching
its budget limit (>80% spent). Designed to run via cron/ofelia.

Usage:
    python3 budget_alert.py [--threshold 0.8] [--verbose]
"""
import json
import sys
import urllib.request
from datetime import datetime

# Configuration
LITELLM_BASE = "http://localhost:4000"
MASTER_KEY = "sk-sa-prod-ce5d031e2a50ffa45d3a200c037971f81853e27ed19b894bc3630625cba0b71a"
TIER_KEYS_FILE = "/opt/agentic-sdlc/tier_keys.json"
ALERT_THRESHOLD = 0.80  # 80% of budget


def api_get(path):
    """Make a GET request to LiteLLM API."""
    req = urllib.request.Request(
        "{}{}".format(LITELLM_BASE, path),
        headers={"Authorization": "Bearer {}".format(MASTER_KEY)}
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def get_key_spend_info():
    """Get spend info for all tier keys."""
    try:
        with open(TIER_KEYS_FILE) as f:
            tier_keys = json.load(f)
    except FileNotFoundError:
        print("ERROR: {} not found".format(TIER_KEYS_FILE))
        return []

    results = []
    for alias, info in tier_keys.items():
        if "error" in info:
            continue
        key = info.get("key", "")
        max_budget = info.get("max_budget")
        if not key:
            continue

        spend = 0
        try:
            req = urllib.request.Request(
                "{}/key/info?key={}".format(LITELLM_BASE, key),
                headers={"Authorization": "Bearer {}".format(MASTER_KEY)}
            )
            resp = urllib.request.urlopen(req, timeout=15)
            key_info = json.loads(resp.read())
            ki = key_info.get("info", key_info)
            spend = ki.get("spend", 0) or 0
        except Exception as e:
            print("  WARNING: Could not get spend for {}: {}".format(alias, e))

        pct = (spend / max_budget * 100) if max_budget else 0
        results.append({
            "alias": alias,
            "tier": info.get("tier", "unknown"),
            "key": key,
            "max_budget": max_budget,
            "spend": spend,
            "pct": pct
        })

    return results


def main():
    threshold = ALERT_THRESHOLD
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    for arg in sys.argv[1:]:
        if arg.startswith("--threshold="):
            threshold = float(arg.split("=")[1])

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("")
    print("=== LiteLLM Budget Check @ {} ===".format(now))
    print("Alert threshold: {:.0f}%".format(threshold * 100))
    print("")

    # Check global spend
    try:
        global_spend = api_get("/global/spend")
        total = global_spend.get("total_spend", global_spend.get("spend", 0)) or 0
        global_max = 200.0  # from config
        pct = total / global_max * 100
        print("Global spend: ${:.4f} / ${:.2f} ({:.1f}%)".format(total, global_max, pct))
        if pct >= threshold * 100:
            print("  WARNING: Global spend at {:.1f}% of ${:.0f} cap!".format(pct, global_max))
    except Exception as e:
        print("  WARNING: Could not get global spend: {}".format(e))

    # Check per-key spend
    print("")
    print("--- Per-Tier Key Budget Status ---")
    keys = get_key_spend_info()
    alerts = []

    for k in keys:
        budget_str = "${:.0f}".format(k["max_budget"]) if k["max_budget"] else "unlimited"
        pct_str = "{:.1f}%".format(k["pct"]) if k["max_budget"] else "n/a"
        status = "OK"

        if k["max_budget"] and k["pct"] >= 95:
            status = "CRITICAL"
            alerts.append(k)
        elif k["max_budget"] and k["pct"] >= threshold * 100:
            status = "WARNING"
            alerts.append(k)

        if verbose or status != "OK":
            print("  [{}] {}: ${:.4f} / {} ({})".format(status, k["alias"], k["spend"], budget_str, pct_str))

    if not alerts:
        print("  All {} keys within budget.".format(len(keys)))
    else:
        print("")
        print("WARNING: {} key(s) approaching budget limit!".format(len(alerts)))
        msg_lines = ["LiteLLM Budget Alert @ {}".format(now)]
        for a in alerts:
            msg_lines.append("  {}: ${:.2f} / ${:.0f} ({:.1f}%)".format(
                a["alias"], a["spend"], a["max_budget"], a["pct"]
            ))
        alert_msg = chr(10).join(msg_lines)
        print(alert_msg)
        print("")
        print("ALERT_MSG={}".format(alert_msg))

    print("")
    print("=== Budget check complete ===")
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
