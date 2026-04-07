#!/usr/bin/env bash
# setup-grafana-alerting.sh
#
# Idempotently creates the Grafana Telegram contact point and default
# notification policy via the Grafana provisioning API.
#
# Run this once after a fresh Grafana install or grafana_data volume wipe.
# Alert rules are provisioned by file (grafana/provisioning/alerting/alerting.yaml).
#
# Usage (on VPS):
#   source /opt/agentic-sdlc/.env
#   bash /opt/agentic-sdlc/scripts/setup-grafana-alerting.sh

set -euo pipefail

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-sovereign2026!}"
AUTH="admin:${GRAFANA_ADMIN_PASSWORD}"

# Require Telegram credentials
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID not set}"

echo "==> Creating Grafana 'Alerts' folder..."
curl -sf -X POST \
  -H 'Content-Type: application/json' \
  -u "${AUTH}" \
  "${GRAFANA_URL}/api/folders" \
  -d "{\"title\":\"Alerts\",\"uid\":\"sa-alerts\"}" 2>/dev/null | grep -q '"uid":"sa-alerts"' \
  && echo "    Created." \
  || echo "    Already exists (OK)."

echo "==> Creating Telegram contact point..."
curl -sf -X POST \
  -H 'Content-Type: application/json' \
  -u "${AUTH}" \
  "${GRAFANA_URL}/api/v1/provisioning/contact-points" \
  -d "{
    \"name\": \"Telegram\",
    \"type\": \"telegram\",
    \"settings\": {
      \"bottoken\": \"${TELEGRAM_BOT_TOKEN}\",
      \"chatid\": \"${TELEGRAM_CHAT_ID}\",
      \"message\": \"{{ if eq .Status \\\"firing\\\" }}🔴 {{ len .Alerts.Firing }} alert(s) firing{{ else }}✅ {{ len .Alerts.Resolved }} alert(s) resolved{{ end }}\n{{ range .Alerts.Firing }}• {{ .Labels.alertname }}{{ if .Labels.name }} [{{ .Labels.name }}]{{ end }}\n  {{ .Annotations.summary }}\n  {{ .Annotations.description }}\n{{ end }}{{ range .Alerts.Resolved }}✅ {{ .Labels.alertname }} resolved\n{{ end }}\"
    },
    \"disableResolveMessage\": false
  }" >/dev/null && echo "    Done."

echo "==> Updating default notification policy → Telegram..."
curl -sf -X PUT \
  -H 'Content-Type: application/json' \
  -u "${AUTH}" \
  "${GRAFANA_URL}/api/v1/provisioning/policies" \
  -d "{
    \"receiver\": \"Telegram\",
    \"group_by\": [\"alertname\", \"name\"],
    \"group_wait\": \"30s\",
    \"group_interval\": \"5m\",
    \"repeat_interval\": \"4h\"
  }" >/dev/null && echo "    Done."

echo ""
echo "✅ Grafana alerting configured."
echo "   Verify by visiting Alerting → Contact points in Grafana UI."
echo "   Then test: send a test message from the Telegram contact point."
