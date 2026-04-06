#!/usr/bin/env python3
"""RAG Knowledge Base Manager — CLI for managing the Qdrant-backed knowledge base.

Manages a separate Qdrant collection (`rag_knowledge_base`) for RAG grounding.
Uses Ollama nomic-embed-text (768-dim) for embeddings via REST API.
Designed to run inside the LiteLLM container via:
  docker exec litellm python3 /app/rag_manager.py <command> [args]

Commands:
  add     --text "..." | --file path  [--tags "k:v,k2:v2"]
  search  --query "..." [--top-k N]
  stats
  delete  --id POINT_ID | --tag "k:v"
  import  --dir /path/to/docs/
"""

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import quote

# ── Configuration ────────────────────────────────────────────────────────────
QDRANT_URL = os.getenv("QDRANT_API_BASE", "http://qdrant:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "nomic-embed-text")
COLLECTION_NAME = os.getenv("RAG_COLLECTION", "rag_knowledge_base")
VECTOR_SIZE = 768  # nomic-embed-text dimensions
CHUNK_SIZE = 500   # tokens per chunk (approx 4 chars per token)
CHUNK_OVERLAP = 50 # overlap tokens
CHARS_PER_TOKEN = 4  # rough approximation


# ── HTTP Helpers ─────────────────────────────────────────────────────────────
def _http(method: str, url: str, data: Optional[dict] = None,
          timeout: int = 30) -> dict:
    """Make an HTTP request and return parsed JSON."""
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
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} from {url}: {err_body}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Connection error to {url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _qdrant(method: str, path: str, data: Optional[dict] = None,
            timeout: int = 30) -> dict:
    """Call Qdrant REST API."""
    url = f"{QDRANT_URL}{path}"
    return _http(method, url, data, timeout)


# ── Embedding ────────────────────────────────────────────────────────────────
def get_embedding(text: str) -> List[float]:
    """Get embedding vector from Ollama nomic-embed-text."""
    url = f"{OLLAMA_URL}/api/embeddings"
    payload = {"model": EMBEDDING_MODEL, "prompt": text}
    result = _http("POST", url, payload, timeout=60)
    embedding = result.get("embedding", [])
    if not embedding:
        print(f"ERROR: Empty embedding returned for text: {text[:80]}...",
              file=sys.stderr)
        sys.exit(1)
    return embedding


# ── Collection Management ────────────────────────────────────────────────────
def ensure_collection() -> None:
    """Create the RAG collection if it doesn't exist."""
    # Check if collection exists
    try:
        resp = _qdrant("GET", f"/collections/{COLLECTION_NAME}")
        if resp.get("result"):
            return  # Already exists
    except SystemExit:
        pass  # Collection doesn't exist, create it

    # Create collection
    _qdrant("PUT", f"/collections/{COLLECTION_NAME}", {
        "vectors": {
            "size": VECTOR_SIZE,
            "distance": "Cosine"
        },
        "optimizers_config": {
            "indexing_threshold": 100
        }
    })
    print(f"Created collection '{COLLECTION_NAME}' (vector_size={VECTOR_SIZE})")

    # Create payload indexes for filtering
    for field in ["source", "tags", "timestamp", "chunk_index", "doc_id"]:
        field_type = "keyword"
        if field == "chunk_index":
            field_type = "integer"
        _qdrant("PUT",
                f"/collections/{COLLECTION_NAME}/index",
                {"field_name": field, "field_schema": field_type})
    print("Created payload indexes")


# ── Chunking ─────────────────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into chunks by approximate token count."""
    char_chunk = chunk_size * CHARS_PER_TOKEN
    char_overlap = overlap * CHARS_PER_TOKEN

    if len(text) <= char_chunk:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + char_chunk
        # Try to break at a sentence or paragraph boundary
        if end < len(text):
            # Look for paragraph break first
            para_break = text.rfind("\n\n", start + char_chunk // 2, end + 200)
            if para_break > start:
                end = para_break
            else:
                # Look for sentence break
                sent_break = text.rfind(". ", start + char_chunk // 2, end + 100)
                if sent_break > start:
                    end = sent_break + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - char_overlap
        if start >= len(text):
            break

    return chunks


# ── Parse Tags ───────────────────────────────────────────────────────────────
def parse_tags(tags_str: str) -> Dict[str, str]:
    """Parse 'key:value,key2:value2' into a dict."""
    if not tags_str:
        return {}
    result = {}
    for pair in tags_str.split(","):
        pair = pair.strip()
        if ":" in pair:
            k, v = pair.split(":", 1)
            result[k.strip()] = v.strip()
    return result


def tags_to_list(tags: Dict[str, str]) -> List[str]:
    """Convert tag dict to list of 'key:value' strings for Qdrant filtering."""
    return [f"{k}:{v}" for k, v in tags.items()]


# ── Commands ─────────────────────────────────────────────────────────────────
def cmd_add(args: argparse.Namespace) -> None:
    """Add text or file content to the knowledge base."""
    ensure_collection()

    text = ""
    source = "manual"

    if args.text:
        text = args.text
        source = "text_input"
    elif args.file:
        filepath = args.file
        if not os.path.isfile(filepath):
            print(f"ERROR: File not found: {filepath}", file=sys.stderr)
            sys.exit(1)
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        source = os.path.basename(filepath)
    else:
        print("ERROR: Provide --text or --file", file=sys.stderr)
        sys.exit(1)

    if not text.strip():
        print("ERROR: Empty content", file=sys.stderr)
        sys.exit(1)

    tags = parse_tags(args.tags) if args.tags else {}
    tag_list = tags_to_list(tags)
    doc_id = hashlib.md5(text.encode()).hexdigest()[:16]
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Chunk the text
    chunks = chunk_text(text)
    print(f"Chunking: {len(text)} chars → {len(chunks)} chunk(s)")

    # Embed and upsert each chunk
    points = []
    for i, chunk in enumerate(chunks):
        print(f"  Embedding chunk {i+1}/{len(chunks)}...", end=" ", flush=True)
        vector = get_embedding(chunk)
        point_id = str(uuid.uuid4())
        points.append({
            "id": point_id,
            "vector": vector,
            "payload": {
                "text": chunk,
                "source": source,
                "doc_id": doc_id,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "tags": tag_list,
                "timestamp": timestamp,
                "char_count": len(chunk)
            }
        })
        print(f"done (dim={len(vector)})")

    # Upsert in batches of 100
    batch_size = 100
    for b_start in range(0, len(points), batch_size):
        batch = points[b_start:b_start + batch_size]
        _qdrant("PUT", f"/collections/{COLLECTION_NAME}/points", {
            "points": batch
        })

    print(f"\n✅ Added {len(points)} point(s) from '{source}' "
          f"(doc_id={doc_id})")
    if tag_list:
        print(f"   Tags: {', '.join(tag_list)}")
    for p in points:
        print(f"   Point ID: {p['id']}")


def cmd_search(args: argparse.Namespace) -> None:
    """Search the knowledge base."""
    ensure_collection()

    query = args.query
    top_k = args.top_k or 5
    threshold = args.threshold or 0.0

    print(f"Searching for: '{query}' (top_k={top_k}, threshold={threshold})")
    vector = get_embedding(query)

    search_body = {
        "vector": vector,
        "limit": top_k,
        "with_payload": True,
        "score_threshold": threshold
    }

    result = _qdrant("POST",
                     f"/collections/{COLLECTION_NAME}/points/search",
                     search_body)

    hits = result.get("result", [])
    if not hits:
        print("No results found.")
        return

    print(f"\n{'='*60}")
    print(f"Found {len(hits)} result(s):")
    print(f"{'='*60}")

    for i, hit in enumerate(hits):
        score = hit.get("score", 0)
        payload = hit.get("payload", {})
        text = payload.get("text", "")
        source = payload.get("source", "unknown")
        tags = payload.get("tags", [])
        point_id = hit.get("id", "")

        print(f"\n--- Result {i+1} (score: {score:.4f}) ---")
        print(f"  ID:     {point_id}")
        print(f"  Source: {source}")
        if tags:
            print(f"  Tags:   {', '.join(tags)}")
        print(f"  Text:   {text[:300]}{'...' if len(text) > 300 else ''}")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show knowledge base statistics."""
    ensure_collection()

    info = _qdrant("GET", f"/collections/{COLLECTION_NAME}")
    result = info.get("result", {})

    points_count = result.get("points_count", 0)
    vectors_count = result.get("vectors_count", 0)
    status = result.get("status", "unknown")
    config = result.get("config", {})
    optimizer = result.get("optimizer_status", {})

    print(f"\n{'='*50}")
    print(f"RAG Knowledge Base Statistics")
    print(f"{'='*50}")
    print(f"  Collection:    {COLLECTION_NAME}")
    print(f"  Status:        {status}")
    print(f"  Points:        {points_count}")
    print(f"  Vectors:       {vectors_count}")
    print(f"  Vector Size:   {VECTOR_SIZE}")
    print(f"  Distance:      Cosine")
    print(f"  Optimizer:     {optimizer}")

    # Get some sample tags if entries exist
    if points_count > 0:
        scroll = _qdrant("POST", f"/collections/{COLLECTION_NAME}/points/scroll", {
            "limit": 100,
            "with_payload": ["source", "tags", "doc_id"]
        })
        points = scroll.get("result", {}).get("points", [])
        sources = set()
        all_tags = set()
        doc_ids = set()
        for p in points:
            pl = p.get("payload", {})
            sources.add(pl.get("source", ""))
            for t in pl.get("tags", []):
                all_tags.add(t)
            doc_ids.add(pl.get("doc_id", ""))

        print(f"  Documents:     {len(doc_ids)}")
        print(f"  Sources:       {', '.join(sorted(sources)) or 'none'}")
        if all_tags:
            print(f"  Tags:          {', '.join(sorted(all_tags))}")
    print()


def cmd_delete(args: argparse.Namespace) -> None:
    """Delete entries by ID or tag."""
    ensure_collection()

    if args.id:
        # Delete by point ID
        _qdrant("POST", f"/collections/{COLLECTION_NAME}/points/delete", {
            "points": [args.id]
        })
        print(f"✅ Deleted point: {args.id}")

    elif args.tag:
        # Delete by tag filter
        tag = args.tag.strip()
        # Count first
        count_resp = _qdrant("POST",
                             f"/collections/{COLLECTION_NAME}/points/scroll",
                             {
                                 "filter": {
                                     "must": [{
                                         "key": "tags",
                                         "match": {"value": tag}
                                     }]
                                 },
                                 "limit": 10000,
                                 "with_payload": False
                             })
        points = count_resp.get("result", {}).get("points", [])
        if not points:
            print(f"No entries found with tag '{tag}'")
            return

        point_ids = [p["id"] for p in points]
        _qdrant("POST", f"/collections/{COLLECTION_NAME}/points/delete", {
            "points": point_ids
        })
        print(f"✅ Deleted {len(point_ids)} point(s) with tag '{tag}'")

    elif args.doc_id:
        # Delete by doc_id
        count_resp = _qdrant("POST",
                             f"/collections/{COLLECTION_NAME}/points/scroll",
                             {
                                 "filter": {
                                     "must": [{
                                         "key": "doc_id",
                                         "match": {"value": args.doc_id}
                                     }]
                                 },
                                 "limit": 10000,
                                 "with_payload": False
                             })
        points = count_resp.get("result", {}).get("points", [])
        if not points:
            print(f"No entries found with doc_id '{args.doc_id}'")
            return

        point_ids = [p["id"] for p in points]
        _qdrant("POST", f"/collections/{COLLECTION_NAME}/points/delete", {
            "points": point_ids
        })
        print(f"✅ Deleted {len(point_ids)} point(s) with doc_id '{args.doc_id}'")

    else:
        print("ERROR: Provide --id, --tag, or --doc-id", file=sys.stderr)
        sys.exit(1)


def cmd_import(args: argparse.Namespace) -> None:
    """Bulk import documents from a directory."""
    dir_path = args.dir
    if not os.path.isdir(dir_path):
        print(f"ERROR: Directory not found: {dir_path}", file=sys.stderr)
        sys.exit(1)

    supported_exts = {".txt", ".md", ".json", ".csv", ".yaml", ".yml",
                      ".rst", ".log", ".py", ".js", ".html", ".xml"}
    files = []
    for root, dirs, fnames in os.walk(dir_path):
        for fname in sorted(fnames):
            ext = os.path.splitext(fname)[1].lower()
            if ext in supported_exts:
                files.append(os.path.join(root, fname))

    if not files:
        print(f"No supported files found in {dir_path}")
        print(f"Supported: {', '.join(sorted(supported_exts))}")
        return

    print(f"Found {len(files)} file(s) to import")
    tags = parse_tags(args.tags) if args.tags else {}

    success = 0
    failed = 0
    for filepath in files:
        print(f"\nImporting: {filepath}")
        try:
            # Create a mock args namespace for cmd_add
            add_args = argparse.Namespace(
                text=None,
                file=filepath,
                tags=args.tags
            )
            cmd_add(add_args)
            success += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"Import complete: {success} succeeded, {failed} failed")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="RAG Knowledge Base Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # add
    p_add = sub.add_parser("add", help="Add document to knowledge base")
    p_add.add_argument("--text", "-t", help="Text content to add")
    p_add.add_argument("--file", "-f", help="File to add (txt, md, json, csv)")
    p_add.add_argument("--tags", help="Tags as 'key:value,key2:value2'")

    # search
    p_search = sub.add_parser("search", help="Search knowledge base")
    p_search.add_argument("--query", "-q", required=True, help="Search query")
    p_search.add_argument("--top-k", "-k", type=int, default=5,
                          help="Number of results")
    p_search.add_argument("--threshold", type=float, default=0.0,
                          help="Minimum similarity score")

    # stats
    sub.add_parser("stats", help="Show knowledge base statistics")

    # delete
    p_del = sub.add_parser("delete", help="Delete entries")
    p_del.add_argument("--id", help="Point ID to delete")
    p_del.add_argument("--tag", help="Delete all entries with this tag")
    p_del.add_argument("--doc-id", help="Delete all chunks of a document")

    # import
    p_imp = sub.add_parser("import", help="Bulk import from directory")
    p_imp.add_argument("--dir", "-d", required=True,
                       help="Directory to import")
    p_imp.add_argument("--tags", help="Tags for all imported docs")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "add": cmd_add,
        "search": cmd_search,
        "stats": cmd_stats,
        "delete": cmd_delete,
        "import": cmd_import,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
