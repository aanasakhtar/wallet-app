"""
Builds the Grafana dashboard JSON.

Written as a generator rather than hand-authored JSON so the panel list stays
readable and the output is guaranteed to be syntactically valid. Re-run with:

    python3 scripts/build_dashboard.py

Output: observability/grafana/dashboards/wallet-app.json
"""
import json
import os

DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}


def target(expr, legend, instant=False):
    return {
        "datasource": DS,
        "expr": expr,
        "legendFormat": legend,
        "refId": "A",
        "instant": instant,
        "range": not instant,
    }


def timeseries(title, x, y, w, h, targets, unit="short", desc="", stack=False):
    return {
        "type": "timeseries",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 2,
                    "fillOpacity": 10,
                    "showPoints": "never",
                    "stacking": {"mode": "normal" if stack else "none"},
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [dict(t, refId=chr(65 + i)) for i, t in enumerate(targets)],
    }


def stat(title, x, y, w, h, targets, unit="short", desc="", decimals=None):
    return {
        "type": "stat",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": decimals,
                "color": {"mode": "thresholds"},
                "thresholds": {"mode": "absolute", "steps": [{"color": "blue", "value": None}]},
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
            "colorMode": "value",
            "graphMode": "area",
        },
        "targets": [dict(t, refId=chr(65 + i)) for i, t in enumerate(targets)],
    }


def heatmap(title, x, y, w, h, targets, desc=""):
    return {
        "type": "heatmap",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "calculate": False,
            "cellGap": 1,
            "color": {"mode": "scheme", "scheme": "Oranges", "steps": 64},
            "yAxis": {"unit": "s"},
        },
        "targets": [dict(t, refId=chr(65 + i), format="heatmap") for i, t in enumerate(targets)],
    }


def row(title, y):
    return {
        "type": "row",
        "title": title,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "collapsed": False,
        "panels": [],
    }


panels = []

# ---------------------------------------------------------------------------
# Row 1: Golden signals (application metrics)
# ---------------------------------------------------------------------------
panels.append(row("Application health", 0))

panels.append(
    timeseries(
        "Request rate by endpoint",
        0, 1, 12, 8,
        [target('sum by (endpoint) (rate(wallet_http_requests_total[1m]))', "{{endpoint}}")],
        unit="reqps",
        desc="COUNTER. rate() converts the ever-increasing counter into "
             "requests per second averaged over a 1-minute window.",
    )
)

panels.append(
    timeseries(
        "Error rate (4xx / 5xx)",
        12, 1, 12, 8,
        [
            target('sum(rate(wallet_http_requests_total{status_code=~"5.."}[1m]))', "5xx"),
            target('sum(rate(wallet_http_requests_total{status_code=~"4.."}[1m]))', "4xx"),
        ],
        unit="reqps",
        desc="COUNTER with a label filter. 5xx spikes during the injected-error "
             "stage of the Part E experiment.",
    )
)

panels.append(
    timeseries(
        "Request latency percentiles",
        0, 9, 12, 8,
        [
            target(
                'histogram_quantile(0.50, sum by (le) '
                '(rate(wallet_http_request_duration_seconds_bucket[5m])))', "p50"),
            target(
                'histogram_quantile(0.95, sum by (le) '
                '(rate(wallet_http_request_duration_seconds_bucket[5m])))', "p95"),
            target(
                'histogram_quantile(0.99, sum by (le) '
                '(rate(wallet_http_request_duration_seconds_bucket[5m])))', "p99"),
        ],
        unit="s",
        desc="HISTOGRAM. histogram_quantile() interpolates within buckets. "
             "The [5m] window means each point summarises the previous 5 minutes, "
             "so the line lags a fault by up to 5 minutes and smooths it out.",
    )
)

panels.append(
    heatmap(
        "Latency distribution (heatmap)",
        12, 9, 12, 8,
        [target('sum by (le) (rate(wallet_http_request_duration_seconds_bucket[1m]))', "{{le}}")],
        desc="HISTOGRAM buckets drawn directly. During the 500ms fault a second "
             "band appears in the upper buckets - clearer than a percentile line.",
    )
)

panels.append(
    stat(
        "Avg transfer processing time",
        0, 17, 6, 5,
        [target('rate(wallet_transfer_processing_seconds_sum[5m]) / '
                'rate(wallet_transfer_processing_seconds_count[5m])', "avg")],
        unit="s", decimals=4,
        desc="SUMMARY. The Python client's Summary exposes only _sum and _count, "
             "so an average is all it can give - hence the histogram above for p95/p99.",
    )
)

panels.append(
    stat(
        "In-flight requests",
        6, 17, 6, 5,
        [target("wallet_http_requests_in_progress", "in flight")],
        desc="GAUGE. Goes up and down; climbs while requests are slowed by the fault.",
    )
)

panels.append(
    stat(
        "Injected faults (experiment only)",
        12, 17, 6, 5,
        [target('sum by (kind) (increase(wallet_injected_faults_total[$__range]))', "{{kind}}")],
        desc="COUNTER. Proves the fault actually fired, rather than relying on the clock.",
    )
)

panels.append(
    stat(
        "Uptime",
        18, 17, 6, 5,
        [target('time() - process_start_time_seconds{job="wallet-app"}', "uptime")],
        unit="s",
        desc="Resets to zero on restart - useful when checking recovery.",
    )
)

# ---------------------------------------------------------------------------
# Row 2: Business metrics
# ---------------------------------------------------------------------------
panels.append(row("Business metrics", 22))

panels.append(
    timeseries(
        "Transactions per minute by type",
        0, 23, 12, 8,
        [target('sum by (type) (rate(wallet_transactions_total{status="completed"}[1m])) * 60',
                "{{type}}")],
        unit="short",
        desc="COUNTER. The core business signal: is money still moving? "
             "A drop here matters even when the error rate looks fine.",
        stack=True,
    )
)

panels.append(
    timeseries(
        "Failed vs completed transfers",
        12, 23, 12, 8,
        [
            target('sum(rate(wallet_transactions_total{type="transfer",status="completed"}[1m]))',
                   "completed"),
            target('sum(rate(wallet_transactions_total{type="transfer",status="failed"}[1m]))',
                   "failed"),
        ],
        unit="short",
        desc="COUNTER split by status label - one metric answering two questions.",
    )
)

panels.append(
    stat(
        "Money in system (PKR)",
        0, 31, 6, 5,
        [target("max(wallet_money_in_system_pkr)", "total")],
        unit="currencyPKR", decimals=2,
        desc="GAUGE. Sum of all wallet balances. Transfers conserve it; "
             "only top-ups and cashback move it. A sudden change is a red flag.",
    )
)

panels.append(
    stat(
        "Registered users",
        6, 31, 6, 5,
        [target("max(wallet_registered_users)", "users")],
        desc="GAUGE, though it only grows here. Modelled as a gauge because it "
             "describes current state, not an accumulating event count.",
    )
)

panels.append(
    stat(
        "Pending transfers",
        12, 31, 6, 5,
        [target("sum(wallet_pending_transfers)", "in flight")],
        desc="GAUGE. Incremented on entry to the transfer service, decremented in "
             "finally. If it drifts upward and never returns to 0, transfers are stuck.",
    )
)

panels.append(
    stat(
        "Transfer p95 amount (PKR)",
        18, 31, 6, 5,
        [target('histogram_quantile(0.95, sum by (le) '
                '(rate(wallet_transfer_amount_pkr_bucket[$__range])))', "p95")],
        unit="currencyPKR", decimals=0,
        desc="HISTOGRAM on a business quantity rather than latency. "
             "95% of transfers are at or below this amount.",
    )
)

panels.append(
    timeseries(
        "Transfer rejections by reason (self-explored)",
        0, 36, 12, 8,
        [target('sum by (reason) (rate(wallet_transfer_rejections_total[5m]) * 300)',
                "{{reason}}")],
        desc="COUNTER with a reason label. This is the metric explored beyond the "
             "class examples: it separates 'users are broke' from 'users mistyped "
             "a recipient', which are very different product problems.",
    )
)

panels.append(
    timeseries(
        "Auth failures by reason (self-explored)",
        12, 36, 12, 8,
        [target('sum by (reason) (rate(wallet_auth_failures_total[5m]) * 300)', "{{reason}}")],
        desc="COUNTER. A spike in bad_password from many users can indicate a "
             "credential-stuffing attempt; unknown_email suggests a broken signup flow.",
    )
)

# ---------------------------------------------------------------------------
# Row 3: Host metrics (Node Exporter)
# ---------------------------------------------------------------------------
panels.append(row("Host metrics (Node Exporter)", 44))

panels.append(
    timeseries(
        "CPU usage %",
        0, 45, 12, 7,
        [target('100 - (avg by (instance) '
                '(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)', "{{instance}}")],
        unit="percent",
        desc="Derived from the idle counter: everything not idle is in use.",
    )
)

panels.append(
    timeseries(
        "Memory used %",
        12, 45, 12, 7,
        [target('100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))',
                "{{instance}}")],
        unit="percent",
        desc="MemAvailable is the honest number - it accounts for reclaimable cache.",
    )
)

panels.append(
    timeseries(
        "Disk usage % (root filesystem)",
        0, 52, 8, 7,
        [target('100 - (node_filesystem_avail_bytes{mountpoint="/",fstype!="tmpfs"} / '
                'node_filesystem_size_bytes{mountpoint="/",fstype!="tmpfs"} * 100)',
                "{{instance}} {{mountpoint}}")],
        unit="percent",
        desc="Worth watching during this assignment specifically: Elasticsearch "
             "indices and Prometheus TSDB both grow on disk.",
    )
)

panels.append(
    timeseries(
        "Network traffic",
        8, 52, 8, 7,
        [
            target('sum by (instance) (rate(node_network_receive_bytes_total'
                   '{device!~"lo|veth.*|docker.*"}[1m]))', "received"),
            target('sum by (instance) (rate(node_network_transmit_bytes_total'
                   '{device!~"lo|veth.*|docker.*"}[1m]))', "transmitted"),
        ],
        unit="Bps",
        desc="Loopback and virtual interfaces excluded so local traffic "
             "between the stack's own components doesn't dominate.",
    )
)

panels.append(
    timeseries(
        "Load average",
        16, 52, 8, 7,
        [
            target("node_load1", "1m"),
            target("node_load5", "5m"),
            target("node_load15", "15m"),
        ],
        desc="Compare against CPU core count; sustained load above core count "
             "means work is queuing.",
    )
)

# ---------------------------------------------------------------------------
# Row 4: Cardinality experiment
# ---------------------------------------------------------------------------
panels.append(row("Cardinality experiment (Part E.2)", 59))

panels.append(
    timeseries(
        "demo_requests_total series count",
        0, 60, 12, 7,
        [target("count(demo_requests_total)", "series")],
        desc="THE key panel for Part E.2. With a request_id label this climbs "
             "toward 100 (one series per id). Without it, the line stays flat at 1 "
             "no matter how much traffic is sent.",
    )
)

panels.append(
    timeseries(
        "Prometheus total series in memory",
        12, 60, 12, 7,
        [target("prometheus_tsdb_head_series", "head series")],
        desc="Prometheus's own view of how many series it holds. Shows the real "
             "cost of the label: memory is consumed per series, not per sample.",
    )
)

# ---------------------------------------------------------------------------
# Row 5: Assignment 2 Part C - the SAME operation (send money) over HTTP and over gRPC.
# Left column = HTTP POST /transfers, right column = gRPC CreateTransfer.
# Every rate uses a 1 m window; ratios and percentiles use a 5 m window (stated in each panel).
# ---------------------------------------------------------------------------
HT = 'method="POST",endpoint="/transfers"'
panels.append(row("Part C - send money: HTTP (left) vs gRPC (right)", 67))

panels.append(timeseries(
    "HTTP POST /transfers - calls/s by status code", 0, 68, 12, 8,
    [target(f'sum by (status_code) (rate(wallet_http_requests_total{{{HT}}}[1m]))', "{{status_code}}")],
    unit="reqps", stack=True,
    desc="COUNTER from MetricsMiddleware. 201 = created (incl. idempotent replays), 404/409/422 = business "
         "or validation rejections, 429 = rate limited."))
panels.append(timeseries(
    "gRPC CreateTransfer - calls/s by method and status", 12, 68, 12, 8,
    [target('sum by (grpc_method, grpc_code) (rate(wallet_grpc_server_handled_total[1m]))',
            "{{grpc_method}} {{grpc_code}}")],
    unit="reqps", stack=True,
    desc="COUNTER from the gRPC interceptor. OK = success, FAILED_PRECONDITION = insufficient balance, "
         "NOT_FOUND = unknown recipient, INVALID_ARGUMENT = validation, UNAUTHENTICATED = bad token, "
         "CANCELLED = the client gave up (deadline) before the server answered."))

panels.append(stat(
    "HTTP: non-2xx share of calls (5m)", 0, 76, 6, 5,
    [target(f'(sum(rate(wallet_http_requests_total{{{HT},status_code!~"2.."}}[5m])) or vector(0)) / '
            f'sum(rate(wallet_http_requests_total{{{HT}}}[5m]))', "non-2xx")],
    unit="percentunit", decimals=1,
    desc="Everything that is not 2xx, including expected business rejections (409/404/422)."))
panels.append(stat(
    "HTTP: server errors (5xx) share (5m)", 6, 76, 6, 5,
    [target(f'(sum(rate(wallet_http_requests_total{{{HT},status_code=~"5.."}}[5m])) or vector(0)) / '
            f'sum(rate(wallet_http_requests_total{{{HT}}}[5m]))', "5xx")],
    unit="percentunit", decimals=1,
    desc="The 'our fault' error rate. Business rejections are NOT counted here."))
panels.append(stat(
    "gRPC: non-OK share of calls (5m)", 12, 76, 6, 5,
    [target('(sum(rate(wallet_grpc_server_handled_total{grpc_code!="OK"}[5m])) or vector(0)) / '
            'sum(rate(wallet_grpc_server_handled_total[5m]))', "non-OK")],
    unit="percentunit", decimals=1,
    desc="Counterpart of the HTTP non-2xx share (OK <-> 2xx)."))
panels.append(stat(
    "gRPC: server errors share (5m)", 18, 76, 6, 5,
    [target('(sum(rate(wallet_grpc_server_handled_total{grpc_code=~"INTERNAL|UNKNOWN|UNAVAILABLE|DATA_LOSS"}[5m])) or vector(0)) / '
            'sum(rate(wallet_grpc_server_handled_total[5m]))', "server errors")],
    unit="percentunit", decimals=1,
    desc="Counterpart of 5xx: INTERNAL, UNKNOWN, UNAVAILABLE, DATA_LOSS."))

panels.append(timeseries(
    "HTTP POST /transfers - latency p50 / p95 (5m window)", 0, 81, 12, 8,
    [target(f'histogram_quantile(0.50, sum by (le) (rate(wallet_http_request_duration_seconds_bucket{{{HT}}}[5m])))', "p50"),
     target(f'histogram_quantile(0.95, sum by (le) (rate(wallet_http_request_duration_seconds_bucket{{{HT}}}[5m])))', "p95")],
    unit="s",
    desc="HISTOGRAM. Each point = quantile of the previous 5 minutes. Measured SERVER-side in the HTTP "
         "middleware (routing + validation + handler + response)."))
panels.append(timeseries(
    "gRPC CreateTransfer - latency p50 / p95 (5m window)", 12, 81, 12, 8,
    [target('histogram_quantile(0.50, sum by (le) (rate(wallet_grpc_server_handling_seconds_bucket[5m])))', "p50"),
     target('histogram_quantile(0.95, sum by (le) (rate(wallet_grpc_server_handling_seconds_bucket[5m])))', "p95")],
    unit="s",
    desc="HISTOGRAM. Same 5-minute window and same bucket edges as the HTTP panel so the two are comparable. "
         "Measured SERVER-side around the handler only (excludes network and protobuf (de)serialisation)."))

panels.append(timeseries(
    "HTTP requests in progress (all endpoints)", 0, 89, 12, 6,
    [target("sum(wallet_http_requests_in_progress)", "in flight")],
    desc="GAUGE. Process-wide (not per endpoint), so it is the closest HTTP counterpart of the gRPC gauge."))
panels.append(timeseries(
    "gRPC in-flight calls", 12, 89, 12, 6,
    [target("sum by (grpc_method) (wallet_grpc_server_in_flight)", "{{grpc_method}}")],
    desc="GAUGE chosen over message size: the server has a fixed pool of 10 worker threads, so in-flight "
         "calls show saturation directly; message sizes here are tiny and constant, which would tell us nothing."))

panels.append(timeseries(
    "Completed transfers/s by front end (job)", 0, 95, 8, 7,
    [target('sum by (job) (rate(wallet_transactions_total{type="transfer",status="completed"}[1m]))', "{{job}}")],
    unit="reqps",
    desc="The shared service layer records this counter in BOTH processes; the job label tells them apart: "
         "wallet-app = HTTP, wallet-grpc = gRPC."))
panels.append(stat(
    "Idempotent replays / rate-limit rejections", 8, 95, 8, 7,
    [target("sum(increase(wallet_idempotent_replays_total[$__range])) or vector(0)", "replays"),
     target("sum(increase(wallet_rate_limit_rejections_total[$__range])) or vector(0)", "429s")],
    decimals=0,
    desc="COUNTERS added in Assignment 2: duplicate transfer requests answered from the stored result, and "
         "requests rejected by the rate limiter, over the selected dashboard time range. `or vector(0)` shows 0 "
         "instead of 'No data' when nothing happened. Note increase() extrapolates to the window edges, so it can "
         "read slightly above the true integer count."))
panels.append(stat(
    "Scrape health (up)", 16, 95, 8, 7,
    [target('up{job=~"wallet-app|wallet-grpc"}', "{{job}}")],
    decimals=0,
    desc="1 = Prometheus scraped the target successfully on its last attempt. Confirms the gRPC service is scraped."))

dashboard = {
    "__inputs": [
        {
            "name": "DS_PROMETHEUS",
            "label": "Prometheus",
            "description": "Prometheus data source backing every panel",
            "type": "datasource",
            "pluginId": "prometheus",
            "pluginName": "Prometheus",
        }
    ],
    "__requires": [
        {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "10.0.0"},
        {"type": "datasource", "id": "prometheus", "name": "Prometheus", "version": "1.0.0"},
    ],
    "title": "Wallet App - Observability",
    "description": "Application, business, host and cardinality-experiment metrics "
                   "for the wallet API. Built for Assignment 1: Observability.",
    "tags": ["wallet", "observability", "assignment"],
    "timezone": "browser",
    "editable": True,
    "schemaVersion": 39,
    "version": 1,
    "refresh": "5s",
    "time": {"from": "now-30m", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
    "annotations": {
        "list": [
            {
                "builtIn": 1,
                "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True,
                "hide": True,
                "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts",
                "type": "dashboard",
            }
        ]
    },
    "templating": {"list": []},
    "panels": [dict(p, id=i + 1) for i, p in enumerate(panels)],
}

out_dir = os.path.join("observability", "grafana", "dashboards")
os.makedirs(out_dir, exist_ok=True)

# Two variants are written, because Grafana resolves data sources differently
# depending on how a dashboard arrives:
#
#   wallet-app.json          uses ${DS_PROMETHEUS}, an __inputs placeholder.
#                            This is the form for manual import via the UI
#                            (Dashboards > Import), where Grafana prompts you
#                            to pick a data source.
#
#   wallet-app-provisioned.json  hardcodes uid "prometheus-main" to match
#                            provisioning/datasources/datasource.yml. File-based
#                            provisioning never prompts, so a placeholder would
#                            leave every panel showing "Datasource not found".
out_path = os.path.join(out_dir, "wallet-app.json")
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(dashboard, fh, indent=2)
    fh.write("\n")

provisioned = json.loads(json.dumps(dashboard).replace("${DS_PROMETHEUS}", "prometheus-main"))
provisioned.pop("__inputs", None)
provisioned["title"] = "Wallet App - Observability"
provisioned["uid"] = "wallet-app-obs"
prov_path = os.path.join(out_dir, "wallet-app-provisioned.json")
with open(prov_path, "w", encoding="utf-8") as fh:
    json.dump(provisioned, fh, indent=2)
    fh.write("\n")

non_row = [p for p in panels if p["type"] != "row"]
print(f"Wrote {out_path} (manual import)")
print(f"Wrote {prov_path} (file provisioning)")
print(f"  {len(non_row)} panels across {len([p for p in panels if p['type'] == 'row'])} rows")
