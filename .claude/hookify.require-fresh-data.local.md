---
name: require-fresh-data
enabled: true
event: bash
action: warn
conditions:
  - field: command
    operator: regex_match
    pattern: (password|api_key|token|secret|AUTH|KEY)\s*=\s*['"]\S{6,}['"]
---

⚠️ **Hardcoded credential detected in command**

You appear to be using a credential value inline rather than fetching it live from the source.

**Fresh Data Rule:** Never use credential values recalled from a prior session or conversation summary.

Fetch live instead:
```bash
ssh root@187.77.208.197 'grep <KEY_NAME> /opt/agentic-sdlc/.env'
# or
export MY_SECRET=$(ssh root@187.77.208.197 'grep MY_SECRET /opt/agentic-sdlc/.env | cut -d= -f2')
```

If you already fetched this value in the current session, proceed — this is a reminder, not a block.
