"""LiteLLM A/B Testing Callback (v1 — observational framework).

Post-call hook that tracks per-model metrics across tier groups for A/B
comparison.  This is an *observational* framework — it tracks natural
traffic patterns across models (which vary due to fallbacks and rate
limits) rather than actively splitting traffic.

Metrics tracked per model:
  - Request count
  - Average latency
  - Token usage (input/output)
  - Success/failure rate
  - Cost
  - Factual verification pass rate (cross-referenced from factual_verifier)
  - Consensus check balance ratings (cross-referenced from consensus_checker)

Results stored in Redis with key pattern: ab_test:{group}:{model}
TTL: 7 days (longer than cache TTL to accumulate meaningful data).
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

import redis
from litellm.integrations.custom_logger import CustomLogger

# ── Logger Setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger("ab_testing")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

def _log(msg: str) -> None:
    """Log with logger, print, AND file for maximum visibility."""
    logger.info(msg)
    print(msg, flush=True)
    sys.stdout.flush()
    try:
        with open("/tmp/ab_testing.log", "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
            f.flush()
    except Exception:
        pass

# ── Configuration ────────────────────────────────────────────────────────────
REDIS_HOST = os.getenv("AB_TESTING_REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("AB_TESTING_REDIS_PORT", "6379"))
REDIS_TTL = int(os.getenv("AB_TESTING_REDIS_TTL", "604800"))  # 7 days
LOG_PREFIX = "[AB_TESTING]"

# Models to skip tracking (internal verification models)
SKIP_MODELS = {
    "_groq-llama3-8b",
    "_groq-llama3-70b",
    "_gemini-flash-25",
    "_deepseek-chat",
    "_local-embedding",
}


class ABTestingCallback(CustomLogger):
    """Tracks per-model metrics for A/B comparison across tier groups."""

    def __init__(self):
        super().__init__()
        self._redis_client: Optional[redis.Redis] = None
        _log(f"{LOG_PREFIX} Initializing A/B Testing Callback")
        _log(f"{LOG_PREFIX}   Redis:     {REDIS_HOST}:{REDIS_PORT}")
        _log(f"{LOG_PREFIX}   TTL:       {REDIS_TTL}s (7 days)")

    def _get_redis(self) -> redis.Redis:
        """Get or create Redis client."""
        if self._redis_client is None:
            self._redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=5,
            )
        return self._redis_client

    def _extract_model_info(self, kwargs: Dict) -> Dict[str, str]:
        """Extract model name and group from kwargs."""
        result = {"model": "unknown", "group": "unknown"}

        try:
            # Get the actual model used (resolved name)
            model = kwargs.get("model", "")
            if model:
                result["model"] = model

            # Get the model group (tier)
            litellm_params = kwargs.get("litellm_params", {}) or {}
            metadata = litellm_params.get("metadata", {}) or {}
            model_group = metadata.get("model_group", "")
            if model_group:
                result["group"] = model_group
            else:
                # Try to get from proxy_server_request
                psr = litellm_params.get("proxy_server_request", {}) or {}
                body = psr.get("body", {}) if isinstance(psr, dict) else {}
                requested_model = body.get("model", "")
                if requested_model:
                    result["group"] = requested_model

        except Exception as e:
            _log(f"{LOG_PREFIX} Error extracting model info: {e}")

        return result

    def _should_skip(self, kwargs: Dict, model_info: Dict) -> bool:
        """Check if this request should be skipped."""
        # Skip internal verification models
        model = model_info.get("model", "")
        group = model_info.get("group", "")

        if model in SKIP_MODELS or group in SKIP_MODELS:
            return True

        # Skip requests with skip_ab_testing metadata
        litellm_params = kwargs.get("litellm_params", {}) or {}
        metadata = litellm_params.get("metadata", {}) or {}
        if metadata.get("skip_ab_testing"):
            return True

        return False

    def _get_factual_result(self, response_id: str) -> Optional[Dict]:
        """Cross-reference factual verification result from Redis."""
        try:
            r = self._get_redis()
            data = r.get(f"factual_verification:{response_id}")
            if data:
                return json.loads(data)
        except Exception as e:
            _log(f"{LOG_PREFIX} Error reading factual result: {e}")
        return None

    def _get_consensus_result(self, response_id: str) -> Optional[Dict]:
        """Cross-reference consensus check result from Redis."""
        try:
            r = self._get_redis()
            data = r.get(f"consensus_check:{response_id}")
            if data:
                return json.loads(data)
        except Exception as e:
            _log(f"{LOG_PREFIX} Error reading consensus result: {e}")
        return None

    def _update_metrics(self, group: str, model: str, metrics: Dict) -> None:
        """Update A/B test metrics in Redis using atomic increments."""
        try:
            r = self._get_redis()
            key = f"ab_test:{group}:{model}"

            pipe = r.pipeline()

            # Increment request count
            pipe.hincrby(key, "request_count", 1)

            # Accumulate latency (store total for averaging later)
            latency_ms = int(metrics.get("latency_ms", 0))
            pipe.hincrby(key, "total_latency_ms", latency_ms)

            # Accumulate tokens
            pipe.hincrby(key, "total_input_tokens", metrics.get("input_tokens", 0))
            pipe.hincrby(key, "total_output_tokens", metrics.get("output_tokens", 0))
            pipe.hincrby(key, "total_tokens", metrics.get("total_tokens", 0))

            # Accumulate cost (store as integer microcents for precision)
            cost_microcents = int(metrics.get("cost", 0) * 1_000_000)
            pipe.hincrby(key, "total_cost_microcents", cost_microcents)

            # Success/failure counts
            pipe.hincrby(key, "success_count", 1)  # This is success callback

            # Factual verification tracking
            if metrics.get("factual_pass") is not None:
                pipe.hincrby(key, "factual_checked", 1)
                if metrics["factual_pass"]:
                    pipe.hincrby(key, "factual_passed", 1)

            # Consensus check tracking
            if metrics.get("consensus_balanced") is not None:
                pipe.hincrby(key, "consensus_checked", 1)
                if metrics["consensus_balanced"]:
                    pipe.hincrby(key, "consensus_balanced_count", 1)

            # Store last update timestamp
            pipe.hset(key, "last_updated", time.strftime("%Y-%m-%d %H:%M:%S"))

            # Store the model display name (for dashboard)
            pipe.hset(key, "model_name", model)
            pipe.hset(key, "group_name", group)

            # Set TTL (reset on each update to keep active models alive)
            pipe.expire(key, REDIS_TTL)

            pipe.execute()
            _log(f"{LOG_PREFIX} Updated metrics: {key} (reqs: +1, latency: {latency_ms}ms)")

        except Exception as e:
            _log(f"{LOG_PREFIX} Error updating metrics: {e}")
            _log(f"{LOG_PREFIX} {traceback.format_exc()}")

    async def async_log_success_event(
        self, kwargs, response_obj, start_time, end_time
    ):
        """Track successful request metrics for A/B testing."""
        try:
            model_info = self._extract_model_info(kwargs)

            if self._should_skip(kwargs, model_info):
                return

            model = model_info["model"]
            group = model_info["group"]

            _log(f"{LOG_PREFIX} Tracking: group={group}, model={model}")

            # ── Calculate latency ────────────────────────────────────────────
            latency_ms = 0
            if start_time and end_time:
                latency_ms = int((end_time - start_time).total_seconds() * 1000)

            # ── Extract token usage ──────────────────────────────────────────
            usage = {}
            if hasattr(response_obj, "usage") and response_obj.usage:
                usage = {
                    "input_tokens": getattr(response_obj.usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(response_obj.usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(response_obj.usage, "total_tokens", 0) or 0,
                }

            # ── Extract cost ─────────────────────────────────────────────────
            cost = 0.0
            response_cost = kwargs.get("response_cost", None)
            if response_cost is not None:
                try:
                    cost = float(response_cost)
                except (TypeError, ValueError):
                    pass

            # ── Get response ID for cross-referencing ────────────────────────
            response_id = ""
            if hasattr(response_obj, "id"):
                response_id = response_obj.id or ""

            # ── Cross-reference factual verification ─────────────────────────
            # Give factual_verifier a moment to store its result
            factual_pass = None
            if response_id:
                await asyncio.sleep(2)  # Brief delay to allow async callback
                factual_result = self._get_factual_result(response_id)
                if factual_result:
                    verdict = factual_result.get("verdict", "")
                    factual_pass = verdict == "pass"
                    _log(f"{LOG_PREFIX} Factual cross-ref: {verdict}")

            # ── Cross-reference consensus check ──────────────────────────────
            consensus_balanced = None
            if response_id:
                consensus_result = self._get_consensus_result(response_id)
                if consensus_result:
                    balance = consensus_result.get("verdict", "")
                    consensus_balanced = balance in ("balanced", "pass")
                    _log(f"{LOG_PREFIX} Consensus cross-ref: {balance}")

            # ── Build metrics dict ───────────────────────────────────────────
            metrics = {
                "latency_ms": latency_ms,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "cost": cost,
                "factual_pass": factual_pass,
                "consensus_balanced": consensus_balanced,
            }

            # ── Store in Redis ────────────────────────────────────────────────
            self._update_metrics(group, model, metrics)

        except Exception as e:
            _log(f"{LOG_PREFIX} ERROR in async_log_success_event: {e}")
            _log(f"{LOG_PREFIX} {traceback.format_exc()}")

    async def async_log_failure_event(
        self, kwargs, response_obj, start_time, end_time
    ):
        """Track failed request for A/B testing failure rate."""
        try:
            model_info = self._extract_model_info(kwargs)

            if self._should_skip(kwargs, model_info):
                return

            model = model_info["model"]
            group = model_info["group"]

            _log(f"{LOG_PREFIX} Tracking failure: group={group}, model={model}")

            r = self._get_redis()
            key = f"ab_test:{group}:{model}"

            pipe = r.pipeline()
            pipe.hincrby(key, "failure_count", 1)
            pipe.hset(key, "last_updated", time.strftime("%Y-%m-%d %H:%M:%S"))
            pipe.hset(key, "model_name", model)
            pipe.hset(key, "group_name", group)
            pipe.expire(key, REDIS_TTL)
            pipe.execute()

        except Exception as e:
            _log(f"{LOG_PREFIX} ERROR in async_log_failure_event: {e}")
            _log(f"{LOG_PREFIX} {traceback.format_exc()}")


# ── Module-level instance for LiteLLM callback registration ─────────────────
ab_testing_instance = ABTestingCallback()

# ── Self-register in async callback list ─────────────────────────────────────
# The _is_async_callable check in LiteLLM's LoggingCallbackManager returns
# False for CustomLogger instances (it checks the instance itself, not its
# methods).  This causes the callback to be added to the SYNC success list
# only, but the proxy uses ASYNC calls.  We fix this by explicitly adding
# our instance to the async list as well.
try:
    import litellm as _litellm
    if ab_testing_instance not in _litellm._async_success_callback:
        _litellm._async_success_callback.append(ab_testing_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm._async_success_callback")
    if ab_testing_instance not in _litellm.success_callback:
        _litellm.success_callback.append(ab_testing_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm.success_callback")
    # Also register for failure callbacks
    if ab_testing_instance not in _litellm._async_failure_callback:
        _litellm._async_failure_callback.append(ab_testing_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm._async_failure_callback")
    if ab_testing_instance not in _litellm.failure_callback:
        _litellm.failure_callback.append(ab_testing_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm.failure_callback")
except Exception as _e:
    _log(f"{LOG_PREFIX} WARNING: Could not self-register: {_e}")
