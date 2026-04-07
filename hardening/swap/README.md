# Swap configuration

4GB swapfile at /swapfile, enabled 2026-04-07.

## Current state
- /swapfile: 4GB, type file, priority -2
- fstab: /swapfile none swap sw 0 0

## Revert
swapoff /swapfile && rm /swapfile
Remove /swapfile line from /etc/fstab
systemctl daemon-reload
