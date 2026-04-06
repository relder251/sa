#!/usr/bin/env python3
"""A/B Testing Dashboard — View per-model metrics across tier groups.

Usage:
    docker exec litellm python3 /app/ab_dashboard.py
    docker exec litellm python3 /app/ab_dashboard.py --group cloud/chat
    docker exec litellm python3 /app/ab_dashboard.py --json
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

try:
    import redis
except ImportError:
    print("ERROR: redis package not available. Run inside litellm container.")
    sys.exit(1)


REDIS_HOST = "redis"
REDIS_PORT = 6379


def get_redis() -> redis.Redis:
    """Connect to Redis."""
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True,
        socket_connect_timeout=5,
    )


def scan_ab_keys(r: redis.Redis) -> List[str]:
    """Scan all ab_test:* keys from Redis."""
    keys = []
    cursor = 0
    while True:
        cursor, batch = r.scan(cursor, match="ab_test:*", count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    return sorted(keys)


def parse_metrics(r: redis.Redis, key: str) -> Optional[Dict]:
    """Parse metrics from a Redis hash into a display-friendly dict."""
    data = r.hgetall(key)
    if not data:
        return None

    request_count = int(data.get("request_count", 0))
    if request_count == 0:
        return None

    success_count = int(data.get("success_count", 0))
    failure_count = int(data.get("failure_count", 0))
    total_requests = success_count + failure_count

    total_latency_ms = int(data.get("total_latency_ms", 0))
    avg_latency_s = (total_latency_ms / request_count / 1000) if request_count > 0 else 0

    total_cost_microcents = int(data.get("total_cost_microcents", 0))
    avg_cost = (total_cost_microcents / request_count / 1_000_000) if request_count > 0 else 0

    total_tokens = int(data.get("total_tokens", 0))
    avg_tokens = total_tokens // request_count if request_count > 0 else 0

    success_rate = (success_count / total_requests * 100) if total_requests > 0 else 0

    # Factual verification
    factual_checked = int(data.get("factual_checked", 0))
    factual_passed = int(data.get("factual_passed", 0))
    factual_rate = f"{factual_passed / factual_checked * 100:.1f}%" if factual_checked > 0 else "n/a"

    # Consensus check
    consensus_checked = int(data.get("consensus_checked", 0))
    consensus_balanced = int(data.get("consensus_balanced_count", 0))
    consensus_rate = (
        f"{consensus_balanced / consensus_checked * 100:.0f}% balanced"
        if consensus_checked > 0
        else "n/a"
    )

    return {
        "model": data.get("model_name", "unknown"),
        "group": data.get("group_name", "unknown"),
        "requests": request_count,
        "avg_latency_s": avg_latency_s,
        "avg_cost": avg_cost,
        "avg_tokens": avg_tokens,
        "success_rate": success_rate,
        "factual_rate": factual_rate,
        "consensus_rate": consensus_rate,
        "total_input_tokens": int(data.get("total_input_tokens", 0)),
        "total_output_tokens": int(data.get("total_output_tokens", 0)),
        "total_cost": total_cost_microcents / 1_000_000,
        "failures": failure_count,
        "last_updated": data.get("last_updated", "unknown"),
        "factual_checked": factual_checked,
        "factual_passed": factual_passed,
        "consensus_checked": consensus_checked,
        "consensus_balanced": consensus_balanced,
    }


def group_by_tier(metrics_list: List[Dict]) -> Dict[str, List[Dict]]:
    """Group metrics by tier group."""
    groups = {}
    for m in metrics_list:
        group = m["group"]
        if group not in groups:
            groups[group] = []
        groups[group].append(m)
    # Sort each group by request count descending
    for group in groups:
        groups[group].sort(key=lambda x: x["requests"], reverse=True)
    return groups


def print_table(group_name: str, models: List[Dict]) -> None:
    """Print a formatted table for a tier group."""
    print(f"\n{'=' * 110}")
    print(f"  A/B Test Results: {group_name} (last 7 days)")
    print(f"{'=' * 110}")
    print()

    # Header
    header = (
        f"{'Model':<35} | {'Reqs':>6} | {'Avg Lat':>9} | {'Avg Cost':>9} "
        f"| {'Tok/Req':>8} | {'Success':>7} | {'Fact Pass':>9} | {'Consensus':>14}"
    )
    print(header)
    print("-" * len(header))

    for m in models:
        # Truncate model name if too long
        model_name = m["model"]
        if len(model_name) > 34:
            model_name = model_name[:31] + "..."

        row = (
            f"{model_name:<35} | {m['requests']:>6} | {m['avg_latency_s']:>7.2f}s "
            f"| ${m['avg_cost']:>7.4f} | {m['avg_tokens']:>8} "
            f"| {m['success_rate']:>5.1f}% | {m['factual_rate']:>9} "
            f"| {m['consensus_rate']:>14}"
        )
        print(row)

    print()


def print_summary(all_metrics: List[Dict]) -> None:
    """Print overall summary statistics."""
    total_reqs = sum(m["requests"] for m in all_metrics)
    total_cost = sum(m["total_cost"] for m in all_metrics)
    total_failures = sum(m["failures"] for m in all_metrics)
    unique_models = len(set(m["model"] for m in all_metrics))
    unique_groups = len(set(m["group"] for m in all_metrics))

    print(f"\n{'─' * 60}")
    print(f"  Summary")
    print(f"{'─' * 60}")
    print(f"  Total Requests:    {total_reqs}")
    print(f"  Total Cost:        ${total_cost:.4f}")
    print(f"  Total Failures:    {total_failures}")
    print(f"  Unique Models:     {unique_models}")
    print(f"  Tier Groups:       {unique_groups}")
    print(f"{'─' * 60}")
    print()


def main():
    parser = argparse.ArgumentParser(description="A/B Testing Dashboard")
    parser.add_argument("--group", "-g", help="Filter by tier group (e.g., cloud/chat)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--redis-host", default="redis", help="Redis host")
    parser.add_argument("--redis-port", type=int, default=6379, help="Redis port")
    args = parser.parse_args()

    redis_host = args.redis_host
    redis_port = args.redis_port


    try:
        r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True, socket_connect_timeout=5)
        r.ping()
    except Exception as e:
        print(f"ERROR: Cannot connect to Redis at {redis_host}:{redis_port}: {e}")
        sys.exit(1)

    keys = scan_ab_keys(r)
    if not keys:
        print("\nNo A/B testing data found in Redis.")
        print("Data will appear after requests are routed through the LiteLLM proxy.")
        sys.exit(0)

    # Parse all metrics
    all_metrics = []
    for key in keys:
        m = parse_metrics(r, key)
        if m:
            all_metrics.append(m)

    if not all_metrics:
        print("\nNo valid A/B testing metrics found.")
        sys.exit(0)

    # Filter by group if specified
    if args.group:
        all_metrics = [m for m in all_metrics if m["group"] == args.group]
        if not all_metrics:
            print(f"\nNo data found for group: {args.group}")
            sys.exit(0)

    # JSON output
    if args.json:
        print(json.dumps(all_metrics, indent=2))
        return

    # Table output
    print("\n" + "#" * 110)
    print("#" + " " * 35 + "A/B TESTING DASHBOARD" + " " * 35 + "#")
    print("#" + f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}" + " " * 63 + "#")
    print("#" * 110)

    grouped = group_by_tier(all_metrics)
    for group_name in sorted(grouped.keys()):
        print_table(group_name, grouped[group_name])

    print_summary(all_metrics)


if __name__ == "__main__":
    main()
