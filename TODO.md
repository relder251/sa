# TODO

## In Progress

## Pending


- [ ] **#2** Add Vault data + Vaultwarden data volumes to backup | LOE: Low | ROI: Critical | Impact: Critical
- [ ] **#3** Add swap space | LOE: Low | ROI: Critical | Impact: High
- [ ] **#4** Set memory limits on all containers | LOE: Medium | ROI: Critical | Impact: Critical
- [ ] **#5** Set CPU limits on all containers | LOE: Medium | ROI: Critical | Impact: Critical
- [ ] **#6** Add off-site backup destination | LOE: Medium | ROI: Critical | Impact: Critical
- [ ] **#7** Cap cAdvisor CPU/RAM + increase scrape interval to 30s | LOE: Low | ROI: High | Impact: High
- [ ] **#8** Constrain Keycloak JVM heap (-Xmx) | LOE: Low | ROI: High | Impact: High
- [ ] **#9** PID limit + CPU cap on Agent Zero | LOE: Low | ROI: High | Impact: High
- [ ] **#10** Install fail2ban for SSH brute force protection | LOE: Low | ROI: High | Impact: High
- [ ] **#11** Add n8n encryption key + Agent Zero usr volumes to backup | LOE: Low | ROI: High | Impact: High
- [ ] **#12** Prometheus storage retention limits | LOE: Low | ROI: High | Impact: Medium
- [ ] **#13** Tune PostgreSQL for available RAM | LOE: Low | ROI: High | Impact: Medium
- [ ] **#14** Add Content-Security-Policy header to nginx | LOE: Low | ROI: High | Impact: Medium
- [ ] **#15** CORS origin allowlist on public n8n webhook | LOE: Low | ROI: High | Impact: Medium
- [ ] **#16** Watchtower pre-update hook commits image tags to git | LOE: Medium | ROI: High | Impact: High
- [ ] **#17** Configure Grafana alerting -> Telegram | LOE: Medium | ROI: High | Impact: High
- [ ] **#18** Configure Vault auto-unseal | LOE: Medium | ROI: High | Impact: High
- [ ] **#19** Scope down docker.sock on pipeline/test containers | LOE: Medium | ROI: High | Impact: Medium
- [ ] **#20** Add Loki log aggregation | LOE: High | ROI: High | Impact: High
- [ ] **#21** Add `make validate` to deploy target | LOE: Low | ROI: Medium | Impact: Medium
- [ ] **#22** Prometheus scrape interval -> uniform 30s | LOE: Low | ROI: Medium | Impact: Medium
- [ ] **#23** Add periodic LiteLLM spend log cleanup | LOE: Low | ROI: Medium | Impact: Medium
- [ ] **#24** Add `no-new-privileges` security option to all containers | LOE: Low | ROI: Medium | Impact: Medium
- [ ] **#25** Archive .bak files and one-time fix scripts to _archive/ | LOE: Low | ROI: Medium | Impact: Low
- [ ] **#26** Archive stale compose files to _archive/ | LOE: Low | ROI: Low | Impact: Low

- [ ] **#27** Fix postgres collation version mismatch (`ALTER DATABASE postgres REFRESH COLLATION VERSION`) — silences pg_dumpall warning; glibc 2.36 in backup container vs 2.41 recorded in DB | LOE: Low | ROI: Low | Impact: Low

## Done
- [x] **#1a** Store Vault root token + unseal keys in Vaultwarden; remove `/root/.vault-keys` from VPS | commit — | 2026-04-07
- [x] **#1** Disable `PasswordAuthentication` + `PermitRootLogin` on SSH | commit 2b7f3f3 | 2026-04-07