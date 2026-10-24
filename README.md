# Wallet API — Observability Assignment

A SadaPay-style digital wallet (sign up, top up, send money, view history)
instrumented end to end: Prometheus + Grafana for metrics, Filebeat +
Elasticsearch + Kibana for logs, plus two reproducible experiments.

Built for **Assignment 1: Observability**, Enterprise Software Development.

---

## Master Submission Report (100 Marks)

The primary grading document is **[`docs/REPORT.md`](docs/REPORT.md)**, which consolidates all sections (Parts A through E) with tables, PromQL/KQL queries, architecture diagrams, and empirical results.

| Section                          |                           Section in Report                           | Details                                                     |
| -------------------------------- | :-------------------------------------------------------------------: | ----------------------------------------------------------- |
| **Part A: Problem & Solution**   | [Part A in `REPORT.md`](docs/REPORT.md#part-a--project-specification) | SadaPay-style wallet, atomic transfers, row-locking         |
| **Part B: Metrics & Dashboards** |  [Part B in `REPORT.md`](docs/REPORT.md#part-b--metrics--dashboards)  | All 4 metric types, Grafana queries, host metrics on `Anas` |
| **Part C: Logging Pipeline**     |   [Part C in `REPORT.md`](docs/REPORT.md#part-c--logging-pipeline)    | Structured JSON, Filebeat parsing, retention, Kibana KQL    |
| **Part D: System Design**        |     [Part D in `REPORT.md`](docs/REPORT.md#part-d--system-design)     | Architecture diagram, pull vs push, metric/log traces       |
| **Part E: Experiments**          |      [Part E in `REPORT.md`](docs/REPORT.md#part-e--experiments)      | E.1 Latency anomaly (p95 spike) & E.2 Cardinality explosion |

_Detailed standalone references remain available under `docs/`:_

- [`docs/METRICS.md`](docs/METRICS.md) — Comprehensive metric definitions and PromQL queries.
- [`docs/LOGGING.md`](docs/LOGGING.md) — Logging pipeline architecture and Kibana discovery.
- [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) — Architectural diagrams and failure mode analysis.
- [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md) — Latency anomaly and cardinality explosion runbooks.

---

## Part A — The project

**Problem.** Sending money between people in Pakistan still commonly means
cash, bank transfers with account numbers, or branch visits. A wallet app lets
someone hold a balance and send it to another user identified by phone or
email.

**Intended users.** Individuals making small peer-to-peer transfers —
splitting a bill, sending money home, paying a freelancer.

**Solution.** A REST API where a user signs up (which creates a wallet), tops
it up, sends money to another user by email or phone, and reviews their
transaction history. Transfers are atomic: both wallets are row-locked in
sorted id order and the debit and credit commit together or not at all.
Rejections (insufficient balance, unknown recipient, self-transfer) are
recorded with a reason rather than a generic failure.

**Scope.** Deliberately limited: no real payment rails, no KYC, no cards. The
assignment's marks are for observability, so the app is a realistic but small
domain to observe.

**What works.** Everything in the API table below, verified by two automated
test suites (62 assertions total). See _Testing_.

---

## API

| Endpoint              | Method   | Auth  | Purpose                                        |
| --------------------- | -------- | ----- | ---------------------------------------------- |
| `/health`             | GET      | no    | Liveness check                                 |
| `/metrics`            | GET      | no    | Prometheus scrape target                       |
| `/auth/signup`        | POST     | no    | Create user + zero-balance wallet              |
| `/auth/login`         | POST     | no    | Get a JWT (form: `username`=email, `password`) |
| `/wallet/balance`     | GET      | yes   | Current balance                                |
| `/wallet/topups`      | POST     | yes   | Add money (simulated) → `201`                  |
| `/transfers`          | POST     | yes   | Send money; **requires `Idempotency-Key`**; rate-limited → `201` + `Location` |
| `/transfers/{id}`     | GET      | yes   | Read a transfer you sent or received           |
| `/transactions`       | GET      | yes   | History; `limit`, `offset`, `type`, `status`   |
| `/admin/faults`       | GET/POST | token | Experiment only — configure fault injection    |
| `/admin/faults/reset` | POST     | token | Experiment only — turn all faults off          |

Interactive docs at http://localhost:8000/docs once running.

---

## Running it — Option 1: Docker (easiest)

Requires Docker with ~4 GB of RAM available.

```bash
docker compose up --build
```

| Service          | URL                                   |
| ---------------- | ------------------------------------- |
| Wallet API       | http://localhost:8000/docs            |
| Cardinality demo | http://localhost:8001/docs            |
| Prometheus       | http://localhost:9090                 |
| Grafana          | http://localhost:3000 (admin / admin) |
| Elasticsearch    | http://localhost:9200                 |
| Kibana           | http://localhost:5601                 |

Grafana's datasource and dashboard are provisioned automatically — the
dashboard appears under the **Wallet App** folder with no import needed.

On a machine with less RAM, run the halves separately:

```bash
docker compose up db app prometheus node-exporter grafana        # metrics only
docker compose up db app elasticsearch kibana filebeat           # logging only
```

---

## Running it — Option 2: No Docker (Native Binaries)

Each component runs as a standalone local binary. Everything lands in `stack/` (gitignored).

### Option 2A: Windows (Native PowerShell / CMD)

#### 1. Setup Python & Dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

#### 2. Download and Configure Stack

```powershell
python scripts/setup_windows_stack.py
```

This automatically downloads Prometheus, Grafana, and `windows_exporter` for Windows, resolves absolute paths, configures the `nodename` label to your machine's hostname, and generates startup `.bat` files in `stack/run/`.

#### 3. Start Each Component (in separate terminals or double-click from Explorer)

```powershell
.\stack\run\start-app.bat               # :8000 (Wallet API)
.\stack\run\start-windows-exporter.bat  # :9100 (Host stats)
.\stack\run\start-prometheus.bat        # :9090 (Prometheus TSDB)
.\stack\run\start-grafana.bat           # :3000 (Grafana UI - admin/admin)
```

---

### Option 2B: Linux / macOS / WSL2

#### 1. Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 2. Download the stack

```bash
./scripts/setup_stack.sh
```

#### 3. Generate machine-specific configs

```bash
./scripts/configure_stack.sh
```

#### 4. Start each component, in its own terminal

```bash
stack/run/start-app.sh               # :8000
stack/run/start-node-exporter.sh     # :9100
stack/run/start-prometheus.sh        # :9090
stack/run/start-grafana.sh           # :3000  (admin/admin)
stack/run/start-elasticsearch.sh     # :9200  (wait ~60s on first boot)
stack/run/start-kibana.sh            # :5601
stack/run/start-filebeat.sh          # start LAST (after app creates logs)
```

**Order matters for Filebeat.** Start the app and make at least one request
first, so `logs/wallet-app.json` exists. Filebeat will not tail a file that
isn't there yet.

### 5. Set up the Kibana data view (once)

Kibana → **Stack Management → Data Views → Create data view**
→ name `wallet-logs-*`, time field `@timestamp`.

---

## Using it

```bash
# Sign up
curl -X POST localhost:8000/auth/signup -H "Content-Type: application/json" \
  -d '{"name":"Alice","email":"alice@example.com","phone":"+92300000001","password":"password123"}'

curl -X POST localhost:8000/auth/signup -H "Content-Type: application/json" \
  -d '{"name":"Bob","email":"bob@example.com","phone":"+92300000002","password":"password123"}'

# Log in
TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -d "username=alice@example.com&password=password123" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Top up, transfer, check
curl -X POST localhost:8000/wallet/topups -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"amount": 5000}'

curl -X POST localhost:8000/transfers -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Idempotency-Key: demo-key-0001" \
  -d '{"recipient":"bob@example.com","amount":250}'
# repeat the exact same command: same transfer comes back with "Idempotent-Replayed: true", no second debit

curl -s localhost:8000/wallet/balance -H "Authorization: Bearer $TOKEN"
curl -s localhost:8000/metrics | grep wallet_transactions_total
```

Generate steady traffic so the dashboard has something to show:

```bash
python3 scripts/load_test.py --duration 120 --rps 5 --label demo
```

---

## Testing

```bash
python3 test_smoke.py           # 20 checks: the application itself
python3 test_observability.py   # 42 checks: metrics, faults, log pipeline
python3 test_api.py             # 47 checks: Assignment 2 HTTP API (valid / invalid / repeated / rate limit)
python3 test_openapi.py         # 25 checks: spec validity, drift, contract test
python3 test_grpc.py            # 19 checks: gRPC service, parity with HTTP, deadline, log correlation
python3 test_resilient_client.py  # 13 checks: timeout, retry, circuit breaker
python3 test_grpc_metrics.py    # Part C: per-RPC metrics, bounded labels, scrape-time gauges
python3 scripts/run_tests.py    # all seven, each in its own interpreter
```

Neither needs a running server — both drive the app in-process. They use
throwaway SQLite files and a separate `test_logs/` directory, so they never
touch real data.

`test_observability.py` verifies, among other things: all four metric types
appear in `/metrics`; histograms expose `_bucket` series for
`histogram_quantile`; the Summary exposes only `_count`/`_sum`; gauges return
to their resting values; `/metrics` excludes itself from request counts; fault
injection fires at the configured interval and resets cleanly; every log line
is valid JSON with the required fields; and no password or bcrypt hash ever
reaches the log file.

Rebuild the Grafana dashboard after editing panels:

```bash
python3 scripts/build_dashboard.py
```

---

## Running the experiments

Full runbooks with recorded results: [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md).

**Anomaly (E.1)** — three stages, ~3 minutes each:

```bash
python3 scripts/load_test.py --duration 180 --rps 5 --label baseline

curl -X POST http://127.0.0.1:8000/admin/faults \
  -H "X-Admin-Token: local-admin-token" -H "Content-Type: application/json" \
  -d '{"latency_enabled": true, "latency_ms": 500, "latency_every_n": 5}'

python3 scripts/load_test.py --duration 180 --rps 5 --label fault

curl -X POST http://127.0.0.1:8000/admin/faults/reset -H "X-Admin-Token: local-admin-token"

python3 scripts/load_test.py --duration 180 --rps 5 --label recovery
```

**Cardinality (E.2)** — run twice, once per mode:

```bash
CARDINALITY_MODE=on  python3 cardinality_demo.py   # then: count(demo_requests_total) -> 100
CARDINALITY_MODE=off python3 cardinality_demo.py   # then: count(demo_requests_total) -> 1

python3 scripts/drive_cardinality.py --requests 120
```

---

## Cleaning up safely

**Fault injection** is in-memory only. One call clears it; restarting the app
also clears it:

```bash
curl -X POST http://127.0.0.1:8000/admin/faults/reset -H "X-Admin-Token: local-admin-token"
```

**Docker** — containers, then volumes (this deletes all metric and log history):

```bash
docker compose down          # stop, keep data
docker compose down -v       # stop and delete all volumes
```

**No Docker** — Ctrl-C each component, then:

```bash
rm -rf stack/run/prometheus-data     # metric history
rm -rf stack/run/grafana-data        # Grafana's own DB
rm -rf stack/run/filebeat-data       # Filebeat read positions
rm -rf logs/ test_logs/              # application logs
rm -f wallet.db runtime.db test_*.db # app data
rm -rf stack/                        # the downloaded binaries entirely
```

**Elasticsearch indices** are never deleted automatically (ILM is off), so
remove them explicitly:

```bash
curl -X DELETE "http://localhost:9200/wallet-logs-*"
```

Prometheus metrics _do_ expire on their own after 15 days
(`--storage.tsdb.retention.time=15d`).

---

## Configuration

Environment variables, all with working defaults (`.env.example`):

| Variable             | Default                 | Purpose                                                         |
| -------------------- | ----------------------- | --------------------------------------------------------------- |
| `DATABASE_URL`       | `sqlite:///./wallet.db` | Postgres URL under Docker                                       |
| `JWT_SECRET`         | `dev-secret-change-me`  | Token signing key                                               |
| `JWT_EXPIRE_MINUTES` | `60`                    | Token lifetime                                                  |
| `ADMIN_TOKEN`        | `local-admin-token`     | Guards `/admin/faults`                                          |
| `DEFAULT_CURRENCY`   | `PKR`                   | Wallet currency                                                 |
| `CASHBACK_RATE`      | `0`                     | Set to e.g. `0.01` to enable a `cashback` transaction type      |
| `LOG_DIR`            | `logs`                  | Where the JSON log file is written                              |
| `LOG_MAX_BYTES`      | `10485760`              | Rotation threshold (10 MB)                                      |
| `LOG_BACKUP_COUNT`   | `5`                     | Rotations kept (~60 MB ceiling)                                 |
| `CARDINALITY_MODE`   | `on`                    | Demo app: `on` labels by request id, `off` uses a bounded label |

---

## Layout

```
app/
  main.py                    app setup, middleware order, /metrics
  metrics.py                 ALL metric definitions, one place
  faults.py                  runtime fault injection (Part E)
  logging_config.py          JSON formatter, rotation, redaction
  middleware/
    request_context.py       assigns request_id (outermost)
    metrics_middleware.py    request counter + latency histogram
    fault_middleware.py      applies faults (innermost) - see its docstring
                             for why the nesting order matters
  routers/                   auth, wallet, transfers, admin
  services/                  business logic + where metrics are recorded
observability/
  prometheus/                prometheus.yml + .docker.yml variant
  filebeat/                  file-input and container-input variants
  grafana/
    dashboards/              generated JSON (import + provisioned variants)
    provisioning/            datasource and dashboard providers
scripts/
  setup_stack.sh             download binaries (no Docker)
  configure_stack.sh         resolve absolute paths, write start scripts
  build_dashboard.py         regenerate the Grafana dashboard
  load_test.py               repeatable load for the anomaly experiment
  drive_cardinality.py       traffic for the cardinality experiment
docs/                        the report (see table at the top)
cardinality_demo.py          standalone app for Part E.2
test_smoke.py                application tests
test_observability.py        observability tests
```

---

## Known limitations

Worth stating rather than hiding:

- Tables are created with `Base.metadata.create_all()` at startup rather than
  via migrations (Alembic). Fine at this scope; wrong for a real service.
- Elasticsearch runs single-node with security disabled. Correct for a local
  assignment, unacceptable anywhere else.
- `/admin/faults` is guarded by a shared static token. A real system would put
  it behind proper RBAC, or not ship it in production builds at all.
- Prometheus counters reset when the app restarts. Expected — `rate()` handles
  resets — and the reason the dashboard graphs rates rather than raw totals.
- The macOS build of `node_exporter` exposes far fewer host metrics than the
  Linux build, so some host panels will be empty there.
- `refresh_business_gauges()` runs a `SUM` over the wallets table on every
  balance change. Fine at this scale; at real volume it would need to be
  incremental or sampled on a timer.

---

## Credits

Built with FastAPI, SQLAlchemy, `prometheus_client`, Prometheus, Grafana,
Elasticsearch, Kibana and Filebeat. Metric naming follows the Prometheus
naming and labelling guidance; the cardinality experiment follows the
Prometheus documentation's guidance on label cardinality.


---

# Assignment 2 — HTTP API, gRPC, Metrics

Report: [`docs/A2_REPORT.md`](docs/A2_REPORT.md) (Parts A, B and C). Evidence output: `docs/evidence/`.

**Setup** (dev tools needed for tests / stub generation / OpenAPI export):
```bash
pip install -r requirements-dev.txt
```

**Run the HTTP app and the gRPC service** (two processes, one database — use the same `DATABASE_URL` for both):
```bash
uvicorn app.main:app --port 8000          # HTTP API   -> http://localhost:8000/docs
python -m grpc_service.server             # gRPC :50051, Prometheus /metrics :8002, logs/wallet-grpc.json
```
With Docker: `docker compose up --build` starts `app`, `grpc` and Postgres (plus the A1 stack).

| What | Command |
|---|---|
| OpenAPI file (regenerate / drift check) | `python scripts/export_openapi.py` / `python scripts/export_openapi.py --check` |
| Regenerate gRPC stubs from the `.proto` | `python scripts/gen_proto.py` (= `python -m grpc_tools.protoc -I proto --python_out=generated --grpc_python_out=generated --pyi_out=generated proto/wallet/v1/wallet.proto`) |
| gRPC client | `python -m clients.grpc_client --email alice@example.com --password password123 --recipient bob@example.com --amount 250 --timeout 2` |
| Same request over HTTP and gRPC, side by side | `python scripts/compare_http_grpc.py` |
| Rate limit demo (short window) | `RATE_LIMIT_TRANSFERS=5/10 uvicorn app.main:app` then `python scripts/rate_limit_demo.py` |
| Python caller: timeout / retry / circuit breaker | `python scripts/reliability_demo.py` (app running; uses the A1 fault injector to cause real failures) |
| Part C: repeatable success/failure traffic over HTTP **and** gRPC | `RATE_LIMIT_ENABLED=false uvicorn app.main:app` + `python -m grpc_service.server`, then `python scripts/part_c_traffic.py --rounds 40 --pause 0.25` |
| Part C: run the dashboard's queries against Prometheus | `python scripts/query_dashboard.py --prometheus http://localhost:9090 --range 5m` |
| Part C: rebuild the Grafana dashboard JSON | `python scripts/build_dashboard.py` (panels are in the **"Part C"** row) |
| Part C: check HTTP/gRPC DB-gauge consistency | `python scripts/check_db_gauges.py` |
| All tests | `python scripts/run_tests.py` |

New environment variables: `RATE_LIMIT_ENABLED` (true), `RATE_LIMIT_TRANSFERS` (`20/60` = 20 per 60 s per user), `IDEMPOTENCY_TTL_SECONDS` (86400), `GRPC_PORT` (50051), `GRPC_METRICS_PORT` (8002).

**Breaking changes since Assignment 1** (callers updated): `POST /wallet/topup` → `POST /wallet/topups` (`201`); `POST /transfers` now requires the `Idempotency-Key` header, returns `201`, and uses `404/409/422` instead of a blanket `400`; all errors use `{"error": {code, message, request_id}}`; `scripts/load_test.py` now spreads load over several sender accounts to stay under the rate limit.

New layout: `proto/` (contract) · `generated/` (stubs) · `grpc_service/` (server, handler, interceptor) · `clients/` (gRPC client, resilient HTTP caller) · `openapi/wallet-api.yaml` · `app/errors.py` (shared HTTP+gRPC error table) · `app/validation.py` · `app/rate_limit.py` · `app/services/idempotency_service.py`.
