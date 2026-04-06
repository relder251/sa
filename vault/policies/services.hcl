# services policy — read-only for docker services injecting secrets at startup
path "secret/data/sdlc/prod" {
  capabilities = ["read"]
}
path "secret/metadata/sdlc/prod" {
  capabilities = ["read"]
}
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}
