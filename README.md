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
