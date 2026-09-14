# Wallet API (Phase 1)

A minimal SadaPay-style wallet backend: sign up, top up your wallet, send
money to other users, and view transaction history. Built specifically as
the "app under test" for an observability assignment — Phase 2 will add
Prometheus/Grafana metrics and a Filebeat/Elasticsearch/Kibana logging
pipeline on top of this without changing the code structure.

## Stack

- FastAPI + Pydantic
- SQLAlchemy (SQLite for quick local runs, Postgres via Docker Compose)
- JWT auth (python-jose) + bcrypt password hashing (passlib)

## Quick start (no Docker, SQLite)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Visit http://127.0.0.1:8000/docs for interactive API docs.

## Quick start (Docker Compose, Postgres)

```bash
docker compose up --build
```

This starts Postgres and the app together. The app waits for Postgres's
healthcheck before starting.

## Run the smoke test

```bash
python3 test_smoke.py
```

This runs an end-to-end flow (signup, login, top-up, transfer, failure
cases, transaction history) against a throwaway SQLite file and prints
`OK:` for each check. It does not require the app server to be running —
it drives the FastAPI app in-process via `TestClient`.

## API overview

| Endpoint | Method | Auth? | Purpose |
|---|---|---|---|
| `/health` | GET | no | Liveness check |
| `/auth/signup` | POST | no | Create user + zero-balance wallet |
| `/auth/login` | POST | no | Get a JWT (form fields: `username`=email, `password`) |
| `/wallet/balance` | GET | yes | Current user's balance |
| `/wallet/topup` | POST | yes | Add money to your own wallet |
| `/transfers` | POST | yes | Send money to another user by email or phone |
| `/transactions` | GET | yes | Your transaction history |

Example curl flow:

```bash
curl -X POST localhost:8000/auth/signup -H "Content-Type: application/json" \
  -d '{"name":"Alice","email":"alice@example.com","password":"password123"}'

TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -d "username=alice@example.com&password=password123" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST localhost:8000/wallet/topup -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"amount": 1000}'
```

## Design notes for Phase 2

- All business logic lives in `app/services/` (`auth_service`,
  `wallet_service`, `transfer_service`), not in the routers. Metrics
  wrapping (counters, histograms) should go around these functions.
- `app/middleware/request_context.py` already tags every request with a
  UUID (`X-Request-ID` response header, available via `get_request_id()`
  anywhere in the code). Structured JSON logging in Phase 2 can reuse this
  instead of inventing a new correlation mechanism.
- `app/logging_config.py` sets up plain-text logging with the request ID
  included. Phase 2 should swap the formatter for JSON output so Filebeat
  can parse fields directly, without touching any of the actual log call
  sites in the services.
- Natural Prometheus metrics from this domain: a counter for transactions
  by type/status, a gauge for wallets with a pending/failed transfer, a
  histogram for request latency, and a histogram or summary for transfer
  amounts.
- `CASHBACK_RATE` in `.env` is 0 by default; set it (e.g. `0.01`) if you
  want a `cashback` transaction type to show up alongside `topup` and
  `transfer` for a more varied metric set.

## Known limitation

Tables are created with `Base.metadata.create_all()` on startup rather
than via migrations (e.g. Alembic). Fine for this assignment's scope;
worth calling out as a limitation in your report if you want to show
awareness of production practice.
