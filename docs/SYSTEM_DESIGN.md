# System design (Part D)

## 1. What each component does

| Component            | Port | Role                                                  | Talks to                                  |
| -------------------- | ---- | ----------------------------------------------------- | ----------------------------------------- |
| Wallet API (FastAPI) | 8000 | The application. Exposes `/metrics`, writes JSON logs | Postgres/SQLite; scraped by Prometheus    |
| Postgres (or SQLite) | 5432 | Source of truth for users, wallets, transactions      | Wallet API only                           |
| Cardinality demo     | 8001 | Isolated app for the Part E.2 experiment              | Scraped by Prometheus                     |
| Windows / Node Exporter | 9100 | Exposes host CPU / memory / disk / network (windows_exporter for Windows host, node_exporter for Linux/Docker) | Scraped by Prometheus |
| Prometheus           | 9090 | Scrapes and stores time series; evaluates PromQL      | Pulls from 8000, 8001, 9100, itself       |
| Grafana              | 3000 | Queries Prometheus, renders dashboards                | Prometheus                                |
| Filebeat             | -    | Tails the JSON log file, parses, ships                | Reads log file; writes to Elasticsearch   |
| Elasticsearch        | 9200 | Stores and indexes log documents                      | Receives from Filebeat; queried by Kibana |
| Kibana               | 5601 | Log search UI                                         | Elasticsearch                             |

### How they communicate

Two distinct patterns, and the difference matters:

**Metrics are pulled.** Prometheus makes an HTTP GET to `/metrics` every 5
seconds. The app does not know or care whether anyone is scraping it, it just
keeps numbers in memory and serves them on request. Consequence: if Prometheus
is down, the app is completely unaffected; it simply loses those data points
permanently, because nothing buffers them.

**Logs are pushed, with a buffer.** The app writes to a file; Filebeat tails
it and pushes to Elasticsearch. Filebeat keeps a registry of its read position,
so if Elasticsearch is down Filebeat retries and resumes rather than losing
data. Logs are therefore more durable than metrics under component failure , 
but the file is the buffer, so log loss becomes possible if the file rotates
away faster than Filebeat can ship it.

### Where data is stored, and why

| Data                         | Store                                         | Why this store                                                                                                                                                                                                  |
| ---------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Users, wallets, transactions | Postgres                                      | Needs ACID transactions. A transfer debits one row and credits another; both must commit or neither. `execute_transfer` takes `SELECT … FOR UPDATE` locks on both wallets in sorted id order to avoid deadlocks |
| Metrics                      | Prometheus TSDB                               | Purpose-built for numeric series over time; compresses well; aggregation (`rate`, `histogram_quantile`) is the query language                                                                                   |
| Logs                         | Elasticsearch                                 | Full-text and field search over semi-structured documents. Answers "find this request id", which a TSDB cannot                                                                                                  |
| Dashboards                   | Grafana (provisioned from files in this repo) | Version-controlled rather than clicked into existence                                                                                                                                                           |

The split is deliberate: **metrics answer "is something wrong and how bad",
logs answer "what exactly happened to this one request".** Putting a request
id in a metric label would collapse that distinction and blow up cardinality , 
which is precisely what Part E.2 demonstrates.

### What happens if a component stops

| Fails         | Effect on users                          | Effect on observability                                                                    | Recovery                                                                   |
| ------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| Postgres      | **App breaks.** The only hard dependency | App still serves `/metrics`, still logs errors                                             | Restart DB; app reconnects                                                 |
| Wallet API    | App down                                 | No new metrics or logs. Prometheus records the target as down; `up{job="wallet-app"} == 0` | Restart; counters reset to 0, gauges re-primed from DB by `prime_gauges()` |
| Prometheus    | None                                     | Metric gap for the outage, permanently, nothing buffers scrapes                           | Restart; stored history intact on disk                                     |
| Grafana       | None                                     | Dashboards unavailable; data still being collected                                         | Restart; provisioned dashboards reload from files                          |
| Windows / Node Exporter | None | Host panels go blank; app metrics unaffected | Restart |
| Filebeat      | None                                     | Logs accumulate in the file; **nothing is lost** as long as the file has not rotated away  | Restart; resumes from its registry position                                |
| Elasticsearch | None                                     | Filebeat retries and backs off; Kibana unusable                                            | Restart; Filebeat drains its backlog                                       |
| Kibana        | None                                     | No log UI; Elasticsearch still queryable via `curl`                                        | Restart                                                                    |

The single point of failure for **users** is Postgres. Everything else is a
single point of failure only for _visibility_. That asymmetry is the point of
keeping the observability stack out of the request path, note that the app
never blocks on a metric write or a log ship.

### Counter resets on restart

Restarting the app resets every counter to zero. This is expected and
Prometheus handles it: `rate()` and `increase()` detect the drop and treat it
as a reset rather than a negative rate. This is also why the dashboard uses
`rate(...)` almost everywhere rather than raw counter values, and why
`prime_gauges()` exists, gauges describe current state and must be correct
immediately after a restart, so they are recomputed from the database at
startup rather than left at zero.

### Things not yet fully understood

Stated honestly, as the assignment invites:

- Prometheus's TSDB compaction and block layout on disk, the retention flag
  is used, but the details of how blocks are merged are not explored here.
- Elasticsearch shard sizing and when the single-node default stops being
  adequate. Security is disabled, which is fine locally and wrong anywhere else.
- Filebeat's backpressure behaviour under sustained Elasticsearch slowness
  (how large the internal queue grows before it stops reading the file).
- Exact interpolation error in `histogram_quantile` when a quantile falls in a
  wide bucket; the approximation is understood in principle, not quantified.

---

## 2. Following one metric end to end

**Metric: `wallet_transactions_total{type="transfer", status="completed"}`**

### Step 1, Code updates it

`app/services/transfer_service.py`, after the database commit succeeds:

```python
metrics.transactions_total.labels(type="transfer", status="completed").inc()
metrics.transfer_amount_pkr.observe(amount)
metrics.refresh_business_gauges(db)
```

It is placed **after** `db.commit()` deliberately: the counter should reflect
money that actually moved, not money that was about to move. The metric lives
in the process's memory (`prometheus_client`'s default registry), nothing is
sent anywhere at this moment.

### Step 2, The value appears at `/metrics`

On the next scrape, `generate_latest()` renders the registry as text. From a
real scrape during the recorded run:

```
# HELP wallet_transactions_total Total wallet transactions recorded, by type and final status.
# TYPE wallet_transactions_total counter
wallet_transactions_total{status="completed",type="topup"} 3.0
wallet_transactions_total{status="completed",type="transfer"} 170.0
wallet_pending_transfers 0.0
wallet_money_in_system_pkr 1.5e+06
wallet_registered_users 2.0
```

### Step 3, Prometheus collects and stores it

The `wallet-app` job in `prometheus.yml` GETs `http://localhost:8000/metrics`
every 5 seconds. Prometheus attaches the labels configured on the target , 
`job="wallet-app"`, `instance="localhost:8000"`, `service="wallet-api"`,
`env="local"`, and appends a `(timestamp, value)` sample to the series. The
full series identity is:

```
wallet_transactions_total{job="wallet-app", instance="localhost:8000",
                          service="wallet-api", env="local",
                          type="transfer", status="completed"}
```

Samples go to the write-ahead log, then into on-disk TSDB blocks, and are
deleted automatically after 15 days.

**Values across three consecutive scrapes** (counter only ever increases):

| Time     | Value |
| -------- | ----- |
| 19:35:45 | 61    |
| 19:35:50 | 88    |
| 19:35:55 | 115   |

### Step 4, Grafana queries and displays it

The "Transactions per minute by type" panel sends this to Prometheus over
HTTP:

```promql
sum by (type) (rate(wallet_transactions_total{status="completed"}[1m])) * 60
```

- `rate(...[1m])` computes per-second increase over a 1-minute window:
  `(115 − 61) / 30s ≈ 1.8/s`
- `* 60` converts to per minute: ≈ 108 transfers/min
- `sum by (type)` collapses the `job`/`instance`/`env` labels, leaving one
  series per transaction type

The chart shows a stacked area per type. The raw counter value (170) is never
displayed, what matters operationally is the **slope**, not the total.

---

## 3. Following one log end to end

**Log: a completed transfer**

### Step 1, Code writes it

`app/services/transfer_service.py`:

```python
app_logger.info(
    "Transfer completed",
    extra={
        "event": "transfer_completed",
        "transaction_id": txn.id,
        "from_wallet_id": sender_wallet.id,
        "to_wallet_id": recipient_wallet.id,
        "amount": amount,
        "cashback_amount": cashback_amount,
    },
)
```

`RequestIdFilter` attaches `request_id` from the context variable set by
`RequestContextMiddleware`, and `JsonFormatter` renders one line.

### Step 2, The original log line

Appended to `logs/wallet-app.json` (real line from the recorded run):

```json
{
  "@timestamp": "2026-09-18T19:35:32.284407+00:00",
  "service": "wallet-api",
  "level": "INFO",
  "logger": "wallet_app",
  "message": "Transfer completed",
  "request_id": "317a6c8c-11ca-4162-9e7d-bb8f0b9a2ddc",
  "event": "transfer_completed",
  "transaction_id": "36a6de63-3a4f-4dd2-856e-8451282438db",
  "from_wallet_id": "ac554213-6a27-403d-844b-3c199389a87f",
  "to_wallet_id": "c1b6cadd-6c4b-4701-b37b-c13e2427fee7",
  "amount": 100.0,
  "cashback_amount": 0.0
}
```

### Step 3, Filebeat collects and parses it

The `filestream` input reads the line; the `ndjson` parser decodes it with
`target: ""`, promoting every key to the document root. The `drop_fields`
processor removes Filebeat's `agent`/`ecs`/`input` metadata, and `add_fields`
attaches `pipeline: filebeat-file-input`.

**Change to the log's format at this stage:** the line arrives as a _string_
and leaves as a _document with typed fields_. `amount` becomes a numeric field
(so `amount >= 1000` works as a range query), `@timestamp` becomes the
document's time axis, and `request_id` becomes a keyword field that can be
matched exactly. Filebeat also adds `log.file.path` and `log.offset`
identifying where the line came from.

### Step 4, Elasticsearch stores it

Indexed into `wallet-logs-2026.09.18`. The stored document:

```json
{
  "_index": "wallet-logs-2026.09.18",
  "_id": "<generated>",
  "_source": {
    "@timestamp": "2026-09-18T19:35:32.284407+00:00",
    "service": "wallet-api",
    "level": "INFO",
    "logger": "wallet_app",
    "message": "Transfer completed",
    "request_id": "317a6c8c-11ca-4162-9e7d-bb8f0b9a2ddc",
    "event": "transfer_completed",
    "transaction_id": "36a6de63-3a4f-4dd2-856e-8451282438db",
    "from_wallet_id": "ac554213-6a27-403d-844b-3c199389a87f",
    "to_wallet_id": "c1b6cadd-6c4b-4701-b37b-c13e2427fee7",
    "amount": 100,
    "cashback_amount": 0,
    "pipeline": "filebeat-file-input",
    "log": { "file": { "path": ".../logs/wallet-app.json" } }
  }
}
```

### Step 5, Kibana finds it

In Discover, against the `wallet-logs-*` data view:

```
request_id : "317a6c8c-11ca-4162-9e7d-bb8f0b9a2ddc"
```

returns every line produced while handling that one request. Or, by business
meaning:

```
event : "transfer_completed" and amount >= 1000
```

### The join between the two pipelines

The metric and the log describe the _same_ transfer but answer different
questions. Grafana shows the transfer rate dropped at 19:35:51. Kibana,
filtered to that minute, shows _which_ transfers and _why_. The `X-Request-ID` header returned to the client is the thread connecting a user's
complaint to a specific log line, verified in `test_observability.py`, where
the header value returned to a client is used to find its log entries.

---

## 3. Why I chose this setup & what I don't yet understand

### Why I chose this setup
1. **The assignment required it:** The course spec specifically asked for Prometheus + Grafana (for metrics) and Filebeat + Elasticsearch + Kibana (for logging).
2. **The practical reason:** It keeps monitoring completely out of the app's way:
   - **Prometheus** just pulls numbers every 5 seconds, so it never slows down money transfers.
   - **Filebeat** ships logs in the background from a local file.
   - If Prometheus, Grafana, or Elasticsearch crash, the wallet app and database keep working with zero downtime.

### What I don't yet understand
As requested by the assignment brief (*"Note anything you do not yet understand"*):
- I understand that Kibana (port 5601) is the web interface and Elasticsearch (port 9200) stores the logs, but I don't fully understand how they interact under the hood, specifically, how Kibana translates my search bar queries (KQL) into Elasticsearch's complex internal search queries and coordinates data across distributed shards behind the scenes.
