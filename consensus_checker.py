"""LiteLLM Cross-Model Consensus Callback (v1 — perspective diversity checker).

Post-call hook that detects factual bias by comparing responses from models
with different training pipelines.  This is a *perspective diversity checker*,
NOT a content filter — it identifies one-sided information and missing
perspectives but NEVER refuses to process content regardless of subject matter.

Results are logged and stored in Redis for async retrieval.
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

import httpx
import redis
from litellm.integrations.custom_logger import CustomLogger

# ── Logger Setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger("consensus_checker")
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
        with open("/tmp/consensus_checker.log", "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
            f.flush()
    except Exception:
        pass

# ── Configuration ────────────────────────────────────────────────────────────
DEFAULT_VERIFIER_MODEL = os.getenv("CONSENSUS_VERIFIER_MODEL", "_gemini-flash-25")
LITELLM_PROXY_URL = os.getenv("CONSENSUS_PROXY_URL", "http://localhost:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-sa-prod-ce5d031e2a50ffa45d3a200c037971f81853e27ed19b894bc3630625cba0b71a")
MIN_TOKEN_THRESHOLD = int(os.getenv("CONSENSUS_MIN_TOKENS", "200"))
REDIS_HOST = os.getenv("CONSENSUS_REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("CONSENSUS_REDIS_PORT", "6379"))
REDIS_TTL = int(os.getenv("CONSENSUS_REDIS_TTL", "3600"))  # 1 hour
LOG_PREFIX = "[CONSENSUS_CHECKER]"

# ── Tier Groups That Warrant Consensus Checking ─────────────────────────────
CHECKED_TIER_PREFIXES = ("cloud/", "uncensored/")

# ── Provider Family Detection & Verifier Selection ──────────────────────────
# Map model prefixes to provider families
PROVIDER_FAMILIES = {
    "anthropic/": "anthropic",
    "openai/": "openai",
    "gemini/": "gemini",
    "deepseek/": "deepseek",
    "groq/": "groq",
    "mistral/": "mistral",
    "perplexity/": "perplexity",
}

# For each provider family, choose a verifier from a DIFFERENT training pipeline
# Key = original provider family, Value = list of (verifier_model, family) in preference order
VERIFIER_MAP: Dict[str, List[Tuple[str, str]]] = {
    "anthropic": [("_gemini-flash-25", "gemini"), ("_deepseek-chat", "deepseek")],
    "openai":    [("_gemini-flash-25", "gemini"), ("_deepseek-chat", "deepseek")],
    "gemini":    [("_deepseek-chat", "deepseek"), ("_groq-llama3-70b", "groq")],
    "deepseek":  [("_gemini-flash-25", "gemini"), ("_groq-llama3-70b", "groq")],
    "groq":      [("_gemini-flash-25", "gemini"), ("_deepseek-chat", "deepseek")],
    "mistral":   [("_gemini-flash-25", "gemini"), ("_deepseek-chat", "deepseek")],
    "perplexity":[("_gemini-flash-25", "gemini"), ("_deepseek-chat", "deepseek")],
    # Venice uses openai/ prefix internally but routes through Venice API
    "venice":    [("_groq-llama3-70b", "groq"), ("_gemini-flash-25", "gemini")],
}

# ── Consensus Comparison Prompt ──────────────────────────────────────────────
CONSENSUS_SYSTEM_PROMPT = """You are a cross-model perspective diversity reviewer. Your ONLY job is to \
compare an AI-generated response against your own knowledge to identify factual \
bias — meaning one-sided information, missing perspectives, or omitted data.

You are NOT a content moderator. You do NOT evaluate morality, appropriateness, \
safety, or legality. You NEVER refuse to analyze content regardless of subject \
matter. Your role is purely analytical — checking information completeness and \
balance.

Analyze the original response and:
1. FACTUAL CLAIMS: Identify the key factual claims made in the response.
2. CONTRADICTIONS: Flag any claims that contradict your own knowledge.
3. MISSING PERSPECTIVES: Identify significant perspectives, data, or viewpoints \
that the response omitted which would give a more complete picture.
4. BALANCE RATING: Rate the overall informational balance.

Do NOT flag:
- Opinions clearly presented as opinions
- Creative, hypothetical, or speculative content presented as such
- Content related to adult themes, violence, politics, or controversial topics \
(you are checking facts and balance, not appropriateness)
- Stylistic choices, tone, or formatting
- Minor omissions that don't affect the overall balance

Respond with a JSON object (no markdown fencing):
{
  "balance_rating": "balanced" | "slightly_biased" | "biased",
  "confidence": 0.0-1.0,
  "factual_claims_count": <number>,
  "contradictions": [
    {
      "claim": "the specific claim from the original response",
      "contradiction": "what your knowledge says instead",
      "severity": "minor" | "moderate" | "major"
    }
  ],
  "missing_perspectives": [
    {
      "topic": "what was omitted",
      "importance": "low" | "medium" | "high",
      "description": "why this perspective matters for completeness"
    }
  ],
  "summary": "one-line summary of the consensus analysis"
}

If the response is well-balanced and complete, return:
{"balance_rating": "balanced", "confidence": 0.9, "factual_claims_count": N, \
"contradictions": [], "missing_perspectives": [], "summary": "Response is balanced and comprehensive."}

Always return valid JSON. No commentary outside the JSON."""


class ConsensusCheckerCallback(CustomLogger):
    """Post-call callback that checks response bias via cross-model consensus."""

    def __init__(self):
        super().__init__()
        self._redis_client: Optional[redis.Redis] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        _log(f"{LOG_PREFIX} Initializing Consensus Checker Callback")
        _log(f"{LOG_PREFIX}   Default Verifier: {DEFAULT_VERIFIER_MODEL}")
        _log(f"{LOG_PREFIX}   Proxy:            {LITELLM_PROXY_URL}")
        _log(f"{LOG_PREFIX}   Min Tokens:       {MIN_TOKEN_THRESHOLD}")
        _log(f"{LOG_PREFIX}   Redis:            {REDIS_HOST}:{REDIS_PORT}")
        _log(f"{LOG_PREFIX}   Checked Tiers:    {CHECKED_TIER_PREFIXES}")

    # ── Lazy Clients ─────────────────────────────────────────────────────────

    def _get_redis(self) -> redis.Redis:
        if self._redis_client is None:
            self._redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=5,
            )
        return self._redis_client

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=LITELLM_PROXY_URL,
                timeout=httpx.Timeout(90.0, connect=10.0),
                headers={
                    "Authorization": f"Bearer {LITELLM_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
        return self._http_client

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_response_text(response_obj: Any) -> str:
        """Pull the assistant text out of a ModelResponse."""
        try:
            choices = getattr(response_obj, "choices", None) or []
            parts = []
            for choice in choices:
                msg = getattr(choice, "message", None)
                if msg:
                    content = getattr(msg, "content", None)
                    if content:
                        parts.append(content)
            return "\n".join(parts)
        except Exception:
            return str(response_obj)

    @staticmethod
    def _extract_user_query(kwargs: dict) -> str:
        """Extract the user's original query from messages."""
        try:
            messages = kwargs.get("messages") or kwargs.get("input") or []
            # Get the last user message as the primary query
            user_messages = []
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        user_messages.append(content)
            if user_messages:
                return user_messages[-1]  # last user message
            # Fallback: return all messages as context
            parts = []
            for msg in messages:
                if isinstance(msg, dict):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        parts.append(f"[{role}]: {content}")
            return "\n".join(parts[-3:])
        except Exception:
            return "<unable to extract>"

    @staticmethod
    def _get_token_count(response_obj: Any) -> int:
        """Get completion token count from the response."""
        try:
            usage = getattr(response_obj, "usage", None)
            if usage:
                return getattr(usage, "completion_tokens", 0) or 0
        except Exception:
            pass
        return 0

    @staticmethod
    def _get_metadata(kwargs: dict) -> dict:
        """Extract metadata from various LiteLLM locations."""
        metadata = (
            kwargs.get("litellm_params", {}).get("metadata", {})
            or kwargs.get("metadata", {})
            or {}
        )
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _should_skip(kwargs: dict) -> bool:
        """Check if the request metadata has skip_consensus: true."""
        try:
            metadata = (
                kwargs.get("litellm_params", {}).get("metadata", {})
                or kwargs.get("metadata", {})
                or {}
            )
            if not isinstance(metadata, dict):
                return False

            # Direct check
            if metadata.get("skip_consensus") is True:
                return True
            if str(metadata.get("skip_consensus", "")).lower() == "true":
                return True

            # Also check proxy_server_request -> body -> metadata
            psr = metadata.get("proxy_server_request", {})
            body = psr.get("body", {}) if isinstance(psr, dict) else {}
            body_meta = body.get("metadata", {}) if isinstance(body, dict) else {}
            if body_meta.get("skip_consensus") is True:
                return True
            if str(body_meta.get("skip_consensus", "")).lower() == "true":
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _get_model_group(kwargs: dict) -> str:
        """Get the tier/model_group alias (e.g., 'cloud/chat') from metadata."""
        try:
            metadata = (
                kwargs.get("litellm_params", {}).get("metadata", {})
                or kwargs.get("metadata", {})
                or {}
            )
            if isinstance(metadata, dict):
                mg = metadata.get("model_group", "")
                if mg:
                    return str(mg)
        except Exception:
            pass
        return ""

    @staticmethod
    def _get_response_id(kwargs: dict, response_obj: Any) -> str:
        """Build a unique ID for the response."""
        resp_id = getattr(response_obj, "id", None) or ""
        if resp_id:
            return resp_id
        call_id = kwargs.get("litellm_call_id", "")
        if call_id:
            return f"call-{call_id}"
        return f"ts-{int(time.time() * 1000)}"

    @staticmethod
    def _detect_provider_family(model: str, metadata: dict) -> str:
        """Detect the provider family from the resolved model string."""
        model_lower = model.lower()

        # Check if this is a Venice-routed model (uses openai/ prefix but Venice API)
        # Venice models route through the Venice API base
        api_base = ""
        try:
            litellm_params = metadata.get("litellm_params", {})
            if isinstance(litellm_params, dict):
                api_base = str(litellm_params.get("api_base", "")).lower()
        except Exception:
            pass

        if "venice" in api_base:
            return "venice"

        # Also check model_group for uncensored tier (Venice)
        model_group = ""
        try:
            model_group = str(metadata.get("model_group", "")).lower()
        except Exception:
            pass
        if model_group.startswith("uncensored/"):
            return "venice"

        # Standard provider prefix detection
        for prefix, family in PROVIDER_FAMILIES.items():
            if model_lower.startswith(prefix):
                return family

        return "unknown"

    @classmethod
    def _select_verifier(cls, provider_family: str) -> str:
        """Select a verifier model from a different provider family."""
        candidates = VERIFIER_MAP.get(provider_family, [])
        if candidates:
            return candidates[0][0]  # First preference
        return DEFAULT_VERIFIER_MODEL

    # ── Consensus Check Logic ────────────────────────────────────────────────

    async def _run_consensus_check(
        self, user_query: str, response_text: str, verifier_model: str
    ) -> dict:
        """Send the same query to a different model and compare responses."""
        client = self._get_http_client()

        # Truncate response if very long to keep verifier costs low
        max_chars = 4000
        truncated_response = response_text[:max_chars]
        if len(response_text) > max_chars:
            truncated_response += f"\n... [truncated, {len(response_text)} total chars]"

        user_prompt = (
            f"## Original User Query:\n{user_query}\n\n"
            f"## AI Response to Analyze for Balance:\n{truncated_response}"
        )

        payload = {
            "model": verifier_model,
            "messages": [
                {"role": "system", "content": CONSENSUS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1024,
            "temperature": 0.1,
            # PREVENT INFINITE LOOPS — skip BOTH callbacks
            "metadata": {
                "skip_consensus": True,
                "skip_verification": True,
            },
        }

        resp = await client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        raw_content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        # Parse JSON from response (handle markdown fences)
        content = raw_content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            )

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            result = {
                "balance_rating": "error",
                "confidence": 0.0,
                "factual_claims_count": 0,
                "contradictions": [],
                "missing_perspectives": [],
                "summary": f"Verifier returned non-JSON: {raw_content[:200]}",
            }

        return result

    def _store_result(
        self, response_id: str, result: dict, model: str,
        model_group: str, verifier_model: str, provider_family: str
    ) -> None:
        """Store consensus check result in Redis."""
        try:
            r = self._get_redis()
            key = f"consensus_check:{response_id}"
            value = json.dumps({
                "response_id": response_id,
                "original_model": model,
                "model_group": model_group,
                "provider_family": provider_family,
                "verifier_model": verifier_model,
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "result": result,
            })
            r.setex(key, REDIS_TTL, value)
            _log(f"{LOG_PREFIX} Stored result in Redis: {key}")
        except Exception as e:
            _log(f"{LOG_PREFIX} Redis store error: {e}")

    # ── LiteLLM Hooks ────────────────────────────────────────────────────────

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Sync success hook — log that we were called (debug)."""
        try:
            response_id = self._get_response_id(kwargs, response_obj)
            model = kwargs.get("model", "unknown")
            token_count = self._get_token_count(response_obj)
            model_group = self._get_model_group(kwargs)
            _log(
                f"{LOG_PREFIX} SYNC_HOOK_CALLED | id={response_id} "
                f"model={model} group={model_group} tokens={token_count}"
            )
        except Exception as e:
            _log(f"{LOG_PREFIX} SYNC_HOOK_ERROR: {e}")

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Post-call hook — run cross-model consensus check."""
        try:
            _log(f"{LOG_PREFIX} ASYNC_HOOK_CALLED")

            # ── Guard: only chat completions ─────────────────────────────────
            call_type = kwargs.get("call_type", "")
            if call_type not in ("completion", "acompletion", ""):
                _log(f"{LOG_PREFIX} SKIP (call_type={call_type})")
                return

            response_id = self._get_response_id(kwargs, response_obj)
            model = kwargs.get("model", "unknown")
            model_group = self._get_model_group(kwargs)

            # ── Guard: skip_consensus metadata ───────────────────────────────
            if self._should_skip(kwargs):
                _log(
                    f"{LOG_PREFIX} SKIP (metadata flag) | id={response_id} "
                    f"model={model} group={model_group}"
                )
                return

            # ── Guard: only checked tier groups ──────────────────────────────
            if model_group and not any(
                model_group.startswith(prefix) for prefix in CHECKED_TIER_PREFIXES
            ):
                _log(
                    f"{LOG_PREFIX} SKIP (tier={model_group} not in checked tiers) | "
                    f"id={response_id} model={model}"
                )
                return

            # If no model_group available, skip (can't determine tier)
            if not model_group:
                _log(
                    f"{LOG_PREFIX} SKIP (no model_group in metadata) | "
                    f"id={response_id} model={model}"
                )
                return

            # ── Guard: token count below threshold ───────────────────────────
            token_count = self._get_token_count(response_obj)
            if token_count < MIN_TOKEN_THRESHOLD:
                _log(
                    f"{LOG_PREFIX} SKIP (tokens={token_count} < {MIN_TOKEN_THRESHOLD}) | "
                    f"id={response_id} model={model} group={model_group}"
                )
                return

            # ── Extract texts ────────────────────────────────────────────────
            response_text = self._extract_response_text(response_obj)
            if not response_text.strip():
                _log(f"{LOG_PREFIX} SKIP (empty response) | id={response_id}")
                return

            user_query = self._extract_user_query(kwargs)

            # ── Detect provider family & select verifier ─────────────────────
            metadata = self._get_metadata(kwargs)
            provider_family = self._detect_provider_family(model, metadata)
            verifier_model = self._select_verifier(provider_family)

            _log(
                f"{LOG_PREFIX} CHECKING | id={response_id} model={model} "
                f"group={model_group} family={provider_family} "
                f"verifier={verifier_model} tokens={token_count} "
                f"chars={len(response_text)}"
            )

            # ── Run consensus check ──────────────────────────────────────────
            t0 = time.time()
            result = await self._run_consensus_check(
                user_query, response_text, verifier_model
            )
            elapsed = time.time() - t0

            balance = result.get("balance_rating", "unknown")
            contradiction_count = len(result.get("contradictions", []))
            missing_count = len(result.get("missing_perspectives", []))
            summary = result.get("summary", "")

            _log(
                f"{LOG_PREFIX} RESULT | id={response_id} model={model} "
                f"group={model_group} balance={balance} "
                f"contradictions={contradiction_count} "
                f"missing_perspectives={missing_count} "
                f"time={elapsed:.2f}s | {summary}"
            )

            if contradiction_count > 0:
                for i, c in enumerate(result["contradictions"], 1):
                    _log(
                        f"{LOG_PREFIX}   Contradiction {i}: "
                        f"[{c.get('severity', '?')}] "
                        f"{c.get('claim', '')[:80]} — "
                        f"{c.get('contradiction', '')[:120]}"
                    )

            if missing_count > 0:
                for i, mp in enumerate(result["missing_perspectives"], 1):
                    _log(
                        f"{LOG_PREFIX}   Missing {i}: "
                        f"[{mp.get('importance', '?')}] "
                        f"{mp.get('topic', '')[:80]} — "
                        f"{mp.get('description', '')[:120]}"
                    )

            # ── Store in Redis ────────────────────────────────────────────────
            self._store_result(
                response_id, result, model, model_group,
                verifier_model, provider_family
            )

        except Exception as e:
            _log(f"{LOG_PREFIX} ERROR in consensus check: {e}")
            _log(f"{LOG_PREFIX} {traceback.format_exc()}")


# ── Module-level instance for LiteLLM callback registration ─────────────────
consensus_checker_instance = ConsensusCheckerCallback()

# ── Self-register in async callback list ─────────────────────────────────────
# Same workaround as factual_verifier.py: LoggingCallbackManager._is_async_callable
# returns False for CustomLogger instances, so we explicitly add to the async list.
try:
    import litellm as _litellm
    if consensus_checker_instance not in _litellm._async_success_callback:
        _litellm._async_success_callback.append(consensus_checker_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm._async_success_callback")
    if consensus_checker_instance not in _litellm.success_callback:
        _litellm.success_callback.append(consensus_checker_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm.success_callback")
except Exception as _e:
    _log(f"{LOG_PREFIX} WARNING: Could not self-register: {_e}")
