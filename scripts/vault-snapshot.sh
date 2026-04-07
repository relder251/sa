#!/usr/bin/env sh
# vault-snapshot.sh — run inside vault container via Ofelia
# Creates a raft snapshot at /vault/data/latest_snapshot.snap
set -e
TOKEN=$(cat /vault/data/.backup-token | cut -d= -f2)
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=$TOKEN
vault operator raft snapshot save /vault/data/latest_snapshot.snap
echo "Vault snapshot saved"
