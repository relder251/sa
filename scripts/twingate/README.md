# Twingate connector automation

This bundle rotates or provisions a Twingate connector with the official GraphQL Admin API, writes the new connector tokens into a Docker Compose `.env` file, and recreates the connector service.

## What it does

1. Looks up a Twingate Remote Network by name.
2. Finds an existing connector by name inside that Remote Network, or creates it.
3. Generates a fresh connector token pair.
4. Updates your Docker Compose `.env` file.
5. Runs `docker compose up -d --force-recreate twingate`.

## Why this is valid

Twingate documents that:

- the Admin API is GraphQL and can create, update, delete, and generate tokens for connectors
- the API endpoint is `https://<network>.twingate.com/api/graphql/`
- API access requires an API key generated under **Settings > API**
- connector tokens are unique per connector and should not be reused across multiple connectors

## Requirements

- Python 3.9+
- Docker and Docker Compose plugin on the VPS
- A Twingate API key with write/provision permissions
- A working Docker Compose project with a `twingate` service that reads these `.env` variables:
  - `TWINGATE_NETWORK`
  - `TWINGATE_ACCESS_TOKEN`
  - `TWINGATE_REFRESH_TOKEN`

## Generate the API key

In Twingate Admin Console:

- go to **Settings > API**
- click **Generate Token**
- use a permission level that can modify objects, such as **Read & Write** or **Read, Write & Provision**

## Recommended deployment on your VPS

Copy this bundle to `/opt/agentic-sdlc/scripts/twingate/` or a similar protected path.

### 1. Make it executable

```bash
chmod +x rotate-twingate-connector.sh twingate_connector_rotate.py
```

### 2. Export the API key

```bash
export TWINGATE_API_KEY='replace-with-your-admin-api-key'
```

### 3. Dry run first

```bash
./rotate-twingate-connector.sh --dry-run --verbose
```

### 4. Run it for real

```bash
./rotate-twingate-connector.sh --verbose
```

## Variables

The shell wrapper accepts these environment variables:

- `TWINGATE_API_KEY` — required
- `TWINGATE_NETWORK` — Twingate subdomain only, default `relder`
- `TWINGATE_REMOTE_NETWORK` — default `Homelab Network`
- `TWINGATE_CONNECTOR_NAME` — connector display name, default `friendly-jaguar`
- `TWINGATE_LABEL_HOSTNAME` — optional metadata label, defaults to current hostname
- `COMPOSE_DIR` — default `/opt/agentic-sdlc`
- `ENV_FILE` — default `$COMPOSE_DIR/.env`
- `COMPOSE_SERVICE` — default `twingate`

## Example systemd timer or cron

Cron example, weekly rotation at 03:15 Sunday:

```cron
15 3 * * 0 TWINGATE_API_KEY=your-key-here /opt/agentic-sdlc/scripts/twingate/rotate-twingate-connector.sh >> /var/log/twingate-rotate.log 2>&1
```

## Notes

- The script creates a `.bak` backup of the target `.env` before writing changes.
- The script does not delete old connectors.
- If the named connector does not exist, it creates it unless `--no-create` is used.
- Use one unique connector name per running connector instance.
