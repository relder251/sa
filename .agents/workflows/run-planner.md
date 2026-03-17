---
description: Trigger the Phase 1 planning workflow in n8n with a prompt
---

Run the Agentic SDLC Phase 1 planner with the following prompt: $ARGUMENTS

Use this command:
```bash
curl -X POST http://localhost:5678/webhook/agentic-planner-002/webhook/generate-plan \
  -H "Content-Type: application/json" \
  -d "{\"prompt\": \"$ARGUMENTS\"}"
```

Show the full response and the path to the saved plan file.
