#!/bin/sh
RAW="${1:-}"
if [ -z "$RAW" ]; then
  echo "Error: no container name provided."
  exit 1
fi
# Argument arrives base64-encoded from the portal (btoa(name))
CONTAINER=$(printf '%s' "$RAW" | base64 -d 2>/dev/null)
if [ -z "$CONTAINER" ]; then
  # Fallback: treat as plain name if base64 decode fails
  CONTAINER="$RAW"
fi
# Try bash first, fall back to sh
exec docker exec -it "$CONTAINER" sh -c 'command -v bash >/dev/null && exec bash || exec sh'
