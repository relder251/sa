#!/usr/bin/env bash
set -Eeuo pipefail

PREFIX="${PREFIX:-/usr/local/bin}"
ETC_DEFAULT="/etc/default/twingate-connector-guard"
SYSTEMD_DIR="/etc/systemd/system"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -m 0755 "$SCRIPT_DIR/twingate_connector_guard.sh" "$PREFIX/twingate_connector_guard.sh"
install -m 0644 "$SCRIPT_DIR/twingate-connector-guard.service" "$SYSTEMD_DIR/twingate-connector-guard.service"
install -m 0644 "$SCRIPT_DIR/twingate-connector-guard.timer" "$SYSTEMD_DIR/twingate-connector-guard.timer"

if [ ! -f "$ETC_DEFAULT" ]; then
  install -m 0600 "$SCRIPT_DIR/twingate-connector-guard.env.example" "$ETC_DEFAULT"
fi

systemctl daemon-reload
systemctl enable --now twingate-connector-guard.timer
systemctl restart twingate-connector-guard.service || true
systemctl status --no-pager twingate-connector-guard.timer || true

echo "Installed. Edit $ETC_DEFAULT if needed, then run:"
echo "  systemctl restart twingate-connector-guard.service"
echo "  journalctl -u twingate-connector-guard.service -f"
