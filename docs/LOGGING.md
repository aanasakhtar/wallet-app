# Logging pipeline (Part C)

## Pipeline shape

**No-Docker setup (the one used here):**

```
app writes JSON line -> logs/wallet-app.json -> Filebeat tails the file
  -> decodes each line into fields -> Elasticsearch index wallet-logs-YYYY.MM.dd
  -> searched in Kibana
```

**Docker setup (for anyone running docker compose):**

```
app writes JSON line to stdout -> Docker json-file driver saves it under
  /var/lib/docker/containers/<id>/<id>-json.log -> Filebeat container input
  unwraps Docker's envelope -> decode_json_fields parses the app payload
  -> Elasticsearch -> Kibana
```

The only structural difference is the input. The assignment's reference
pipeline uses Docker's log driver; without Docker the app writes the same JSON
to a file and Filebeat uses a `filestream` input instead. The document that
lands in Elasticsearch is the same either way. Configs:
`observability/filebeat/filebeat.yml` (file input) and `filebeat.docker.yml`
(container input).

---

## What is logged, and why

Logging is done at the **service layer** (`app/services/`), not in the
routers, so a log line describes a business event rather than an HTTP
detail. Every call uses `extra={...}` to attach structured fields.

| Event | Level | Where | Why it is logged |
|---|---|---|---|
| `app_startup` | INFO | `app/main.py` | Marks restarts; explains gauge resets and counter resets |
| `user_signup` | INFO | `auth_service.signup` | Growth, and ties a user id to a wallet id |
| `signup_rejected` | WARNING | `auth_service.signup` | Duplicate email, could be a user retrying or a broken form |
| `login_success` | INFO | `auth_service.login` | Establishes a session baseline for correlating later activity |
| `login_failed` | WARNING | `auth_service.login` | Carries `reason` (`bad_password` / `unknown_email`); the security signal |
| `topup_completed` | INFO | `wallet_service.topup` | Money entering the system, the audit-critical event |
| `transfer_completed` | INFO | `transfer_service` | The core business event; carries both wallet ids and the amount |
| `transfer_failed` | WARNING | `transfer_service` | Insufficient balance; a transaction row is written with status `failed` |
| `transfer_rejected` | WARNING | `transfer_service` | Rejected before any DB write (unknown recipient, self-transfer) |
| `wallet_missing` | ERROR | `wallet_service` | Should be impossible; indicates a data-integrity bug |
| `fault_injected` | WARNING | `app/faults.py` | Part E experiment audit trail |
| `fault_config_changed` / `fault_reset` | WARNING | `app/faults.py` | Marks the exact boundary of an experiment stage |
| `request_failed` | ERROR | `fault_middleware` | The 503 produced by an injected error |

### Field contract

Every line carries these, set in `app/logging_config.py`:

| Field | Example | Notes |
|---|---|---|
| `@timestamp` | `2026-09-18T19:35:32.284407+00:00` | ISO-8601, UTC. Named `@timestamp` because that is the field Elasticsearch treats as the time axis by default |
| `service` | `wallet-api` | Constant. Lets one index hold several services later |
| `level` | `INFO` / `WARNING` / `ERROR` | Severity |
| `logger` | `wallet_app` | Python logger name |
| `message` | `Transfer completed` | Human-readable |
| `request_id` | `317a6c8c-…` | Correlation id, from `app/middleware/request_context.py` |
| `event` | `transfer_completed` | Machine-readable event type, the field you actually filter on |

Plus event-specific fields (`amount`, `transaction_id`, `from_wallet_id`,
`reason`, …).

A real line from the recorded run:

```json
{"@timestamp": "2026-09-18T19:35:32.284407+00:00", "service": "wallet-api",
 "level": "INFO", "logger": "wallet_app", "message": "Transfer completed",
 "request_id": "317a6c8c-11ca-4162-9e7d-bb8f0b9a2ddc",
 "event": "transfer_completed",
 "transaction_id": "36a6de63-3a4f-4dd2-856e-8451282438db",
 "from_wallet_id": "ac554213-6a27-403d-844b-3c199389a87f",
 "to_wallet_id": "c1b6cadd-6c4b-4701-b37b-c13e2427fee7",
 "amount": 100.0, "cashback_amount": 0.0}
```

### Never logged

`_REDACT_KEYS` in `app/logging_config.py` forces `[REDACTED]` for
`password`, `password_hash`, `token`, `access_token`, `authorization`,
`secret`, even if a future call site passes one by accident. The formatter
also never logs request bodies wholesale, which is how passwords usually leak
into logs.

Wallet ids and transaction ids **are** logged: they are internal UUIDs, not
account numbers, and without them a transfer cannot be traced. Email addresses
are not logged on success; `login_failed` logs a `reason` and a `user_id`
rather than the submitted email.

`test_observability.py` asserts that no plaintext password and no bcrypt hash
(`$2b$`) ever appears in the log file.

---

## How Filebeat turns a line into searchable fields

From `observability/filebeat/filebeat.yml`:

```yaml
filebeat.inputs:
  - type: filestream
    id: wallet-app-logs
    paths:
      - /absolute/path/to/wallet-app/logs/wallet-app.json
    parsers:
      - ndjson:
          target: ""
          add_error_key: true
          overwrite_keys: true
```

- `filestream` tails the file, remembering its position in a registry so a
  Filebeat restart does not re-ship everything.
- `ndjson` decodes each line as one JSON object.
- `target: ""` promotes the decoded keys to the **top level** of the
  Elasticsearch document. Without it everything would nest under a `json.`
  prefix and queries would read `json.event : "transfer_completed"`.
- `overwrite_keys: true` lets the app's `@timestamp` win over Filebeat's own
  ingest timestamp, so Kibana's time axis shows when the event *happened*, not
  when it was shipped.
- `add_error_key: true` tags a malformed line instead of silently dropping it.

**No grok or dissect patterns are needed**, because the app emits structured
JSON rather than plain text. If the app logged `2026-09-18 Transfer completed
amount=100`, a `dissect` or `grok` processor would be required to pull out
each field, and every format change would break it. That is the main practical
argument for structured logging.

Documents land in `wallet-logs-YYYY.MM.dd` (`output.elasticsearch.index`).
A custom index name requires `setup.template.name` and
`setup.template.pattern`, which is why those are set.

---

## Where logs live, what survives a restart, when they are deleted

| Stage | Location | Survives app restart? | Deleted when |
|---|---|---|---|
| App (file) | `logs/wallet-app.json` | Yes, appended, not truncated | Rotated at 10 MB, 5 old files kept (`RotatingFileHandler`), so ~60 MB ceiling; oldest deleted on rotation |
| App (stdout) | terminal / `docker logs` | No | Terminal closes; Docker path caps at `max-size: 10m`, `max-file: 5` |
| Filebeat position | `stack/run/filebeat-data/registry` | Yes | Deleting the registry makes Filebeat re-ship the whole file from the start |
| Elasticsearch | `wallet-logs-YYYY.MM.dd` index, on disk | Yes | **Never automatically.** ILM is disabled (`setup.ilm.enabled: false`) |
| Prometheus (metrics, for comparison) | TSDB | Yes | Automatically after 15 days (`--storage.tsdb.retention.time=15d`) |

The asymmetry is worth calling out in the report: **metrics expire on their
own, logs do not.** With `setup.ilm.enabled: false` the daily indices grow
forever until deleted manually:

```bash
curl -X DELETE "http://localhost:9200/wallet-logs-2026.09.18"
curl -X DELETE "http://localhost:9200/wallet-logs-*"     # all of them
```

For a real deployment an ILM policy would delete indices after N days. It is
off here because ILM adds setup steps that obscure the basic pipeline, and the
assignment's cleanup instructions want deletion to be explicit and visible.

---

## Searching in Kibana

Set up the index pattern once: **Stack Management → Data Views → Create data
view**, name `wallet-logs-*`, time field `@timestamp`. Then use **Discover**.

Queries below are KQL (the default in the Kibana search bar).

### Find everything for one request

This is the main reason `request_id` exists. The API returns it in the
`X-Request-ID` response header, so a user reporting a failure can quote it:

```
request_id : "317a6c8c-11ca-4162-9e7d-bb8f0b9a2ddc"
```

Verified end-to-end in `test_observability.py`: the header value returned to
the client finds its log lines in the file.

### Find errors and warnings

```
level : ("ERROR" or "WARNING")
```

### Why are transfers failing?

```
event : "transfer_failed" or event : "transfer_rejected"
```

Split by cause:

```
event : "transfer_rejected" and reason : "recipient_not_found"
```

### Large transfers (numeric range, works because `amount` is a real number)

```
event : "transfer_completed" and amount >= 1000
```

This only works because `decode_json_fields`/`ndjson` promoted `amount` as a
numeric field. If the whole line were stored as a text `message`, this query
would be impossible, a good concrete illustration of what parsing buys.

### Possible credential stuffing

```
event : "login_failed" and reason : "bad_password"
```

Then set the time range to the last 15 minutes and watch the hit count.

### The Part E experiment window

```
event : ("fault_config_changed" or "fault_reset" or "fault_injected")
```

`fault_config_changed` and `fault_reset` bracket the fault stage exactly, so
they define the time range to use for the before/during/after comparison in
Grafana.

### Everything that happened to one wallet

```
from_wallet_id : "ac554213-6a27-403d-844b-3c199389a87f"
  or to_wallet_id : "ac554213-6a27-403d-844b-3c199389a87f"
```

### Useful Discover setup

Add these as columns so the table is readable:
`@timestamp`, `level`, `event`, `request_id`, `message`, `amount`, `reason`.

Save the searches you use for the report (**Save** in Discover) and export
them via **Stack Management → Saved Objects → Export** so they can be
submitted with the code.

---

## Verifying the pipeline is actually working

Run these in order if logs are not appearing in Kibana:

```bash
# 1. Is the app writing?
tail -f logs/wallet-app.json

# 2. Is every line valid JSON?
python3 -c "import json;[json.loads(l) for l in open('logs/wallet-app.json') if l.strip()];print('all lines valid')"

# 3. Is Elasticsearch up?
curl http://localhost:9200/_cluster/health?pretty

# 4. Did the index get created, and does it have documents?
curl 'http://localhost:9200/_cat/indices/wallet-logs-*?v'

# 5. What does a stored document actually look like?
curl 'http://localhost:9200/wallet-logs-*/_search?size=1&pretty'

# 6. Is Filebeat connected and shipping?
tail -f stack/run/filebeat-logs/filebeat*.ndjson
```

Most common failures, in order of likelihood:

1. **Filebeat's path is still the placeholder.** It does not expand `~` or
   relative paths. `scripts/configure_stack.sh` substitutes the absolute path;
   if you edited the config by hand, check it.
2. **Filebeat refuses to start over config permissions.** It requires the
   config file not be group/world-writable, `chmod go-w filebeat.yml`. The
   configure script does this; `-strict.perms=false` is also passed in the
   generated start script.
3. **Filebeat started before the app ever logged anything.** The file must
   exist. Start the app, make a request, then start Filebeat.
4. **Elasticsearch not ready yet.** It takes 30–60 seconds on first boot and
   Filebeat will retry; watch its log rather than assuming it failed.
