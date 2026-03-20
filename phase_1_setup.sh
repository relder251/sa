#!/bin/bash
set -euo pipefail

echo "=================================================="
echo "🚀 Initializing Agentic SDLC Environment..."
echo "=================================================="

# ── Pre-flight checks ────────────────────────────────────────────────────────

if ! command -v docker &>/dev/null; then
  echo "❌ Docker is not installed or not in PATH. Aborting."
  exit 1
fi

if ! docker info &>/dev/null; then
  echo "❌ Docker daemon is not running. Aborting."
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "❌ .env file not found. Copy .env.example and fill in your keys."
  exit 1
fi

# ── 1. Verify necessary directories exist ────────────────────────────────────
echo "📂 Verifying project directories..."
mkdir -p workflows output output/opportunities backup notebooks opportunities

# ── 2. Boot Docker ───────────────────────────────────────────────────────────
echo "🚀 Starting Docker containers..."
# docker-compose.homelab.yml adds RTX 3070 GPU reservation for ollama.
# The VPS uses docker-compose.prod.yml instead and does not include homelab.yml.
docker compose -f docker-compose.yml -f docker-compose.homelab.yml up -d

# ── 3. Monitor Boot Sequence ─────────────────────────────────────────────────
echo "⏳ Waiting for n8n to become ready (timeout: 120s)..."
TIMEOUT=120
ELAPSED=0
while ! curl -s http://localhost:5678/healthz > /dev/null; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))
  if [[ $ELAPSED -ge $TIMEOUT ]]; then
    echo "❌ n8n did not become ready within ${TIMEOUT}s. Check: docker compose logs n8n"
    exit 1
  fi
done
echo "✅ n8n is online!"

echo "⏳ Waiting for Ollama to become ready (timeout: 120s)..."
ELAPSED=0
while ! curl -s http://localhost:11434/ > /dev/null; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))
  if [[ $ELAPSED -ge $TIMEOUT ]]; then
    echo "❌ Ollama did not become ready within ${TIMEOUT}s. Check: docker compose logs ollama"
    exit 1
  fi
done
echo "✅ Ollama is online!"

# ── 4. Pull Local Model ───────────────────────────────────────────────────────
echo "📥 Pulling the local Mistral model..."
docker exec ollama ollama pull mistral

# ── 5. Inject Workflows ───────────────────────────────────────────────────────
echo "⚙️  Injecting workflows into n8n..."
for workflow in workflows/*.json; do
  name="$(basename "$workflow")"
  echo "   → Importing ${name}"
  docker exec n8n n8n import:workflow --input="/data/workflows/${name}"
done

echo "=================================================="
echo "🎉 Environment Successfully Automated & Deployed!"
echo "=================================================="
