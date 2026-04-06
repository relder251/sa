#!/bin/sh
CONTAINER="${1:-}"
if [ -z "$CONTAINER" ]; then
  echo "Error: no container name provided."
  exit 1
fi
# Try bash first, fall back to sh
exec docker exec -it "$CONTAINER" sh -c 'command -v bash >/dev/null && exec bash || exec sh'
