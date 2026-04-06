# =============================================================================
# Makefile — agentic-sdlc compose stack
# Usage: make <target> [ENV=local|prod|test] [SVC=service-name]
# ENV defaults to local
# =============================================================================

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

## Pull secrets then start prod stack
deploy: secrets
	$(MAKE) up ENV=prod

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

## Deploy: fetch from Vault then start full prod stack
deploy: vault-env
	$(MAKE) up ENV=prod
