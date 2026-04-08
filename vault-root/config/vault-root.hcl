ui = false
log_level = "info"

storage "file" {
  path = "/vault/data"
}

listener "tcp" {
  address     = "0.0.0.0:8300"
  tls_disable = true
}

api_addr          = "http://vault-root:8300"
default_lease_ttl = "87600h"
max_lease_ttl     = "87600h"
