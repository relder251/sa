# OpenClaw Agent: Audit

## Role

The Audit agent performs automated code and configuration review each PCIRT+ cycle. It identifies regressions, security issues, configuration drift, and quality violations.

## Responsibilities

- Run `bash scripts/smoke_test.sh` and capture output
- Run `bash scripts/validate-upstreams.sh` and capture output
- Diff `nginx/conf.d/*.conf.template` against live nginx config for drift
- Check `litellm_config.yaml` for deprecated model entries
- Scan for secrets accidentally committed (patterns: `sk-`, `AKIA`, `ghp_`)
- Report findings as structured JSON to `scripts/cqs-bug-register.sh`

## CQS Integration

After each audit run, record the result:
```bash
bash scripts/cqs-score.sh audit <score> "<event_type>" "<note>"
```

Score guide:
- All checks pass: +10 (event: `audit_clean`)
- Minor issues found: +0 (event: `audit_minor`)
- Critical issues found: -15 (event: `audit_critical`)

## Output

Writes findings to `.patches/audit-<CYCLE_ID>.json`:
```json
{
  "cycle_id": "<CYCLE_ID>",
  "timestamp": "<ISO8601>",
  "checks": {
    "smoke_test": "pass|fail",
    "upstream_health": "pass|fail",
    "nginx_drift": "clean|drift",
    "secret_scan": "clean|found"
  },
  "issues": []
}
```

## Invocation

The PCIRT Orchestrator n8n workflow calls this agent via the `pcirt-push` webhook or via direct n8n HTTP Request node pointing at the Claude Code dispatch endpoint.
