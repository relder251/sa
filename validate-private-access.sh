#!/usr/bin/env bash
set -euo pipefail

echo '== Public listeners =='
ss -tulpn | grep -E ':(22|80|443)\b' || true

echo
echo '== Private loopback listener =='
ss -tulpn | grep -E '127\.0\.0\.1:8443\b' || true

echo
echo '== Unexpected direct host exposures =='
ss -tulpn | grep -E ':(3000|4000|5002|5678|8888|11434|5432)\b' || true

echo
echo '== Container ports =='
docker ps --format 'table {{.Names}}\t{{.Ports}}'

echo
echo '== Private nginx config check =='
docker exec sa_nginx_private nginx -t || true

echo
echo '== Private endpoint check from VPS =='
curl -skI https://127.0.0.1:8443 || true

echo
echo '== Docker internal DNS =='
docker exec sa_nginx_private getent hosts n8n webui litellm jupyter ollama pipeline-server postgres || true
