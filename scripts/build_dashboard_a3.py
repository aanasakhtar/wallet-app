"""
Assignment 3: Grafana Dashboard Generator.

Builds the load-balancing and multi-replica dashboard covering:
  1. Request Rate per Replica (ops/sec for A, B, C)
  2. Traffic Share (% distribution across replicas)
  3. Active In-Flight Requests per Replica
  4. Latency p50 per Replica
  5. Latency p95 per Replica
  6. HTTP Status Breakdown per Replica (2xx, 4xx, 5xx)
  7. Envoy Cluster Upstream Request Rate & Retries
  8. Envoy Cluster Active & Healthy Memberships

Output:
  - observability/grafana/dashboards/wallet-app-a3.json (UI manual import)
  - observability/grafana/dashboards/wallet-app-a3-provisioned.json (auto-provisioned)
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
                    "fillOpacity": 15 if stack else 10,
                    "showPoints": "never",
                    "stacking": {"mode": "normal" if stack else "none"},
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "lastNotNull", "max"]},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [dict(t, refId=chr(65 + i)) for i, t in enumerate(targets)],
    }


def row(title, y):
    return {
        "type": "row",
        "title": title,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "collapsed": False,
    }


panels = [
    # -------------------------------------------------------------------------
    # ROW 1: Per-Replica Traffic & Load Distribution
    # -------------------------------------------------------------------------
    row("Replica Load Balancing & Traffic Distribution", 0),

    timeseries(
        "Request Rate per Replica (ops/sec)",
        0, 1, 12, 8,
        [
            target('sum by (replica) (rate(http_requests_total[1m]))', "HTTP Replica {{replica}}"),
            target('sum by (replica) (rate(grpc_server_handled_total[1m]))', "gRPC Replica {{replica}}"),
        ],
        unit="reqps",
        desc="Number of HTTP and gRPC requests handled per second by each backend replica.",
    ),

    timeseries(
        "Traffic Share (% of Total Traffic)",
        12, 1, 12, 8,
        [
            target(
                'sum by (replica) (rate(http_requests_total[1m])) / ignoring(replica) group_left sum(rate(http_requests_total[1m])) * 100',
                "Replica {{replica}} Share %",
            ),
        ],
        unit="percent",
        desc="Percentage share of incoming traffic served by each replica (expected ~33% in round-robin, ~50%/25%/25% in weighted).",
    ),

    # -------------------------------------------------------------------------
    # ROW 2: Active Concurrency & Latency
    # -------------------------------------------------------------------------
    row("Active Concurrency & Latency Profile", 9),

    timeseries(
        "Active Concurrent Requests per Replica",
        0, 10, 8, 8,
        [
            target('sum by (replica) (http_requests_in_progress)', "Replica {{replica}} Active In-Flight"),
        ],
        unit="short",
        desc="Number of simultaneous requests actively being processed on each replica.",
    ),

    timeseries(
        "Latency p50 per Replica (Median)",
        8, 10, 8, 8,
        [
            target(
                'histogram_quantile(0.50, sum by (replica, le) (rate(http_request_duration_seconds_bucket[1m]))) * 1000',
                "Replica {{replica}} p50",
            ),
        ],
        unit="ms",
        desc="Median response time in milliseconds per replica.",
    ),

    timeseries(
        "Latency p95 per Replica (Tail)",
        16, 10, 8, 8,
        [
            target(
                'histogram_quantile(0.95, sum by (replica, le) (rate(http_request_duration_seconds_bucket[1m]))) * 1000',
                "Replica {{replica}} p95",
            ),
        ],
        unit="ms",
        desc="95th-percentile response time in milliseconds per replica.",
    ),

    # -------------------------------------------------------------------------
    # ROW 3: Error Breakdown & Envoy Reverse Proxy Telemetry
    # -------------------------------------------------------------------------
    row("Status Breakdown & Envoy Reverse Proxy", 18),

    timeseries(
        "Errors & Responses by HTTP Status per Replica",
        0, 19, 12, 8,
        [
            target(
                'sum by (replica, status_code) (rate(http_requests_total{status_code=~"[245].."}[1m]))',
                "Replica {{replica}} [{{status_code}}]",
            ),
        ],
        unit="reqps",
        desc="Throughput breakdown by HTTP response status code per replica.",
        stack=True,
    ),

    timeseries(
        "Envoy Cluster Upstream Requests & Health",
        12, 19, 12, 8,
        [
            target('sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_total[1m]))', "Cluster {{envoy_cluster_name}} Rate"),
            target('sum by (envoy_cluster_name) (envoy_cluster_membership_healthy)', "Cluster {{envoy_cluster_name}} Healthy Hosts"),
        ],
        unit="short",
        desc="Envoy proxy L7 metrics: upstream request rate and healthy host counts per cluster.",
    ),
]

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
    "title": "Wallet App - Load Balancing & Multi-Replica (Assignment 3)",
    "description": "Per-replica load distribution, traffic share, active requests, latency percentiles, and Envoy proxy observability.",
    "tags": ["wallet", "assignment-3", "load-balancing", "envoy"],
    "timezone": "browser",
    "editable": True,
    "schemaVersion": 39,
    "version": 1,
    "refresh": "5s",
    "time": {"from": "now-15m", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
    "templating": {"list": []},
    "panels": [dict(p, id=i + 1) for i, p in enumerate(panels)],
}

out_dir = os.path.join("observability", "grafana", "dashboards")
os.makedirs(out_dir, exist_ok=True)

# 1. Manual import version
out_path = os.path.join(out_dir, "wallet-app-a3.json")
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(dashboard, fh, indent=2)
    fh.write("\n")

# 2. Provisioned version
provisioned = json.loads(json.dumps(dashboard).replace("${DS_PROMETHEUS}", "prometheus-main"))
provisioned.pop("__inputs", None)
provisioned["title"] = "Wallet App - Load Balancing & Multi-Replica (Assignment 3)"
provisioned["uid"] = "wallet-app-a3"
prov_path = os.path.join(out_dir, "wallet-app-a3-provisioned.json")
with open(prov_path, "w", encoding="utf-8") as fh:
    json.dump(provisioned, fh, indent=2)
    fh.write("\n")

non_row = [p for p in panels if p["type"] != "row"]
print(f"Wrote {out_path} (manual import)")
print(f"Wrote {prov_path} (file provisioning)")
print(f"  {len(non_row)} panels across {len([p for p in panels if p['type'] == 'row'])} rows")
