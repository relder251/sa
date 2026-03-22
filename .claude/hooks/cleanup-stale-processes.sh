#!/bin/bash
# Kill stale background processes from prior Claude Code sessions
# Patterns known to accumulate: hung docker exec log readers, stale SSH sessions

# Kill stale docker exec processes reading logs (common leftovers)
pkill -f "docker exec .* cat /var/log" 2>/dev/null || true
pkill -f "docker exec .* tail -f" 2>/dev/null || true

# Kill hung SSH commands from prior sessions (runner config, etc.)
# Only kill if they've been running more than 1 hour
find /proc -maxdepth 1 -name '[0-9]*' 2>/dev/null | while read pid_dir; do
    pid="${pid_dir##*/}"
    cmd=$(cat "$pid_dir/cmdline" 2>/dev/null | tr '\0' ' ')
    if echo "$cmd" | grep -q "ssh.*root@187.77.208.197.*config.sh\|actions-runner\|AAEWGE"; then
        etime=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
        if [ -n "$etime" ] && [ "$etime" -gt 3600 ]; then
            kill "$pid" 2>/dev/null || true
        fi
    fi
done

echo '{"systemMessage": "Stale process cleanup complete"}'
