"""LiteLLM RAG Retrieval Callback — Grounding layer that enriches prompts with
relevant context from a Qdrant-backed knowledge base.

Pre-call hook that:
1. Extracts the user's latest message from the request
2. Queries Qdrant `rag_knowledge_base` for relevant context
3. Injects grounding context into the system message if relevant hits found
4. Stores retrieval metrics in Redis

This is a GROUNDING layer, NOT a content filter — it adds factual context
to reduce hallucinations without censoring or modifying user intent.
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import redis
from litellm.integrations.custom_logger import CustomLogger

# ── Logger Setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger("rag_retrieval")
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
        with open("/tmp/rag_retrieval.log", "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
            f.flush()
    except Exception:
        pass


# ── Configuration ────────────────────────────────────────────────────────────
QDRANT_URL = os.getenv("QDRANT_API_BASE", "http://qdrant:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "nomic-embed-text")
COLLECTION_NAME = os.getenv("RAG_COLLECTION", "rag_knowledge_base")
REDIS_HOST = os.getenv("RAG_REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("RAG_REDIS_PORT", "6379"))
REDIS_TTL = int(os.getenv("RAG_REDIS_TTL", "3600"))  # 1 hour
TOP_K = int(os.getenv("RAG_TOP_K", "3"))
SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.7"))
LOG_PREFIX = "[RAG_RETRIEVAL]"

# ── Redis Client ─────────────────────────────────────────────────────────────
try:
    _redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    _redis.ping()
    _log(f"{LOG_PREFIX} Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
except Exception as e:
    _log(f"{LOG_PREFIX} WARNING: Redis connection failed: {e}")
    _redis = None

# ── Collection State Cache ───────────────────────────────────────────────────
_collection_exists: Optional[bool] = None
_collection_empty: Optional[bool] = None
_collection_check_time: float = 0
COLLECTION_CHECK_INTERVAL = 60  # Re-check collection status every 60s


# ── HTTP Helpers ─────────────────────────────────────────────────────────────
def _http_sync(method: str, url: str, data: Optional[dict] = None,
               timeout: int = 10) -> Optional[dict]:
    """Make a synchronous HTTP request and return parsed JSON. Returns None on error."""
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if QDRANT_API_KEY:
        req.add_header("api-key", QDRANT_API_KEY)
    try:
        resp = urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (HTTPError, URLError, Exception) as e:
        _log(f"{LOG_PREFIX} HTTP error {method} {url}: {e}")
        return None


# ── Collection Check ─────────────────────────────────────────────────────────
def _check_collection() -> Tuple[bool, bool]:
    """Check if collection exists and has entries. Returns (exists, is_empty).
    Caches result for COLLECTION_CHECK_INTERVAL seconds."""
    global _collection_exists, _collection_empty, _collection_check_time

    now = time.time()
    if (now - _collection_check_time) < COLLECTION_CHECK_INTERVAL:
        return (_collection_exists or False, _collection_empty if _collection_empty is not None else True)

    _collection_check_time = now

    resp = _http_sync("GET", f"{QDRANT_URL}/collections/{COLLECTION_NAME}")
    if resp is None or not resp.get("result"):
        _collection_exists = False
        _collection_empty = True
        return (False, True)

    _collection_exists = True
    points_count = resp.get("result", {}).get("points_count", 0)
    _collection_empty = (points_count == 0)
    return (True, _collection_empty)


# ── Embedding ────────────────────────────────────────────────────────────────
def _get_embedding_sync(text: str) -> Optional[List[float]]:
    """Get embedding vector from Ollama nomic-embed-text (synchronous)."""
    url = f"{OLLAMA_URL}/api/embeddings"
    payload = {"model": EMBEDDING_MODEL, "prompt": text}
    result = _http_sync("POST", url, payload, timeout=30)
    if result is None:
        return None
    embedding = result.get("embedding", [])
    if not embedding:
        _log(f"{LOG_PREFIX} Empty embedding for: {text[:60]}...")
        return None
    return embedding


# ── Qdrant Search ────────────────────────────────────────────────────────────
def _search_knowledge_base(query_text: str) -> List[dict]:
    """Search Qdrant knowledge base for relevant context.
    Returns list of {text, score, source, tags} dicts."""
    vector = _get_embedding_sync(query_text)
    if vector is None:
        return []

    search_body = {
        "vector": vector,
        "limit": TOP_K,
        "with_payload": True,
        "score_threshold": SIMILARITY_THRESHOLD
    }

    result = _http_sync(
        "POST",
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/search",
        search_body,
        timeout=10
    )

    if result is None:
        return []

    hits = result.get("result", [])
    contexts = []
    for hit in hits:
        payload = hit.get("payload", {})
        contexts.append({
            "text": payload.get("text", ""),
            "score": hit.get("score", 0),
            "source": payload.get("source", "unknown"),
            "tags": payload.get("tags", [])
        })

    return contexts


# ── Grounding Context Builder ────────────────────────────────────────────────
def _build_grounding_message(contexts: List[dict]) -> str:
    """Build the grounding context string to inject into the prompt."""
    parts = []
    parts.append("[GROUNDING CONTEXT - The following information is from the "
                 "knowledge base and should be used to ground your response:]")
    for i, ctx in enumerate(contexts, 1):
        source_info = f" (source: {ctx['source']})" if ctx['source'] != 'unknown' else ""
        parts.append(f"\n--- Context {i} (relevance: {ctx['score']:.2f}){source_info} ---")
        parts.append(ctx['text'])
    parts.append("\n[END GROUNDING CONTEXT]")
    return "\n".join(parts)


# ── Store Metrics ────────────────────────────────────────────────────────────
def _store_metrics(request_id: str, query: str, contexts: List[dict],
                   injected: bool) -> None:
    """Store retrieval metrics in Redis."""
    if _redis is None:
        return
    try:
        metrics = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "query": query[:200],
            "retrieved_count": len(contexts),
            "top_similarity_score": max((c["score"] for c in contexts), default=0),
            "grounding_injected": injected,
            "sources": [c["source"] for c in contexts]
        }
        _redis.setex(
            f"rag_retrieval:{request_id}",
            REDIS_TTL,
            json.dumps(metrics)
        )
    except Exception as e:
        _log(f"{LOG_PREFIX} Redis metrics error: {e}")


# ── Main Callback Class ─────────────────────────────────────────────────────
class RAGRetrievalCallback(CustomLogger):
    """LiteLLM callback that enriches prompts with RAG context."""

    def __init__(self):
        super().__init__()
        _log(f"{LOG_PREFIX} RAGRetrievalCallback initialized")
        _log(f"{LOG_PREFIX} Config: qdrant={QDRANT_URL}, collection={COLLECTION_NAME}")
        _log(f"{LOG_PREFIX} Config: ollama={OLLAMA_URL}, model={EMBEDDING_MODEL}")
        _log(f"{LOG_PREFIX} Config: top_k={TOP_K}, threshold={SIMILARITY_THRESHOLD}")

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> Any:
        """Intercept requests before they reach the LLM.
        Inject grounding context from the knowledge base."""
        try:
            return await self._do_rag_injection(user_api_key_dict, cache, data, call_type)
        except Exception as e:
            _log(f"{LOG_PREFIX} ERROR in pre_call_hook: {e}")
            _log(f"{LOG_PREFIX} Traceback: {traceback.format_exc()}")
            # On any error, pass through without modification
            return data

    async def _do_rag_injection(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> Any:
        """Core RAG injection logic."""
        # ── Skip conditions ──────────────────────────────────────────────

        # Only process chat completion calls
        if call_type not in ("completion", "acompletion"):
            return data

        # Check metadata for skip flags
        metadata = data.get("metadata", {}) or {}
        litellm_params_metadata = data.get("litellm_params", {}).get("metadata", {}) or {}
        # Merge metadata sources
        all_metadata = {**metadata, **litellm_params_metadata}

        # Skip if explicitly disabled
        if all_metadata.get("skip_rag") is True:
            _log(f"{LOG_PREFIX} Skipping: skip_rag=true")
            return data

        # Skip internal verification/consensus calls
        if all_metadata.get("skip_verification") is True:
            _log(f"{LOG_PREFIX} Skipping: internal verification call")
            return data
        if all_metadata.get("skip_consensus") is True:
            _log(f"{LOG_PREFIX} Skipping: internal consensus call")
            return data

        # Skip if collection doesn't exist or is empty
        exists, is_empty = _check_collection()
        if not exists:
            _log(f"{LOG_PREFIX} Skipping: collection does not exist")
            return data
        if is_empty:
            _log(f"{LOG_PREFIX} Skipping: collection is empty")
            return data

        # ── Extract user message ─────────────────────────────────────────
        messages = data.get("messages", [])
        if not messages:
            return data

        # Find the last user message
        user_message = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Handle multimodal messages — extract text parts
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    user_message = " ".join(text_parts)
                else:
                    user_message = str(content)
                break

        if not user_message or len(user_message.strip()) < 5:
            _log(f"{LOG_PREFIX} Skipping: no/short user message")
            return data

        _log(f"{LOG_PREFIX} Processing query: {user_message[:80]}...")

        # ── Search knowledge base (sync in thread to not block) ──────────
        loop = asyncio.get_event_loop()
        contexts = await loop.run_in_executor(
            None, _search_knowledge_base, user_message
        )

        # Generate a request ID for metrics
        request_id = all_metadata.get("request_id",
                      data.get("litellm_call_id",
                      str(int(time.time() * 1000))))

        if not contexts:
            _log(f"{LOG_PREFIX} No relevant context found (threshold={SIMILARITY_THRESHOLD})")
            _store_metrics(str(request_id), user_message, [], False)
            return data

        _log(f"{LOG_PREFIX} Found {len(contexts)} relevant context(s), "
             f"top_score={contexts[0]['score']:.4f}")

        # ── Inject grounding context ─────────────────────────────────────
        grounding_text = _build_grounding_message(contexts)

        # Find existing system message or create one
        system_idx = None
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                system_idx = i
                break

        if system_idx is not None:
            # Append grounding context to existing system message
            existing_content = messages[system_idx].get("content", "")
            messages[system_idx]["content"] = (
                f"{existing_content}\n\n{grounding_text}"
            )
            _log(f"{LOG_PREFIX} Appended grounding to existing system message")
        else:
            # Insert a new system message at the beginning
            messages.insert(0, {
                "role": "system",
                "content": grounding_text
            })
            _log(f"{LOG_PREFIX} Inserted new system message with grounding")

        data["messages"] = messages

        # Store metrics
        _store_metrics(str(request_id), user_message, contexts, True)

        _log(f"{LOG_PREFIX} ✅ Grounding context injected "
             f"({len(contexts)} chunks, {len(grounding_text)} chars)")

        return data

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Log successful requests that had RAG grounding (for observability)."""
        # This is a no-op for RAG retrieval — we do our work in pre_call_hook.
        # But we need this method to exist for the callback registration.
        pass

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """Log failed requests (no-op for RAG)."""
        pass


# ── Module-level instance ────────────────────────────────────────────────────
rag_retrieval_instance = RAGRetrievalCallback()

# ── Self-register in async callback list ─────────────────────────────────────
# The _is_async_callable check in LiteLLM's LoggingCallbackManager returns
# False for CustomLogger instances (it checks the instance itself, not its
# methods).  This causes the callback to be added to the SYNC success list
# only, but the proxy uses ASYNC calls.  We fix this by explicitly adding
# our instance to the async list as well.
try:
    import litellm as _litellm
    if rag_retrieval_instance not in _litellm._async_success_callback:
        _litellm._async_success_callback.append(rag_retrieval_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm._async_success_callback")
    if rag_retrieval_instance not in _litellm.success_callback:
        _litellm.success_callback.append(rag_retrieval_instance)
        _log(f"{LOG_PREFIX} Self-registered in litellm.success_callback")
except Exception as _e:
    _log(f"{LOG_PREFIX} WARNING: Could not self-register: {_e}")

_log(f"{LOG_PREFIX} Module loaded and ready")
