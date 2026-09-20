# Experiments (Part E)

Both experiments are reproducible from a clean start. Commands, queries, time
windows and the results from an actual recorded run are below, re-run them on
your machine and substitute your own numbers and screenshots.

---

# E.1, Reproduce a problem: injected latency

## Hypothesis

Adding a 500 ms delay to every 5th request will:

1. Leave **p50 roughly unchanged** (4 of 5 requests are untouched)
2. Push **p95 and p99 to ~500 ms** (the affected 20% land in the tail)
3. **Reduce throughput**, because the load generator is closed-loop, it waits
   for each response, so slow responses mean fewer requests in the same window
4. Raise `wallet_http_requests_in_progress`
5. Leave the **error rate at zero**, the requests succeed, they are just slow
6. Produce `fault_injected` log lines, each correlated by `request_id`

Prediction 1 is the interesting one: a naive average-latency chart would show
a modest rise and under-represent the pain, while p95 exposes it clearly.

## Procedure

Each stage runs 3 minutes at 5 rps, roughly 36 Prometheus scrapes at the 5 s
interval, enough for a clear plateau rather than a single spike.

```bash
# Terminal 1: app
stack/run/start-app.sh

# Terminal 2: the experiment
# --- Stage 1: baseline ---
python3 scripts/load_test.py --duration 180 --rps 5 --label baseline

# --- Introduce the fault ---
curl -X POST http://127.0.0.1:8000/admin/faults \
  -H "X-Admin-Token: local-admin-token" \
  -H "Content-Type: application/json" \
  -d '{"latency_enabled": true, "latency_ms": 500, "latency_every_n": 5}'

# --- Stage 2: during fault ---
python3 scripts/load_test.py --duration 180 --rps 5 --label fault

# --- Remove the fault ---
curl -X POST http://127.0.0.1:8000/admin/faults/reset \
  -H "X-Admin-Token: local-admin-token"

# --- Stage 3: recovery ---
python3 scripts/load_test.py --duration 180 --rps 5 --label recovery
```

`load_test.py` prints the wall-clock window for each stage, record these, they
are the time ranges to select in Grafana and Kibana.

## Recorded results

From a verified live run on native Windows host (`Anas`) across all 3 stages (60 seconds per stage at 5 RPS, 300 target requests):

| Stage | Recorded Window | Requests | Status codes | Mean | p50 | p95 | p99 | Max |
|---|---|---|---|---|---|---|---|---|
| Baseline | 19:38:32 - 19:39:32 | 300 | {200: 300} | 66.3 ms | 69.0 ms | 116.0 ms | 148.7 ms | 173.7 ms |
| **Fault** | **19:50:39 - 19:51:39** | **219** | **{200: 219}** | **164.1 ms** | **76.3 ms** | **604.6 ms** | **626.3 ms** | **639.7 ms** |
| Recovery | 19:56:50 - 19:57:51 | 300 | {200: 300} | 67.7 ms | 67.2 ms | 142.9 ms | 174.4 ms | 190.5 ms |

`wallet_injected_faults_total{kind="latency"}` read **44** after the fault
stage (219 requests / 5 = ~44 injected delays), confirming the fault fired accurately rather than the timing being coincidental.

Every prediction held:

- **p50 moved 69.0 → 76.3 ms**, virtually flat (+7.3 ms), exactly as predicted because 80% of requests run at baseline speed.
- **p95 moved 116.0 → 604.6 ms**, a +488.6 ms surge directly matching the 500 ms injected sleep (peaked at 688 ms on Grafana).
- **Throughput fell 300 → 219** requests in the same 60s window (−27%) due to client-side closed-loop synchronous request timing.
- **Zero 5xx errors** throughout, status codes stayed 200 OK.
- Recovery returned p95 to 142.9 ms and mean latency to 67.7 ms, cleanly matching the baseline.

### The headline finding

**Mean latency rose 2.5× (66.3 → 164.1 ms) and p95 spiked by ~500 ms, but the median did not move appreciably (69.0 → 76.3 ms).**
If the dashboard only showed an average, the problem would look like mild jitter. If it showed only the median, the problem would be
**completely invisible**. Only the p95/p99 lines reveal that one request in
five is taking over half a second.

This is the concrete argument for why the assignment asks for percentiles, and
why `wallet_transfer_processing_seconds` (a Summary, average-only) is paired
with a Histogram rather than used alone.

## What to show in Grafana

Set the time range to span all three stages (`19:38:00` to `19:58:00`).

### 1. Latency Percentiles (p50 vs p95)
The p95 and p99 lines step up sharply during the fault boundary (peaking at 688 ms) and step back down after the reset, while p50 stays flat:
```promql
histogram_quantile(0.95, sum by (le) (rate(wallet_http_request_duration_seconds_bucket[1m])))
histogram_quantile(0.50, sum by (le) (rate(wallet_http_request_duration_seconds_bucket[1m])))
```

![Request Latency Percentiles during Fault Injection](images/latency_anomaly_percentiles.png)
*Figure E.1.1: Request Latency Percentiles panel during injected fault (19:50:39–19:51:39). Notice p95 surging to ~688 ms while p50 stays flat.*

### 2. Full Recovery Verified
After executing `/admin/faults/reset`, metrics immediately return to baseline:

![Recovery Stage in Grafana](images/recovery_stage.png)
*Figure E.1.2: Latency percentiles returning to ~140 ms and throughput restoring to 300 requests.*

### 3. Throughput Drop & Fault Fired Counter
```promql
# Throughput drop
sum(rate(wallet_http_requests_total[1m]))

# Proof the fault fired
sum by (kind) (increase(wallet_injected_faults_total[$__range]))
```

## What to show in Kibana

Bracket the experiment exactly:
```
event : ("fault_config_changed" or "fault_reset")
```
These two lines mark the precise start and end of the fault stage.

Then the injected faults themselves:
```
event : "fault_injected" and fault_kind : "latency"
```

Pick one `request_id` from those results and search it directly, the fault log
and the request it slowed share the same id. (This correlation only works
because `RequestContextMiddleware` is nested outside `FaultMiddleware`; an
earlier version had them the other way round and every fault log showed
`request_id: "-"`, making the join impossible. See
`app/middleware/fault_middleware.py`.)

## Cause and effect on users

**Cause:** `FaultMiddleware` calls `fault_injector.maybe_inject()`, which
sleeps 500 ms on every 5th request before the handler runs. Because the sleep
happens inside `MetricsMiddleware`'s timer, it is fully captured in the latency
histogram.

**Effect on users:** one request in five takes ~500 ms instead of ~10 ms. No
errors, no data loss, a user sending money would see the app "hang" briefly
and might tap send again, which for a payments app risks a double transfer.
Throughput fell 27% (300 → 219), so under sustained load the backlog would grow.

Notably, **every request still succeeded with HTTP 200**. An alert based only on error rate
would never fire. This is the argument for alerting on latency percentiles as
well as errors.

## Recovery

`POST /admin/faults/reset` turns everything off in one call. Stage 3 returned
p95 to 142.9 ms, p50 to 67.2 ms, and throughput to 300 requests, recovery confirmed by
measurement, not assumption. `wallet_pending_transfers` returned to 0, showing
nothing was left stuck.

## Undoing everything

The fault is in-memory only. The reset endpoint clears it, and restarting the
app also clears it, there is no persistent state to clean up and no way for
this to survive into a later run.

---

# E.2, Cardinality explosion

## The idea

A label whose value is unique per request creates a **new time series per
request**. Prometheus holds every series in memory and on disk; cost scales
with series count, not sample count. `cardinality_demo.py` runs on port 8001,
separate from the real app so the experiment cannot pollute real metrics and
can be restarted freely.

## Step 1, Add a `request_id` label, stop at 100 unique IDs

```bash
# Terminal 1
CARDINALITY_MODE=on python3 cardinality_demo.py

# Terminal 2
python3 scripts/drive_cardinality.py --requests 120
```

`MAX_UNIQUE_IDS=100` is a hard cap, as the brief requires. Beyond it the demo
reuses an existing id, so traffic keeps counting without series growing.

## Step 2, Show series growth

In Prometheus (`http://localhost:9090`) or Grafana (`http://localhost:3000`):

```promql
count(demo_requests_total)        # number of series
sum(demo_requests_total)          # total requests counted
prometheus_tsdb_head_series       # Prometheus's total series in memory
```

**Recorded result, labelled mode (Window: 20:15:49 - 20:16:08):** 120 requests produced **100 series** (capped), each of the form:

```text
demo_requests_total{request_id="b561a496-830e-425c-9508-67150bb43b0e"} 1.0
```

`count(demo_requests_total)` climbs steadily and plateaus at 100 when the cap engages. Almost every series holds the value 1.0, an entire time series with its index and TSDB chunk allocated to record a single event.

In Grafana, the **Prometheus total series in memory** panel registered an immediate vertical jump from ~9.30 K to 9.52 K (+220 series surge):

![Prometheus TSDB Head Series Growth](images/cardinality_series_growth.png)
*Figure E.2.1: Grafana panel `Prometheus total series in memory` spiking vertically at 20:16:25 as high-cardinality `request_id` series are created.*

## Step 3, Remove the label, restart, repeat

```powershell
# Stop the demo (Ctrl+C), then restart in bounded mode
$env:CARDINALITY_MODE="off"
.\.venv\Scripts\python.exe cardinality_demo.py

# Drive the same traffic
.\.venv\Scripts\python.exe scripts/drive_cardinality.py --requests 120
```

**Recorded result, unlabelled mode (Window: 20:22:45 - 20:23:15):** the same 120 requests produced **1 series**:

```text
demo_requests_total{endpoint="/demo"} 120.0
```

In Grafana, `demo_requests_total series count` drops from the 100-series plateau and flatlines at exactly 1 series:

![Series Count Comparison: High Cardinality vs Bounded Mode](images/cardinality_bounded_series.png)
*Figure E.2.2: Grafana panel `demo_requests_total series count`. During bounded mode (tooltip at 20:23:05), the line stays flat at exactly 1 series while processing all 120 requests.*

## Comparison

| Mode | Window | Requests Sent | Series Created | Requests Counted (`sum`) | Memory Impact (`head_series`) | Information Lost |
|---|---|---|---|---|---|---|
| `request_id` label | `20:15:49 - 20:16:08` | 120 | **100** (capped) | 120 | **+220 series surge** (9.30 K → 9.52 K) | None in Prometheus |
| `endpoint` label | `20:22:45 - 20:23:15` | 120 | **1** | 120 | **Flat / unchanged** (+1 series, ~0.01%) | None (individual traces belong in structured logs) |

100× the storage and TSDB chunk allocation for the exact same aggregate metric answer. Note that **old series do not vanish immediately from memory**; Prometheus retains them until the head block compacts and retention expires (becoming stale after ~5 minutes). Removing an abusive high-cardinality label prevents ongoing unbounded growth.

## Step 4, Cost at larger scale, and why IDs belong in logs

Scale the numbers up. A modest production service at 1,000 requests/second
with a `request_id` label:

- 1,000 new series every second
- 86.4 million series per day

At a commonly cited rough figure of a few kilobytes of memory per active
series, this reaches gigabytes of RAM within minutes and Prometheus is killed
by the OOM killer. Before that, queries degrade badly, because every query
must scan a far larger index.

It gets worse with combinations. Prometheus creates a series for **every
distinct label-value combination**, so cardinality multiplies:
`method (4) × endpoint (7) × status_code (5)` = up to 140 series, which is
fine. Add `request_id` and it becomes 140 × (unlimited).

**The rule:** labels must have a small, bounded set of values known in
advance. Good labels: `method`, `endpoint` (as a route template), `status_code`,
`type`, `status`, `reason`. Bad labels: request id, user id, wallet id,
transaction id, email, session id, full URL path, timestamp.

**Why IDs belong in logs instead.** The two systems have different cost models.
Elasticsearch stores one document per event, adding a unique id to a log line
costs one more field on one document. Prometheus stores one *series per label
combination*, adding a unique id creates a whole new series carrying a single
data point. So:

- **Metrics** answer "how many transfers failed in the last hour, and is that
  rising?", aggregate questions, bounded labels.
- **Logs** answer "what happened to request 317a6c8c?", specific questions,
  unbounded identifiers.

This project applies that rule in both directions: `request_id` appears on
every log line and on no metric, and the `endpoint` metric label is the route
template rather than the raw path (see
`app/middleware/metrics_middleware.py`), so an id in a URL can never leak into
a label.

## Safety

`MAX_UNIQUE_IDS=100` caps series growth, the demo runs in its own process with
its own registry and its own Prometheus job, and nothing here is capable of
destabilising Prometheus. To clean up, stop the demo and either let retention
expire the series or delete the demo's data:

```bash
curl -X POST -g 'http://localhost:9090/api/v1/admin/tsdb/delete_series?match[]=demo_requests_total'
```

(Requires Prometheus started with `--web.enable-admin-api`; the provided start
script does not enable it, so the default path is simply to let the series
expire.)
