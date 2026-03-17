#!/bin/bash
set -e

echo "=================================================="
echo "🚀 Initializing Agentic SDLC Environment..."
echo "=================================================="

# 1. Verify necessary directories exist
echo "📂 Verifying project directories..."
mkdir -p workflows output

# 2. Boot Docker (reads local docker-compose.yml automatically)
echo "🚀 Starting Docker containers..."
docker compose up -d

# 3. Monitor Boot Sequence
echo "⏳ Waiting for n8n to become ready..."
while ! curl -s http://localhost:5678/healthz > /dev/null; do
  sleep 2
done
echo "✅ n8n is online!"

echo "⏳ Waiting for Ollama to become ready..."
while ! curl -s http://localhost:11434/ > /dev/null; do
  sleep 2
done
echo "✅ Ollama is online!"

# 4. Pull Local Model
echo "📥 Pulling the local Mistral model..."
docker exec ollama ollama pull mistral

# 5. Inject Workflows (reads from mounted ./workflows directory)
echo "⚙️ Injecting Phase 1 Planner into n8n..."
docker exec n8n n8n import:workflow --input=/data/workflows/phase_1_planner.json

echo "⚙️ Injecting Phase 2 Executor into n8n..."
docker exec n8n n8n import:workflow --input=/data/workflows/phase_2_executor.json

echo "=================================================="
echo "🎉 Environment Successfully Automated & Deployed!"
echo "=================================================="
