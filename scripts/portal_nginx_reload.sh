#!/bin/bash
# Reloads sa_nginx_private after a new service conf is written
set -euo pipefail
echo "Testing nginx config..."
docker exec sa_nginx_private nginx -t
echo "Reloading nginx..."
docker exec sa_nginx_private nginx -s reload
echo "nginx reloaded"
