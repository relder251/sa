# Twingate connector guard

This bundle adds a scheduled guard around your Twingate connector deployment.

What it does:
- runs every 5 minutes via `systemd` timer
- checks the Docker connector state and recent logs
- decodes the current `TWINGATE_ACCESS_TOKEN` from your `.env` file and reads its JWT `exp`
- only auto-rotates when the failure pattern looks token-related:
  - explicit `token is expired` in logs, or
  - token is already expired, or
  - token is within the configured preemptive expiry window
- calls your existing rotation command to regenerate tokens and recreate the Docker connector

This aligns with Twingate's documented connector behavior:
- Connector deployment uses access and refresh tokens generated when a connector is provisioned or re-provisioned. citeturn183550search14turn183550search2
- Twingate provides an Admin GraphQL API and API tokens created under **Settings → API** for automation. citeturn111822search0turn111822search2turn183550search3
- Connectors and clients use time-expiring controller messages/tokens as part of registration and runtime auth. citeturn111822search4

## Files
- `twingate_connector_guard.sh` — the guard script
- `install-twingate-guard.sh` — installs the script and the `systemd` units
- `twingate-connector-guard.service` — oneshot service unit
- `twingate-connector-guard.timer` — timer unit, runs every 5 minutes
- `twingate-connector-guard.env.example` — config file template

## Install

```bash
sudo mkdir -p /opt/agentic-sdlc/scripts/twingate-guard
cd /opt/agentic-sdlc/scripts/twingate-guard
# copy files here
sudo chmod +x install-twingate-guard.sh twingate_connector_guard.sh
sudo ./install-twingate-guard.sh
```

The installer copies the script to `/usr/local/bin`, installs the `systemd` units, creates `/etc/default/twingate-connector-guard` if it does not already exist, and enables the timer.

## Configure

Edit:

```bash
sudo nano /etc/default/twingate-connector-guard
```

Set `ROTATE_COMMAND` to your working token-rotation script, for example:

```bash
ROTATE_COMMAND=/opt/agentic-sdlc/scripts/twingate/rotate-twingate-connector.sh --verbose
```

## Check and test

Run the guard immediately:

```bash
sudo systemctl restart twingate-connector-guard.service
sudo journalctl -u twingate-connector-guard.service -n 100 --no-pager
```

See the schedule:

```bash
systemctl list-timers twingate-connector-guard.timer
```

Dry run without changing anything:

```bash
sudo env DRY_RUN=1 VERBOSE=1 /usr/local/bin/twingate_connector_guard.sh
```

Force a rotation once:

```bash
sudo env FORCE_ROTATE=1 VERBOSE=1 /usr/local/bin/twingate_connector_guard.sh
```

## Trigger logic

Rotation is triggered only when one of these is true:
- logs include `token is expired` or `expired token`
- the current access token is expired
- the current access token will expire within `EXPIRY_THRESHOLD_SECONDS`
- auth failures are present and the current access token is already expired

Rotation is **not** triggered for generic connectivity issues unless token metadata also points at expiry. That avoids rotating tokens for unrelated problems like DNS, clock drift, blocked egress, or controller outages. Twingate specifically notes that connector connectivity can also fail because of clock drift. citeturn111822search1

## Notes

- The access token is a JWT, so the guard reads the `exp` claim locally from `.env` without contacting the Twingate API.
- The script rate-limits itself and suppresses repeated rotations for 10 minutes after a successful attempt.
- The guard assumes your `.env` contains the live `TWINGATE_ACCESS_TOKEN` that Docker Compose uses for the `twingate` service.
