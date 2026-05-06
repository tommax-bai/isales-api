# isales-api

Management HTTP API + WebSocket call-event proxy for the iSales platform. See
the OpenSpec change `impl-api` in the
[`isales`](https://github.com/tommax-bai/isales) meta-repo for design and
tasks.

## Surface

- `POST /auth/login` (`username` + `password` → JWT) and `GET /auth/me`
- CRUD: `/campaigns` (with nested `role_config` / `filler_set` / `callback_config`),
  `/campaigns/{id}/devices`, `/leads` (+ `POST /leads/import` CSV bulk),
  `/voice-models` (+ `GET /voice-models/{id}/sample` audio stream),
  `/holidays`, `/handoff-tasks` (read-only in v1 stage 2),
  `/calls` (read-only)
- `/analytics/{answer-rate,goal-rate,duration-distribution}` SQL aggregates
- `POST /campaigns/{id}/start` / `POST /campaigns/{id}/pause` → `CampaignControl`
  message in Redis queue `scheduler:campaign-control`
- `GET /ws/calls/{campaign_id}` (WebSocket) — fans out `EngineEvent` from
  Redis Pub/Sub `engine:events:campaign:{id}` to subscribed clients

## Local development

```bash
# 1. install isales-common from local sibling (development snapshot)
pip install -e ../isales-common

# 2. install this package + dev tools
pip install -e ".[dev]"

# 3. environment
export ISALES_DATABASE_URL=postgresql+asyncpg://bears@localhost:5432/isales_dev
export ISALES_REDIS_URL=redis://localhost:6379/0
export ISALES_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')

# admin login (v1: env-var-stored credentials)
export ISALES_ADMIN_USER=admin
export ISALES_ADMIN_PASSWORD_HASH=$(python -c 'import bcrypt; print(bcrypt.hashpw(b"changeme", bcrypt.gensalt()).decode())')

# 4. run
isales-api          # FastAPI on :8000
```

## Mock event publisher (dev only)

Stage 4 ships the real engine. Until then, drive `/ws/calls/{id}` end-to-end
from a CLI that publishes `EngineEvent` messages into the same Redis Pub/Sub
channel the real engine will use.

```bash
# Terminal 1: start the API (DB + Redis must be reachable)
isales-api

# Terminal 2: drive fake events for campaign 1, 1 Hz, for 60 seconds
python -m scripts.fake_engine_events --campaign-id 1 --rate-hz 1 --duration-s 60

# Terminal 3: subscribe via WebSocket (token must be a valid admin JWT)
TOKEN=$(curl -s -d 'username=admin&password=changeme' \
    http://localhost:8000/auth/login | jq -r .access_token)
websocat "ws://localhost:8000/ws/calls/1?token=$TOKEN"
```

The publisher is dev-only — there is no console-script entry-point and it
will not be packaged into production wheels.

## Tests

```bash
export ISALES_TEST_DATABASE_URL=postgresql+asyncpg://bears@localhost:5432/isales_api_test
pytest -q
ruff check . && mypy isales_api
```

WebSocket fan-out + `/campaigns/{id}/{start,pause}` tests need a Redis
reachable at `ISALES_REDIS_URL` (default `redis://localhost:6379/0`); they
skip cleanly when Redis is absent.

## Production deployment

Linux + systemd, single host (multi-instance is a v2 OpenSpec change).

```bash
sudo useradd --system --create-home isales
sudo install -d -o isales -g isales /opt/isales
sudo -u isales python3.11 -m venv /opt/isales/venv
sudo -u isales /opt/isales/venv/bin/pip install \
    "isales-common @ git+https://github.com/tommax-bai/isales-common@v0.1.2" \
    isales-api

sudo install -m 0644 deploy/isales-api.service /etc/systemd/system/isales-api.service

# Required env in the systemd override file
sudo systemctl edit isales-api
#   ISALES_JWT_SECRET=<must match telephony-api>
#   ISALES_ADMIN_USER=admin
#   ISALES_ADMIN_PASSWORD_HASH=<bcrypt hash>
#   ISALES_DATABASE_URL=postgresql+asyncpg://...
#   ISALES_REDIS_URL=redis://...

# DB schema is owned by isales-common — run alembic from that package.
sudo -u isales bash -c '
ISALES_DATABASE_URL=postgresql+asyncpg://... \
  /opt/isales/venv/bin/alembic \
  -c /opt/isales/venv/lib/python3.11/site-packages/isales_common/../alembic.ini \
  upgrade head'

sudo systemctl daemon-reload
sudo systemctl enable --now isales-api

# Logs
journalctl -fu isales-api
```

### Required environment

| variable                   | meaning                                              |
|----------------------------|------------------------------------------------------|
| `ISALES_DATABASE_URL`      | PG asyncpg URL                                       |
| `ISALES_REDIS_URL`         | Redis URL — Pub/Sub for WS, scheduler queue lpush    |
| `ISALES_JWT_SECRET`        | HS256 secret, **shared with telephony-api**          |
| `ISALES_ADMIN_USER`        | v1 single-admin username                             |
| `ISALES_ADMIN_PASSWORD_HASH` | bcrypt hash of admin password                      |

### uvicorn worker count

The systemd unit pins to 1 worker. WebSocket connections live in the worker
process; multi-worker would require sticky session routing for
`/ws/calls/{id}`. Multi-instance scaling is a v2 OpenSpec change candidate
(sticky session via L7 LB or Redis Streams + cross-instance fan-out).
