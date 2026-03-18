#!/usr/bin/env bash
# .devcontainer/setup.sh — install test dependencies inside the devcontainer
# Called automatically via postCreateCommand after container creation.
set -euo pipefail

echo "=== Installing Python test dependencies ==="
pip install --quiet \
  pytest \
  pytest-playwright \
  playwright

echo "=== Installing Playwright Chromium browser ==="
# Chromium is used for all Playwright tests in this repo
playwright install chromium --with-deps

echo "=== Verifying installations ==="
python -m pytest --version
python -m playwright --version

echo ""
echo "=== Setup complete ==="
echo ""
echo "Run tests:          pytest tests/test_post_deploy.py -v"
echo "Dry-run backup:     bash scripts/backup.sh --dry-run"
echo "Restore validation: bash scripts/backup_test.sh <dump.sql.gz>"
echo "Smoke test:         bash scripts/smoke_test.sh"
echo ""
echo "Option 3 (connect to host Docker stack):"
echo "  Open this repo in VS Code with 'Remote Containers: Open Folder in Container'"
echo "  The Docker socket is forwarded — 'docker ps' will show your host containers."
echo "  Forward ports 4000/5678/3000/5003 to run smoke tests against your local stack."
