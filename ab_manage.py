#!/usr/bin/env python3
"""A/B Testing Management — Create, view, and conclude experiments.

This is an observational A/B testing framework that tracks natural traffic
patterns across models within tier groups. It does NOT actively split traffic
(since LiteLLM uses priority-based failover), but instead provides insights
into how different models perform when they handle requests.

Usage:
    docker exec litellm python3 /app/ab_manage.py list
    docker exec litellm python3 /app/ab_manage.py results --group cloud/chat
    docker exec litellm python3 /app/ab_manage.py conclude --group cloud/chat
    docker exec litellm python3 /app/ab_manage.py reset --group cloud/chat
"""

import argparse
import json
import sys
from datetime import datetime
from typing import Dict, List, Optional

try:
    import redis
except ImportError:
    print("ERROR: redis package not available. Run inside litellm container.")
    sys.exit(1)


REDIS_HOST = "redis"
REDIS_PORT = 6379


def get_redis() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT,
        decode_responses=True, socket_connect_timeout=5,
    )


def scan_ab_keys(r: redis.Redis, group_filter: str = None) -> List[str]:
    """Scan ab_test:* keys, optionally filtered by group."""
    pattern = f"ab_test:{group_filter}:*" if group_filter else "ab_test:*"
    keys = []
    cursor = 0
    while True:
        cursor, batch = r.scan(cursor, match=pattern, count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    return sorted(keys)


def get_metrics(r: redis.Redis, key: str) -> Optional[Dict]:
    """Get parsed metrics from a Redis hash."""
    data = r.hgetall(key)
    if not data:
        return None

    request_count = int(data.get("request_count", 0))
    success_count = int(data.get("success_count", 0))
    failure_count = int(data.get("failure_count", 0))
    total_requests = success_count + failure_count

    total_latency_ms = int(data.get("total_latency_ms", 0))
    avg_latency_s = (total_latency_ms / request_count / 1000) if request_count > 0 else 0

    total_cost_microcents = int(data.get("total_cost_microcents", 0))
    total_cost = total_cost_microcents / 1_000_000
    avg_cost = total_cost / request_count if request_count > 0 else 0

    total_tokens = int(data.get("total_tokens", 0))
    avg_tokens = total_tokens // request_count if request_count > 0 else 0

    success_rate = (success_count / total_requests * 100) if total_requests > 0 else 0

    factual_checked = int(data.get("factual_checked", 0))
    factual_passed = int(data.get("factual_passed", 0))
    factual_rate = (factual_passed / factual_checked * 100) if factual_checked > 0 else None

    consensus_checked = int(data.get("consensus_checked", 0))
    consensus_balanced = int(data.get("consensus_balanced_count", 0))
    consensus_rate = (consensus_balanced / consensus_checked * 100) if consensus_checked > 0 else None

    return {
        "model": data.get("model_name", "unknown"),
        "group": data.get("group_name", "unknown"),
        "request_count": request_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "avg_latency_s": avg_latency_s,
        "avg_cost": avg_cost,
        "total_cost": total_cost,
        "avg_tokens": avg_tokens,
        "total_tokens": total_tokens,
        "success_rate": success_rate,
        "factual_rate": factual_rate,
        "consensus_rate": consensus_rate,
        "last_updated": data.get("last_updated", "unknown"),
    }


def cmd_list(r: redis.Redis, args):
    """List all active A/B test groups with summary stats."""
    keys = scan_ab_keys(r)
    if not keys:
        print("\nNo A/B testing data found.")
        print("Data will appear after requests flow through the proxy.")
        return

    groups = {}
    for key in keys:
        m = get_metrics(r, key)
        if not m:
            continue
        g = m["group"]
        if g not in groups:
            groups[g] = {"models": 0, "total_requests": 0, "total_cost": 0}
        groups[g]["models"] += 1
        groups[g]["total_requests"] += m["request_count"]
        groups[g]["total_cost"] += m["total_cost"]

    print(f"\n{'=' * 70}")
    print(f"  Active A/B Test Groups")
    print(f"{'=' * 70}")
    print(f"\n{'Group':<25} | {'Models':>6} | {'Requests':>8} | {'Total Cost':>10}")
    print("-" * 60)

    for g in sorted(groups.keys()):
        info = groups[g]
        print(f"{g:<25} | {info['models']:>6} | {info['total_requests']:>8} | ${info['total_cost']:>9.4f}")

    print(f"\nTotal groups: {len(groups)}")
    print()


def cmd_results(r: redis.Redis, args):
    """Show detailed results for a specific group."""
    if not args.group:
        print("ERROR: --group is required for results command")
        sys.exit(1)

    keys = scan_ab_keys(r, args.group)
    if not keys:
        print(f"\nNo data found for group: {args.group}")
        return

    metrics_list = []
    for key in keys:
        m = get_metrics(r, key)
        if m:
            metrics_list.append(m)

    metrics_list.sort(key=lambda x: x["request_count"], reverse=True)

    print(f"\n{'=' * 110}")
    print(f"  A/B Test Results: {args.group}")
    print(f"{'=' * 110}")
    print()

    header = (
        f"{'Model':<35} | {'Reqs':>6} | {'Avg Lat':>9} | {'Avg Cost':>9} "
        f"| {'Tok/Req':>8} | {'Success':>7} | {'Fact Pass':>9} | {'Consensus':>14}"
    )
    print(header)
    print("-" * len(header))

    for m in metrics_list:
        model_name = m["model"]
        if len(model_name) > 34:
            model_name = model_name[:31] + "..."

        fact_str = f"{m['factual_rate']:.1f}%" if m["factual_rate"] is not None else "n/a"
        cons_str = f"{m['consensus_rate']:.0f}% balanced" if m["consensus_rate"] is not None else "n/a"

        row = (
            f"{model_name:<35} | {m['request_count']:>6} | {m['avg_latency_s']:>7.2f}s "
            f"| ${m['avg_cost']:>7.4f} | {m['avg_tokens']:>8} "
            f"| {m['success_rate']:>5.1f}% | {fact_str:>9} "
            f"| {cons_str:>14}"
        )
        print(row)

    print()


def cmd_conclude(r: redis.Redis, args):
    """Analyze A/B test data and recommend a winner."""
    if not args.group:
        print("ERROR: --group is required for conclude command")
        sys.exit(1)

    keys = scan_ab_keys(r, args.group)
    if not keys:
        print(f"\nNo data found for group: {args.group}")
        return

    metrics_list = []
    for key in keys:
        m = get_metrics(r, key)
        if m and m["request_count"] > 0:
            metrics_list.append(m)

    if not metrics_list:
        print(f"\nInsufficient data for group: {args.group}")
        return

    # Scoring system (weighted composite score)
    # Lower latency = better, higher success rate = better,
    # lower cost = better, higher factual rate = better,
    # higher consensus rate = better
    print(f"\n{'=' * 80}")
    print(f"  A/B Test Conclusion: {args.group}")
    print(f"{'=' * 80}")
    print()

    # Check if we have enough data
    min_requests = int(args.min_requests) if hasattr(args, 'min_requests') and args.min_requests else 5
    eligible = [m for m in metrics_list if m["request_count"] >= min_requests]

    if not eligible:
        print(f"  No models have >= {min_requests} requests.")
        print(f"  Models with data:")
        for m in metrics_list:
            print(f"    {m['model']}: {m['request_count']} requests")
        print(f"\n  Recommendation: Collect more data before concluding.")
        return

    # Calculate composite scores
    # Normalize each metric to 0-100 scale within the group
    def normalize(values, lower_is_better=False):
        if not values:
            return [50] * len(values)
        min_v, max_v = min(values), max(values)
        if min_v == max_v:
            return [50] * len(values)
        if lower_is_better:
            return [100 - ((v - min_v) / (max_v - min_v) * 100) for v in values]
        return [((v - min_v) / (max_v - min_v) * 100) for v in values]

    latencies = [m["avg_latency_s"] for m in eligible]
    costs = [m["avg_cost"] for m in eligible]
    success_rates = [m["success_rate"] for m in eligible]

    norm_latency = normalize(latencies, lower_is_better=True)
    norm_cost = normalize(costs, lower_is_better=True)
    norm_success = normalize(success_rates, lower_is_better=False)

    # Weights
    w_latency = 0.20
    w_cost = 0.15
    w_success = 0.25
    w_factual = 0.25
    w_consensus = 0.15

    scores = []
    for i, m in enumerate(eligible):
        score = (
            norm_latency[i] * w_latency
            + norm_cost[i] * w_cost
            + norm_success[i] * w_success
        )

        # Add quality metrics if available
        if m["factual_rate"] is not None:
            score += m["factual_rate"] * w_factual
        else:
            # Redistribute weight
            score += 50 * w_factual  # neutral

        if m["consensus_rate"] is not None:
            score += m["consensus_rate"] * w_consensus
        else:
            score += 50 * w_consensus  # neutral

        scores.append((score, m))

    scores.sort(key=lambda x: x[0], reverse=True)

    print(f"  Scoring Weights: Latency={w_latency:.0%}, Cost={w_cost:.0%}, "
          f"Success={w_success:.0%}, Factual={w_factual:.0%}, Consensus={w_consensus:.0%}")
    print()

    print(f"  {'Rank':<5} {'Model':<35} {'Score':>7} {'Reqs':>6} {'Latency':>9} {'Cost':>9} {'Success':>8}")
    print(f"  {'-' * 85}")

    for rank, (score, m) in enumerate(scores, 1):
        model_name = m["model"]
        if len(model_name) > 34:
            model_name = model_name[:31] + "..."
        indicator = " ★" if rank == 1 else ""
        print(
            f"  {rank:<5} {model_name:<35} {score:>6.1f} {m['request_count']:>6} "
            f"{m['avg_latency_s']:>7.2f}s ${m['avg_cost']:>7.4f} {m['success_rate']:>6.1f}%{indicator}"
        )

    winner_score, winner = scores[0]
    print(f"\n  ┌{'─' * 76}┐")
    print(f"  │ RECOMMENDATION: {winner['model']:<57}│")
    print(f"  │ Score: {winner_score:.1f} | Requests: {winner['request_count']} | "
          f"Avg Latency: {winner['avg_latency_s']:.2f}s | Avg Cost: ${winner['avg_cost']:.4f}" + " " * max(0, 10 - len(f"${winner['avg_cost']:.4f}")) + "│")
    print(f"  └{'─' * 76}┘")
    print()

    if len(scores) > 1:
        runner_score, runner = scores[1]
        margin = winner_score - runner_score
        if margin < 5:
            print(f"  ⚠  Close margin ({margin:.1f} points). Consider collecting more data.")
        elif margin < 15:
            print(f"  ✓  Moderate margin ({margin:.1f} points). Winner is likely superior.")
        else:
            print(f"  ✓✓ Strong margin ({margin:.1f} points). Winner is clearly superior.")
    print()


def cmd_reset(r: redis.Redis, args):
    """Reset A/B test data for a specific group."""
    if not args.group:
        print("ERROR: --group is required for reset command")
        sys.exit(1)

    keys = scan_ab_keys(r, args.group)
    if not keys:
        print(f"\nNo data found for group: {args.group}")
        return

    if not args.force:
        print(f"\nThis will delete {len(keys)} A/B test records for group: {args.group}")
        print("Keys to delete:")
        for k in keys:
            print(f"  {k}")
        print("\nUse --force to confirm deletion.")
        return

    for key in keys:
        r.delete(key)
    print(f"\nDeleted {len(keys)} A/B test records for group: {args.group}")


def main():
    parser = argparse.ArgumentParser(
        description="A/B Testing Management Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 ab_manage.py list                           # List active groups
  python3 ab_manage.py results --group cloud/chat     # Show results
  python3 ab_manage.py conclude --group cloud/chat    # Recommend winner
  python3 ab_manage.py reset --group cloud/chat --force  # Clear data
""",
    )
    parser.add_argument(
        "command",
        choices=["list", "results", "conclude", "reset"],
        help="Command to execute",
    )
    parser.add_argument("--group", "-g", help="Tier group (e.g., cloud/chat)")
    parser.add_argument("--force", action="store_true", help="Force operation (for reset)")
    parser.add_argument("--min-requests", type=int, default=5, help="Min requests to be eligible for conclusion")
    parser.add_argument("--redis-host", default="redis")
    parser.add_argument("--redis-port", type=int, default=6379)
    args = parser.parse_args()

    redis_host = args.redis_host
    redis_port = args.redis_port

    try:
        r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True, socket_connect_timeout=5)
        r.ping()
    except Exception as e:
        print(f"ERROR: Cannot connect to Redis: {e}")
        sys.exit(1)

    commands = {
        "list": cmd_list,
        "results": cmd_results,
        "conclude": cmd_conclude,
        "reset": cmd_reset,
    }
    commands[args.command](r, args)


if __name__ == "__main__":
    main()
