"""LiteLLM Factual Verification Callback (v1 — flag only, no blocking).

Post-call hook that verifies LLM responses for factual accuracy using a cheap
model (Groq llama-3.1-8b-instant via the local LiteLLM proxy).  This is a
*fact-checker*, NOT a content filter — it checks dates, numbers, citations,
and internal consistency but never evaluates morality or appropriateness.

Results are logged and stored in Redis for async retrieval.
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from typing import Any, Dict, Optional

import httpx
import redis
from litellm.integrations.custom_logger import CustomLogger

# ── Logger Setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger("factual_verifier")
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
        with open("/tmp/factual_verifier.log", "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
            f.flush()
    except Exception:
        pass

# ── Configuration ────────────────────────────────────────────────────────────
VERIFIER_MODEL = os.getenv("FACTUAL_VERIFIER_MODEL", "_groq-llama3-8b")
LITELLM_PROXY_URL = os.getenv("FACTUAL_VERIFIER_PROXY_URL", "http://localhost:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-sa-prod-ce5d031e2a50ffa45d3a200c037971f81853e27ed19b894bc3630625cba0b71a")
MIN_TOKEN_THRESHOLD = int(os.getenv("FACTUAL_VERIFIER_MIN_TOKENS", "100"))
REDIS_HOST = os.getenv("FACTUAL_VERIFIER_REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("FACTUAL_VERIFIER_REDIS_PORT", "6379"))
REDIS_TTL = int(os.getenv("FACTUAL_VERIFIER_REDIS_TTL", "3600"))  # 1 hour
LOG_PREFIX = "[FACTUAL_VERIFIER]"

# ── Verification Prompt ──────────────────────────────────────────────────────
VERIFIER_SYSTEM_PROMPT = """You are a factual accuracy reviewer. Your ONLY job is to check \
whether the following AI-generated response contains factual errors. You are NOT a content \
moderator — you do not evaluate morality, appropriateness, safety, or legality.

Check for ONLY these issues:
1. DATES & NUMBERS: Are dates, years, statistics, quantities plausible and internally consistent?
2. CITATIONS & SOURCES: Are any named sources, papers, books, URLs, or references real? Flag any that appear fabricated.
3. INTERNAL CONTRADICTIONS: Does the response contradict itself?
4. UNSUPPORTED CAUSAL CLAIMS: Does the response assert causal relationships without evidence?
5. FABRICATED SPECIFICS: Are specific names, places, organizations, or data points verifiably real or clearly invented?

Do NOT flag:
- Opinions, hypotheticals, or subjective statements
- Content related to adult themes, violence, politics, or controversial topics (you are not a censor)
- Stylistic choices or tone
- Speculative or creative content clearly presented as such

Respond with a JSON object (no markdown fencing):
{
  "verdict": "pass" | "flag",
  "confidence": 0.0-1.0,
  "issues": [
    {
      "type": "fabricated_citation" | "wrong_date" | "wrong_number" | "contradiction" | "unsupported_claim" | "fabricated_detail",
      "excerpt": "the specific claim",
      "explanation": "why this is flagged"
    }
  ],
  "summary": "one-line summary of findings"
}

If everything checks out, return {"verdict": "pass", "confidence": 0.9, "issues": [], "summary": "No factual issues detected."}.
Always return valid JSON. No commentary outside the JSON."""


class FactualVerifierCallback(CustomLogger):
    """Post-call callback that verifies LLM responses for factual accuracy."""

    def __init__(self):
        super().__init__()
        self._redis_client: Optional[redis.Redis] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        _log(f"{LOG_PREFIX} Initializing Factual Verifier Callback")
        _log(f"{LOG_PREFIX}   Model:     {VERIFIER_MODEL}")
        _log(f"{LOG_PREFIX}   Proxy:     {LITELLM_PROXY_URL}")
        _log(f"{LOG_PREFIX}   Min Tokens: {MIN_TOKEN_THRESHOLD}")
        _log(f"{LOG_PREFIX}   Redis:     {REDIS_HOST}:{REDIS_PORT}")

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
                timeout=httpx.Timeout(60.0, connect=10.0),
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
    def _extract_request_messages(kwargs: dict) -> str:
        """Pull the user messages for context."""
        try:
            messages = kwargs.get("messages") or kwargs.get("input") or []
            parts = []
            for msg in messages:
                if isinstance(msg, dict):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        parts.append(f"[{role}]: {content}")
            return "\n".join(parts[-3:])  # last 3 messages for context
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
    def _should_skip(kwargs: dict) -> bool:
        """Check if the request metadata has skip_verification: true."""
        try:
            # LiteLLM passes metadata in several places
            metadata = (
                kwargs.get("litellm_params", {}).get("metadata", {})
                or kwargs.get("metadata", {})
                or {}
            )
            if metadata.get("skip_verification") is True:
                return True
            if str(metadata.get("skip_verification", "")).lower() == "true":
                return True

            # Also check proxy_server_request -> body -> metadata
            psr = metadata.get("proxy_server_request", {})
            body = psr.get("body", {}) if isinstance(psr, dict) else {}
            body_meta = body.get("metadata", {}) if isinstance(body, dict) else {}
            if body_meta.get("skip_verification") is True:
                return True
            if str(body_meta.get("skip_verification", "")).lower() == "true":
                return True
        except Exception:
            pass
        return False

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

    # ── Verification Logic ───────────────────────────────────────────────────

    async def _run_verification(self, request_context: str, response_text: str) -> dict:
        """Call the verifier model and parse its output."""
        client = self._get_http_client()

        # Truncate if very long to keep verifier costs low
        max_chars = 4000
        truncated_response = response_text[:max_chars]
        if len(response_text) > max_chars:
            truncated_response += f"\n... [truncated, {len(response_text)} total chars]"

        user_prompt = (
            f"## Original Request Context (last messages):\n{request_context}\n\n"
            f"## AI Response to Verify:\n{truncated_response}"
        )

        payload = {
            "model": VERIFIER_MODEL,
            "messages": [
                {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1024,
            "temperature": 0.1,
            "metadata": {"skip_verification": True},  # PREVENT INFINITE LOOP
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
            # strip ```json ... ```
            lines = content.split("\n")
            content = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            )

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            result = {
                "verdict": "error",
                "confidence": 0.0,
                "issues": [],
                "summary": f"Verifier returned non-JSON: {raw_content[:200]}",
            }

        return result

    def _store_result(self, response_id: str, result: dict, model: str) -> None:
        """Store verification result in Redis."""
        try:
            r = self._get_redis()
            key = f"factual_verification:{response_id}"
            value = json.dumps({
                "response_id": response_id,
                "model": model,
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
            _log(f"{LOG_PREFIX} SYNC_HOOK_CALLED | id={response_id} model={model} tokens={token_count}")
        except Exception as e:
            _log(f"{LOG_PREFIX} SYNC_HOOK_ERROR: {e}")

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Post-call hook — verify the response for factual accuracy."""
        try:
            _log(f"{LOG_PREFIX} ASYNC_HOOK_CALLED")

            # ── Guard: only chat completions ─────────────────────────────────
            call_type = kwargs.get("call_type", "")
            if call_type not in ("completion", "acompletion", ""):
                _log(f"{LOG_PREFIX} SKIP (call_type={call_type})")
                return

            response_id = self._get_response_id(kwargs, response_obj)
            model = kwargs.get("model", "unknown")

            # ── Guard: skip_verification metadata ────────────────────────────
            if self._should_skip(kwargs):
                _log(f"{LOG_PREFIX} SKIP (metadata flag) | id={response_id} model={model}")
                return

            # ── Guard: token count below threshold ───────────────────────────
            token_count = self._get_token_count(response_obj)
            if token_count < MIN_TOKEN_THRESHOLD:
                _log(
                    f"{LOG_PREFIX} SKIP (tokens={token_count} < {MIN_TOKEN_THRESHOLD}) | "
                    f"id={response_id} model={model}"
                )
                return

            # ── Extract texts ────────────────────────────────────────────────
            response_text = self._extract_response_text(response_obj)
            if not response_text.strip():
                _log(f"{LOG_PREFIX} SKIP (empty response) | id={response_id}")
                return

            request_context = self._extract_request_messages(kwargs)

            _log(
                f"{LOG_PREFIX} VERIFYING | id={response_id} model={model} "
                f"tokens={token_count} chars={len(response_text)}"
            )

            # ── Run verification ─────────────────────────────────────────────
            t0 = time.time()
            result = await self._run_verification(request_context, response_text)
            elapsed = time.time() - t0

            verdict = result.get("verdict", "unknown")
            issue_count = len(result.get("issues", []))
            summary = result.get("summary", "")

            log_level = "FLAG" if verdict == "flag" else "PASS"
            _log(
                f"{LOG_PREFIX} {log_level} | id={response_id} model={model} "
                f"verdict={verdict} issues={issue_count} "
                f"time={elapsed:.2f}s | {summary}"
            )

            if verdict == "flag" and result.get("issues"):
                for i, issue in enumerate(result["issues"], 1):
                    _log(
                        f"{LOG_PREFIX}   Issue {i}: [{issue.get('type', '?')}] "
                        f"{issue.get('excerpt', '')[:80]} — {issue.get('explanation', '')[:120]}"
                    )

            # ── Store in Redis ────────────────────────────────────────────────
            self._store_result(response_id, result, model)

        except Exception as e:
            _log(f"{LOG_PREFIX} ERROR in verification: {e}")
            _log(f"{LOG_PREFIX} {traceback.format_exc()}")


# ── Module-level instance for LiteLLM callback registration ─────────────────
factual_verifier_instance = FactualVerifierCallback()

# ── Self-register in async callback list ─────────────────────────────────────
# The _is_async_callable check in LiteLLM's LoggingCallbackManager returns
# False for CustomLogger instances (it checks the instance itself, not its
# methods).  This causes the callback to be added to the SYNC success list
# only, but the proxy uses ASYNC calls.  We fix this by explicitly adding
# our instance to the async list as well.
try:
    import litellm as _litellm
    if factual_verifier_instance not in _litellm._async_success_callback:
        _litellm._async_success_callback.append(factual_verifier_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm._async_success_callback")
    if factual_verifier_instance not in _litellm.success_callback:
        _litellm.success_callback.append(factual_verifier_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm.success_callback")
except Exception as _e:
    _log(f"{LOG_PREFIX} WARNING: Could not self-register: {_e}")
