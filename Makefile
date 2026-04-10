# =============================================================================
# Makefile — agentic-sdlc compose stack
# Usage: make <target> [ENV=local|prod|test] [SVC=service-name]
# ENV defaults to local
# =============================================================================
# -- Secrets Workflow ---------------------------------------------------------
# Source of truth: HashiCorp Vault (secret/sdlc/prod)
# Flow: Vault -> vault-env.sh -> .env.prod -> docker-compose --env-file
# NEVER edit .env.prod manually. Use:
#   make vault-rotate KEY=NAME VAL=value   # rotate a single secret
#   make vault-push                         # push all .env.prod to Vault
#   make vault-env                          # pull Vault -> .env.prod
#   make deploy                             # pull + validate + up
# -----------------------------------------------------------------------------


ENV ?= local

# Map ENV to env-file and compose files
ifeq ($(ENV),prod)
  ENV_FILE   := .env.prod
  COMPOSE_F  := -f docker-compose.yml -f docker-compose.prod.yml
else ifeq ($(ENV),test)
  ENV_FILE   := .env.test
  COMPOSE_F  := -f docker-compose.yml -f docker-compose.test.yml
else
  ENV_FILE   := .env.local
  COMPOSE_F  := -f docker-compose.yml -f docker-compose.override.yml
endif

COMPOSE := docker compose --env-file $(ENV_FILE) $(COMPOSE_F)

.PHONY: up down logs ps restart build pull config validate

## Start all services (detached)
up:
	$(COMPOSE) up -d

## Stop and remove containers
down:
	$(COMPOSE) down

## Tail logs (all services, or SVC=name for one)
logs:
ifdef SVC
	$(COMPOSE) logs -f $(SVC)
else
	$(COMPOSE) logs -f
endif

## Show running container status
ps:
	$(COMPOSE) ps

## Restart a service: make restart SVC=n8n ENV=prod
restart:
ifndef SVC
	$(error SVC is required: make restart SVC=<service-name>)
endif
	$(COMPOSE) restart $(SVC)

## Build image(s): make build SVC=webui ENV=local
build:
ifdef SVC
	$(COMPOSE) build $(SVC)
else
	$(COMPOSE) build
endif

## Pull latest images
pull:
	$(COMPOSE) pull

## Print resolved config (dry-run / debug)
config:
	$(COMPOSE) config

## Validate config without printing (alias for CI)
validate:
	$(COMPOSE) config --quiet && echo "$(ENV): config valid"

## Show help
help:
	@echo "Usage: make <target> [ENV=local|prod|test] [SVC=service-name]"
	@echo ""
	@echo "Targets:"
	@echo "  up         Start all services (detached)"
	@echo "  down       Stop and remove containers"
	@echo "  logs       Tail logs (all or SVC=name)"
	@echo "  ps         Show running container status"
	@echo "  restart    Restart a single service (SVC=name required)"
	@echo "  build      Build image(s) (optionally SVC=name)"
	@echo "  pull       Pull latest images"
	@echo "  config     Print resolved compose config"
	@echo "  validate   Validate config (quiet, for CI)"
	@echo ""
	@echo "Examples:"
	@echo "  make up ENV=prod"
	@echo "  make logs SVC=n8n ENV=prod"
	@echo "  make restart SVC=keycloak ENV=prod"
	@echo "  make validate ENV=local"

## Pull secrets from Vaultwarden into .env.prod
## Usage: source /root/.env.vault && make secrets
secrets:
	@bash scripts/pull-secrets.sh

## (deploy target defined below — see vault section)

## ── Vault secrets management ─────────────────────────────────────────────────
## Initialize Vault (one-time setup — run after first `make vault-up`)
vault-init:
	@bash scripts/vault-init.sh

## Unseal Vault after restart
vault-unseal:
	@bash scripts/vault-unseal.sh

## Push current .env.prod to Vault (migration / manual update)
vault-push:
	@bash scripts/vault-push.sh

## Pull secrets from Vault into .env.prod (run before deploy)
vault-env:
	@bash scripts/vault-env.sh

## Rotate a secret: make vault-rotate KEY=GROQ_API_KEY VAL=newkey
vault-rotate:
	@bash scripts/vault-rotate.sh $(KEY)=$(VAL)

## Start Vault container only
vault-up:
	docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d vault

## Check if .env.prod is stale (older than 1 hour)
vault-check:
	@MTIME=$$(stat -c %Y .env.prod 2>/dev/null || echo 0); \
	NOW=$$(date +%s); AGE=$$(( NOW - MTIME )); \
	if [ $$AGE -gt 3600 ]; then \
		echo "[WARN] .env.prod is $$(( AGE / 60 ))m old -- refreshing from Vault"; \
	fi

## Deploy: check freshness, fetch from Vault, unseal vault-root, then start full stack
## Two-phase startup: vault-root must be unsealed (Shamir) before main vault can
## auto-unseal via Transit. Running bare `make up` on a cold stack will fail because
## vault-root starts sealed, cascading to vault and agent_zero.
.PHONY: deploy
deploy: vault-check vault-env validate
	@echo "[deploy] Phase 1: Starting vault-root..."
	$(COMPOSE) up -d vault-root
	@sleep 3
	@bash scripts/vault-unseal.sh
	@echo "[deploy] Waiting for main vault to auto-unseal via Transit..."
	@$(COMPOSE) up -d vault
	@timeout 30 sh -c 'until docker exec vault vault status -address=http://127.0.0.1:8200 2>/dev/null | grep -q "Sealed.*false"; do sleep 2; done' \
		|| { echo "[deploy] ERROR: vault did not unseal within 30s"; exit 1; }
	@echo "[deploy] Phase 2: Starting all services..."
	$(MAKE) up ENV=prod
