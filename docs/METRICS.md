# Metrics reference (Part B)

Every metric the wallet app exposes, where it is recorded, and the Grafana
query that displays it. All are defined in `app/metrics.py`; the "recorded in"
column says where the value is actually set.

Scrape config: `observability/prometheus/prometheus.yml`, `scrape_interval: 5s`.

---

## Application metrics

| Metric | Type | Unit | Labels | Recorded in | Purpose |
|---|---|---|---|---|---|
| `wallet_http_requests_total` | Counter | requests | `method`, `endpoint`, `status_code` | `app/middleware/metrics_middleware.py`, in `dispatch()` `finally` | Traffic volume and error rate for every route |
| `wallet_http_request_duration_seconds` | Histogram | seconds | `method`, `endpoint` | same, `.observe(elapsed)` | Latency distribution; the source of p95/p99 |
| `wallet_http_requests_in_progress` | Gauge | requests | none | same, `.inc()` on entry / `.dec()` in `finally` | Concurrency right now; rises when requests back up |

**Why `endpoint` is the route template, not the URL.** The middleware reads
`request.scope["route"].path`, so a request to `/wallet/balance` records
`endpoint="/wallet/balance"`. If a route ever became `/wallets/{wallet_id}`,
the label would still be the template, one series, not one per wallet. Paths
that match no route collapse to `endpoint="unmatched"`. This is the same
principle the Part E.2 cardinality experiment demonstrates, applied
preemptively to the real app.

### Grafana queries

**Request rate by endpoint**
```promql
sum by (endpoint) (rate(wallet_http_requests_total[1m]))
```
`rate()` turns the monotonically increasing counter into per-second throughput
averaged over 1 minute. One line per route. A line dropping to zero means that
route stopped being called; all lines dropping means traffic stopped.

**Error rate**
```promql
sum(rate(wallet_http_requests_total{status_code=~"5.."}[1m]))
sum(rate(wallet_http_requests_total{status_code=~"4.."}[1m]))
```
The same counter filtered by label. 4xx is normally non-zero here (rejected
transfers are 400s and that is correct behaviour); 5xx should sit at zero
outside the experiment.

**Latency percentiles**
```promql
histogram_quantile(0.95, sum by (le) (rate(wallet_http_request_duration_seconds_bucket[5m])))
```
Swap `0.95` for `0.50` / `0.99`. `sum by (le)` merges the per-endpoint bucket
series before interpolating, giving one number for the whole service.

**Time window.** The `[5m]` means each plotted point summarises the preceding
5 minutes. Consequences worth stating in the report: a fault takes up to
5 minutes to fully show in the line, and it lingers for 5 minutes after being
removed. Accuracy is bounded by the bucket edges in `app/metrics.py`
(`0.005 … 5.0`); `histogram_quantile` interpolates linearly inside whichever
bucket the quantile falls in, so with a 500 ms fault landing between the
`0.5` and `0.75` boundaries the reported p95 is approximate, not exact.

---

## Business metrics

| Metric | Type | Unit | Labels | Recorded in | Purpose |
|---|---|---|---|---|---|
| `wallet_transactions_total` | Counter | transactions | `type`, `status` | `services/wallet_service.py` (topup), `services/transfer_service.py` (transfer, cashback) | Is money actually moving? |
| `wallet_transfer_amount_pkr` | Histogram | PKR | none | `transfer_service.execute_transfer`, on success | Distribution of transfer sizes |
| `wallet_transfer_processing_seconds` | Summary | seconds | none | `transfer_service.execute_transfer`, in `finally` | Average time inside the transfer logic, excluding HTTP overhead |
| `wallet_pending_transfers` | Gauge | transfers | none | `transfer_service`, `.inc()` on entry / `.dec()` in `finally` | Transfers in flight; should return to 0 |
| `wallet_money_in_system_pkr` | Gauge | PKR | none | `metrics.refresh_business_gauges()` | Sum of all balances |
| `wallet_registered_users` | Gauge | users | none | `metrics.refresh_business_gauges()` | User count |

### On the Summary vs the Histogram

`wallet_transfer_processing_seconds` is a **Summary**, and the Python client
implements Summary without quantiles, it exposes only `_sum` and `_count`.
So it can answer "what is the average?" and nothing more:

```promql
rate(wallet_transfer_processing_seconds_sum[5m])
  / rate(wallet_transfer_processing_seconds_count[5m])
```

For p95/p99 the **Histogram** is used instead
(`wallet_http_request_duration_seconds`, and `wallet_transfer_amount_pkr` for
the business quantity). This is exactly the split the assignment describes.

### Gauge semantics

`wallet_money_in_system_pkr` and `wallet_registered_users` are recomputed from
the database by `refresh_business_gauges()` after any balance- or user-changing
operation, and again on startup (`prime_gauges` in `app/main.py`). The startup
call matters: without it both gauges would read 0 after a restart until the
next write, which would look like every user's money had vanished.

A property worth showing in the report: **transfers conserve
`wallet_money_in_system_pkr`.** Only top-ups (and cashback, if enabled) change
it. In the recorded experiment run, 170 completed transfers left the total at
exactly the amount topped up. A sudden move in this gauge with no matching
top-up counter increase would indicate a genuine accounting bug.

`wallet_pending_transfers` is decremented in a `finally` block, so it returns
to zero even when a transfer is rejected. A value that climbs and never comes
back down means transfers are hanging.

### Grafana queries

**Transactions per minute by type** (stacked)
```promql
sum by (type) (rate(wallet_transactions_total{status="completed"}[1m])) * 60
```

**Failed vs completed transfers**
```promql
sum(rate(wallet_transactions_total{type="transfer",status="completed"}[1m]))
sum(rate(wallet_transactions_total{type="transfer",status="failed"}[1m]))
```

**Transfer p95 amount**
```promql
histogram_quantile(0.95, sum by (le) (rate(wallet_transfer_amount_pkr_bucket[$__range])))
```
Reads as: 95% of transfers are at or below this amount. `$__range` is a
Grafana macro for the dashboard's selected time range.

---

## Self-explored metrics

The assignment asks for at least one metric explored beyond the class
examples. These two are included because they answer product questions that
the standard request/error/duration set cannot.

| Metric | Type | Labels | Recorded in |
|---|---|---|---|
| `wallet_auth_failures_total` | Counter | `reason` (`bad_password`, `unknown_email`, `duplicate_signup`) | `services/auth_service.py` |
| `wallet_transfer_rejections_total` | Counter | `reason` (`insufficient_balance`, `recipient_not_found`, `self_transfer`) | `services/transfer_service.py` |

```promql
sum by (reason) (rate(wallet_transfer_rejections_total[5m]) * 300)
sum by (reason) (rate(wallet_auth_failures_total[5m]) * 300)
```

**Why these are useful.** A plain error-rate chart shows "400s are up" and
stops there. Splitting by reason separates causes that need completely
different responses:

- `insufficient_balance` rising → users want to send more than they hold;
  a product signal (maybe top-up flow friction), not an incident.
- `recipient_not_found` rising → possibly a broken contact picker, or users
  mistyping; an engineering signal.
- `bad_password` rising sharply across many accounts → possible
  credential-stuffing attempt; a security signal.
- `unknown_email` rising → signup flow may be failing silently.

The label sets are deliberately small and fixed (3 values each), so they add
a handful of series, not thousands.

---

## Experiment metric

| Metric | Type | Labels | Recorded in |
|---|---|---|---|
| `wallet_injected_faults_total` | Counter | `kind` (`latency`, `error`) | `app/faults.py`, in `maybe_inject()` |

```promql
sum by (kind) (increase(wallet_injected_faults_total[$__range]))
```

This exists so the Part E write-up can **prove** the fault fired and count how
many times, rather than inferring it from wall-clock timing. In the recorded
run it read exactly 16 over the fault window.

---

## Host metrics (Windows / Node Exporter)

Host metrics are gathered on port 9100 and scraped by the `node-exporter` job in `prometheus.yml`. 
- On native Windows host (`Anas`), **`windows_exporter`** is used to collect Windows OS performance counters.
- On Linux and Docker environments, **`node_exporter`** is used to collect POSIX `/proc` metrics.

| Panel | Windows Exporter Query (Measured on `Anas`) | Node Exporter Query (Linux / Docker) |
|---|---|---|
| CPU usage % | `100 - (avg by (instance) (rate(windows_cpu_time_total{mode="idle"}[1m])) * 100)` | `100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)` |
| Memory used % | `100 * (1 - (windows_os_physical_memory_free_bytes / windows_cs_physical_memory_bytes))` | `100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))` |
| Disk usage % | `100 - (windows_logical_disk_free_bytes{volume="C:"} / windows_logical_disk_size_bytes{volume="C:"} * 100)` | `100 - (node_filesystem_avail_bytes{mountpoint="/",fstype!="tmpfs"} / node_filesystem_size_bytes{mountpoint="/",fstype!="tmpfs"} * 100)` |
| Network | `rate(windows_net_bytes_received_total[1m])` | `sum by (instance) (rate(node_network_receive_bytes_total{device!~"lo|veth.*|docker.*"}[1m]))` |

Notes:

- CPU is derived from the **idle** counter, Node Exporter reports seconds
  spent in each mode, so "busy" is whatever is left over after idle.
- `MemAvailable` is used rather than `MemFree` because free memory looks
  alarmingly low on a healthy Linux box (the kernel uses spare RAM for cache);
  `MemAvailable` accounts for what is reclaimable.
- Loopback and virtual interfaces are excluded from network so that traffic
  between the stack's own components doesn't dominate the chart.
- Disk is worth watching during this assignment specifically: Prometheus TSDB
  and Elasticsearch indices both grow steadily on disk.
- **macOS caveat:** the darwin build of node_exporter exposes far fewer
  metrics than the linux build, `node_cpu_seconds_total` has no per-mode
  breakdown. Some host panels will be empty. Say so in the report rather than
  leaving it unexplained.

---

## Metric type coverage summary

The assignment requires all four types across the project (not four per
category). Coverage:

| Type | Application | Business |
|---|---|---|
| Counter | `wallet_http_requests_total` | `wallet_transactions_total`, `wallet_auth_failures_total`, `wallet_transfer_rejections_total` |
| Gauge | `wallet_http_requests_in_progress` | `wallet_pending_transfers`, `wallet_money_in_system_pkr`, `wallet_registered_users` |
| Histogram | `wallet_http_request_duration_seconds` | `wallet_transfer_amount_pkr` |
| Summary | - | `wallet_transfer_processing_seconds` |
