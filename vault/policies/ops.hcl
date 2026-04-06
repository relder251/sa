# ops policy — full access to sdlc secrets, read-only on sys health
path "secret/data/sdlc/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "secret/metadata/sdlc/*" {
  capabilities = ["list", "read", "delete"]
}
path "secret/delete/sdlc/*" {
  capabilities = ["update"]
}
path "secret/undelete/sdlc/*" {
  capabilities = ["update"]
}
path "secret/destroy/sdlc/*" {
  capabilities = ["update"]
}
path "sys/health" {
  capabilities = ["read", "sudo"]
}
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
