#!/usr/bin/env python3
"""index_stack_docs.py — Index SA stack documentation into Qdrant for chat RAG.

Usage (run inside any container with access to litellm + qdrant):
  python3 /opt/agentic-sdlc/scripts/index_stack_docs.py

Environment overrides:
  LITELLM_URL   (default: http://litellm:4000)
  LITELLM_KEY   (default: sk-sa-prod-ce5d031e2a50ffa45d3a200c037971f81853e27ed19b894bc3630625cba0b71a)
  QDRANT_URL    (default: http://qdrant:6333)
"""
import json, os, sys, time
import urllib.request, urllib.error

LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-sa-prod-ce5d031e2a50ffa45d3a200c037971f81853e27ed19b894bc3630625cba0b71a")
QDRANT_URL  = os.getenv("QDRANT_URL", "http://qdrant:6333")
COLLECTION  = "stack-knowledge"
EMBED_MODEL = "_local-embedding"
CHUNK_SIZE  = 900  # chars — fits well inside nomic-embed-text's 8192 token window
BATCH_SIZE  = 6   # embeddings per request

DOCS = [
    ("/opt/agentic-sdlc/docs/stack-documentation.md", "stack-docs"),
    ("/opt/agentic-sdlc/docker-compose.yml",          "docker-compose"),
    ("/opt/agentic-sdlc/nginx/conf.d/portal.conf",    "portal-nginx"),
]

# ── helpers ──────────────────────────────────────────────────────────────────

def http_json(method: str, url: str, body=None, headers=None):
    data = json.dumps(body).encode() if body else None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode()
        print(f"  HTTP {e.code} from {url}: {msg[:200]}", file=sys.stderr)
        raise

def chunk_text(text: str) -> list[str]:
    """Split on double-newline boundaries, accumulating up to CHUNK_SIZE chars."""
    paragraphs = text.split('\n\n')
    chunks, buf = [], ''
    for p in paragraphs:
        candidate = buf + p + '\n\n'
        if len(candidate) > CHUNK_SIZE and buf:
            chunks.append(buf.strip())
            buf = p + '\n\n'
        else:
            buf = candidate
    if buf.strip():
        chunks.append(buf.strip())
    return [c for c in chunks if len(c) > 40]  # skip tiny fragments

def embed(texts: list[str]) -> list[list[float]]:
    resp = http_json("POST", f"{LITELLM_URL}/v1/embeddings",
                     body={"model": EMBED_MODEL, "input": texts},
                     headers={"Authorization": f"Bearer {LITELLM_KEY}"})
    return [d["embedding"] for d in resp["data"]]

def ensure_collection():
    try:
        http_json("GET", f"{QDRANT_URL}/collections/{COLLECTION}")
        print(f"Collection '{COLLECTION}' exists — will overwrite points")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            http_json("PUT", f"{QDRANT_URL}/collections/{COLLECTION}",
                      body={"vectors": {"size": 768, "distance": "Cosine"}})
            print(f"Created collection '{COLLECTION}'")
        else:
            raise

def upsert(points: list[dict]):
    http_json("PUT", f"{QDRANT_URL}/collections/{COLLECTION}/points",
              body={"points": points})

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ensure_collection()

    all_chunks = []
    for path, tag in DOCS:
        if not os.path.exists(path):
            print(f"  skip (not found): {path}")
            continue
        text = open(path).read()
        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            all_chunks.append({"text": chunk, "source": tag, "chunk_index": i})
        print(f"  {tag}: {len(chunks)} chunks from {path}")

    if not all_chunks:
        print("No chunks to index — check doc paths.")
        return

    print(f"\nEmbedding {len(all_chunks)} chunks in batches of {BATCH_SIZE}...")
    points = []
    for i in range(0, len(all_chunks), BATCH_SIZE):
        batch = all_chunks[i:i+BATCH_SIZE]
        embeddings = embed([b["text"] for b in batch])
        for j, (meta, emb) in enumerate(zip(batch, embeddings)):
            points.append({
                "id": i + j,
                "vector": emb,
                "payload": {**meta, "ts": int(time.time())}
            })
        print(f"  batch {i//BATCH_SIZE+1}/{(len(all_chunks)+BATCH_SIZE-1)//BATCH_SIZE} done", flush=True)

    print(f"\nUpserting {len(points)} points into Qdrant...")
    # Upload in chunks of 50 to stay within request limits
    for i in range(0, len(points), 50):
        upsert(points[i:i+50])
    print(f"Done — {len(points)} points indexed into '{COLLECTION}'")

if __name__ == "__main__":
    main()
