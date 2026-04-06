ui = true
log_level = "info"
log_file = "/vault/logs/vault.log"

storage "raft" {
  path    = "/vault/data"
  node_id = "sa-vps-node-1"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true   # nginx handles TLS termination
}

# Public API address — nginx proxies secrets.private.sovereignadvisory.ai → this
api_addr     = "https://secrets.private.sovereignadvisory.ai"
cluster_addr = "https://vault:8201"

# Performance / stability
default_lease_ttl = "168h"   # 7 days
max_lease_ttl     = "8760h"  # 1 year
