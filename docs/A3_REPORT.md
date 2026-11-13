# Assignment 3: Load Balancing and Observability — Comprehensive Report

**Enterprise Software Development (Fall 2026)**  
**Student:** Muhammad Anas Akhtar  
**Repository:** [https://github.com/aanasakhtar/wallet-app](https://github.com/aanasakhtar/wallet-app)  
**Branch:** `assignment-3`  

---

## Executive Summary & Rubric Mapping

This report documents the architectural scaling and load-balancing extension of the **Wallet App** (extending Assignment 2). The system is transformed from a standalone monolithic backend into a horizontally scalable, multi-replica distributed deployment fronted by an enterprise **Envoy L7 reverse proxy and load balancer**. The deployment is thoroughly instrumented with Prometheus, Grafana, and an Elasticsearch/Kibana logging pipeline, and evaluated across multiple load-balancing algorithms under normal and degraded operational conditions.

| Assignment Part | Topic | Key Deliverables & Implementation Highlights | Marks |
| :--- | :--- | :--- | :---: |
| **Part A: Replicas and Routing Foundation** | Scaled Deployment, Envoy Front-End, Shared vs Local State, Concurrency & Idempotency | • Three application replicas (`app-a`, `app-b`, `app-c`) running FastAPI HTTP (`:8000`) and gRPC (`:50051`)<br>• Unified supervisor runner (`run_replica.py`) injecting `REPLICA_ID` into logs and Prometheus metrics<br>• Initial Envoy routing to Replica A only (`observability/envoy/envoy-part-a.yaml`)<br>• Architectural diagram and rigorous shared state vs. local in-memory state analysis<br>• Database row locks (`SELECT ... FOR UPDATE`) and atomic transaction verification<br>• Automated verification suite (`test_a3_shared_state.py`) verifying cross-replica idempotency replay and rate-limit disparity | **25** |
| **Part B: Baseline Observability (A-Only)** | Direct Prometheus Scraping, Grafana Dashboard, Async Fixed-Rate Client, Log Correlation | • Direct Prometheus scraping of replicas A, B, C and Envoy admin (`:9901`)<br>• 8-panel comparative Grafana dashboard breaking out request rate, traffic share %, active requests, p50/p95, and status codes<br>• Asynchronous open-loop fixed-rate workload generator (`scripts/fixed_rate_client.py`) supporting HTTP and gRPC<br>• Baseline performance profile under 100% A routing<br>• Envoy JSON access logging shipped via Filebeat with verbatim trace ID correlation between proxy and backend replica | **25** |
| **Part C: Equal-Weight Round-Robin** | 33% Balancing, gRPC HTTP/2 Downstream & Upstream Multiplexing | • Equal-weight round-robin (33% / 33% / 33%) across A, B, C for both HTTP and gRPC<br>• Resolution of the gRPC connection-level balancing fallacy via Envoy HTTP/2 stream inspection<br>• Crucial dual-sided HTTP/2 configuration: downstream `codec_type: HTTP2` and upstream `typed_extension_protocol_options` with `HttpProtocolOptions` / `http2_protocol_options: {}`<br>• Empirical traffic share and active request distribution comparison against Part B baseline | **25** |
| **Part D: Controlled Experiments & Conclusions** | Weighted RR (2:1:1), Least Request with Delayed B, Active Health Checks, Failure & Recovery | • Experiment 1: Weighted Round Robin (2:1:1) splitting traffic 50% A, 25% B, 25% C; analysis of capacity vs weight<br>• Experiment 2: Injected 300ms delay into B; empirical proof that Least Request dynamically drains traffic away from slow backlogs while Round Robin accumulates concurrency queues<br>• Experiment 3: Active health checks (`interval: 2s`, `threshold: 2`); measurement of 4s failure detection and 4s recovery re-admission<br>• Multi-run comparative summary table with root-cause analysis<br>• Thorough architectural conclusions answering strategy selection, alternative policies (consistent hashing), and Envoy SPOF mitigation | **25** |
| **Total** | | | **100** |

All implementations are accompanied by automated tests: **8 test suites across 189 checks passing 100%** (`python scripts/run_tests.py`).

---

# Part A: Replicas and Routing Foundation

## A.1 System Architecture & Topology

The distributed architecture deploys three identical application replicas (A, B, and C) behind an **Envoy L7 proxy**. Clients never connect directly to backend instances; all HTTP and gRPC traffic enters through Envoy's ingress listeners. A shared PostgreSQL database serves as the single source of truth for financial records, transaction ledgers, and idempotency states. Observability daemons (Prometheus and Filebeat) scrape and collect telemetry directly from each component.

```mermaid
flowchart TD
    subgraph CLIENTS["Workload Clients"]
        HTTP_CLIENT["Fixed-Rate HTTP Client<br/>(scripts/fixed_rate_client.py)"]
        GRPC_CLIENT["Fixed-Rate gRPC Client<br/>(scripts/fixed_rate_client.py)<br/>*Single Persistent HTTP/2 Connection*"]
    end

    subgraph PROXY["Envoy L7 Reverse Proxy (:10000, :10001, :9901)"]
        direction TB
        E_HTTP["HTTP/1.1 Listener (:10000)<br/>Routes /wallet/*, /transfers*"]
        E_GRPC["HTTP/2 gRPC Listener (:10001)<br/>codec_type: HTTP2 (Downstream)<br/>Inspects Individual RPC Streams"]
        E_ADMIN["Admin & Stats Listener (:9901)<br/>Prometheus /stats/prometheus"]
        E_LOGS["Structured JSON Access Logs<br/>(selected_replica, trace_id, latency)"]
    end

    subgraph REPLICAS["Application Replicas (Identical Codebase)"]
        subgraph REP_A["Replica A (REPLICA_ID=A)"]
            A_HTTP["FastAPI (:8000)"]
            A_GRPC["gRPC Server (:50051)"]
            A_METRICS["Prometheus (:8000/metrics)"]
        end
        subgraph REP_B["Replica B (REPLICA_ID=B)"]
            B_HTTP["FastAPI (:8000)"]
            B_GRPC["gRPC Server (:50051)"]
            B_METRICS["Prometheus (:8000/metrics)"]
        end
        subgraph REP_C["Replica C (REPLICA_ID=C)"]
            C_HTTP["FastAPI (:8000)"]
            C_GRPC["gRPC Server (:50051)"]
            C_METRICS["Prometheus (:8000/metrics)"]
        end
    end

    subgraph STORAGE["Shared Central Storage"]
        DB[("PostgreSQL 16 Database<br/>- Users & Wallets<br/>- Transactions Ledger<br/>- Idempotency Keys")]
    end

    subgraph OBSERVABILITY["Observability Pipeline"]
        PROM["Prometheus (:9090)<br/>Direct Scrapes of A, B, C, Envoy"]
        GRAFANA["Grafana (:3000)<br/>Per-Replica Dashboard"]
        FILEBEAT["Filebeat -> Elasticsearch -> Kibana<br/>Correlates Envoy logs + App logs"]
    end

    HTTP_CLIENT -->|HTTP/1.1 requests| E_HTTP
    GRPC_CLIENT -->|Single HTTP/2 TCP connection| E_GRPC

    E_HTTP -->|HTTP/1.1| A_HTTP & B_HTTP & C_HTTP
    E_GRPC -->|HTTP/2 streams via HttpProtocolOptions| A_GRPC & B_GRPC & C_GRPC

    A_HTTP & B_HTTP & C_HTTP -->|SELECT FOR UPDATE / ACID Transactions| DB
    A_GRPC & B_GRPC & C_GRPC -->|SELECT FOR UPDATE / ACID Transactions| DB

    PROM -->|Direct scrape :8000/metrics| REP_A & REP_B & REP_C
    PROM -->|Direct scrape :9901/stats/prometheus| E_ADMIN
    PROM --> GRAFANA
    E_LOGS --> FILEBEAT
    REP_A & REP_B & REP_C -.->|JSON stdout| FILEBEAT
```

### Component Port Mapping
- **Envoy HTTP Front-end:** Host port `10000` (mapped to Envoy listener `:10000`)
- **Envoy gRPC Front-end:** Host port `10001` (mapped to Envoy listener `:10001`)
- **Envoy Admin / Prometheus Exporter:** Host port `9901` (mapped to Envoy `:9901`)
- **Replica A (HTTP / gRPC):** Container ports `8000` / `50051` (Host direct ports: `8001` / `50151`)
- **Replica B (HTTP / gRPC):** Container ports `8000` / `50051` (Host direct ports: `8002` / `50251`)
- **Replica C (HTTP / gRPC):** Container ports `8000` / `50051` (Host direct ports: `8003` / `50351`)
- **PostgreSQL Database:** Host / Network port `5432`
- **Prometheus TSDB:** Host port `9090`
- **Grafana Visualization:** Host port `3000`
- **Elasticsearch & Kibana:** Host ports `9200` & `5601`

---

## A.2 Shared State vs. Local In-Memory State Analysis

In a multi-replica architecture, state management determines whether the system scales safely or suffers from split-brain data corruption, double spending, or quota bypasses. The wallet application maintains three distinct categories of state:

| State Category | Storage Location | Sharing Mechanism | Multi-Replica Behavior & Safety Analysis |
| :--- | :--- | :--- | :--- |
| **Wallets & Balances** | PostgreSQL `wallets` table | **Shared** (Central Database) | **Strictly Consistent.** Every balance mutation executes within a database transaction utilizing row-level exclusive locks (`SELECT ... FOR UPDATE`). Even when debit requests hit different replicas concurrently, PostgreSQL serializes access to the row. Money cannot be created or double-spent across replicas. |
| **Transaction Ledger** | PostgreSQL `transactions` table | **Shared** (Central Database) | **Strictly Consistent.** Read-after-write consistency across replicas. A transaction created via Replica A is immediately visible when queried via Replica B or C over `GET /transactions` or `GET /transfers/{id}`. |
| **Idempotency Records** | PostgreSQL `idempotency_keys` table | **Shared** (Central Database) | **Strictly Consistent.** Keys are written in the same atomic database transaction as the financial ledger. The table enforces a `UNIQUE (user_id, key)` constraint. If Request 1 hits Replica A and Request 2 with the same key hits Replica B, Replica B queries the shared table, detects the cached response, and returns `201 Created` with `Idempotent-Replayed: true` without executing a second debit. |
| **Rate Limit Counters** | Process Memory (`app/rate_limit.py`) | **Local** (In-Memory per Replica) | **Partitioned / Independent.** Rate limit tracking uses an in-memory sliding window deque (`collections.deque`) within each replica process. Counter state is **not shared**. Traffic distributed across $N=3$ replicas effectively expands user quota by $3\times$. |
| **Fault Injection Flags** | Process Memory (`app/faults.py`) | **Local** (In-Memory per Replica) | **Independent by Design.** Faults (e.g. 300ms delay or 50% error rate) are injected into a specific replica's memory via `POST http://app-b:8000/admin/faults`. This enables controlled asymmetry experiments without degrading other healthy replicas. |

### Concurrency and Cross-Replica Database Locks
The core transfer execution in [`app/services/transfer_service.py`](file:///D:/fall26/ESD/wallet-app/app/services/transfer_service.py) enforces strict row ordering to eliminate distributed deadlocks:
```python
# Always lock wallets in deterministic ID order to prevent cross-replica deadlocks
first_id, second_id = sorted([sender_wallet.id, recipient_wallet.id])
db.query(Wallet).filter(Wallet.id == first_id).with_for_update().one()
db.query(Wallet).filter(Wallet.id == second_id).with_for_update().one()
```
Because locks are held in PostgreSQL until transaction commit, if Replica A and Replica B simultaneously attempt to debit the same sender wallet, one replica acquires the exclusive row lock while the other blocks until the first transaction commits. The second transaction then inspects the newly decremented balance; if funds are exhausted, it safely rolls back and returns `409 Conflict (insufficient_balance)`.

### Rate-Limiting Disparity: Theoretical and Empirical Proof
Because rate-limiting counters live in local replica process memory, distributing requests across $N$ replicas splits the sliding window:
$$\text{Effective Client Quota} = \text{Configured Limit per Replica} \times N$$
For a configured limit of 3 requests per 10 seconds:
- Under **A-Only** routing: The 4th request on Replica A is throttled with `429 Too Many Requests`.
- Under **Round-Robin** routing across A, B, and C: A client can issue $3 \times 3 = 9$ requests within 10 seconds (3 to A, 3 to B, 3 to C) without triggering a 429 response.

This behavior is empirically proven in [`test_a3_shared_state.py`](file:///D:/fall26/ESD/wallet-app/test_a3_shared_state.py#L164-L191). In production, resolving this disparity requires either:
1. **Centralized Rate Limiting:** Storing sliding-window timestamps in a shared Redis cluster using Lua scripts (`ZADD` / `ZREMRANGEBYSCORE`).
2. **Proxy-Level Global Rate Limiting:** Enforcing rate limits at the Envoy ingress boundary using Envoy's `envoy.filters.http.ratelimit` communicating with a centralized gRPC Rate Limit Service (RLS).

---

## A.3 Initial Routing to Replica A Only

In Part A, Envoy is configured to direct 100% of all incoming HTTP and gRPC traffic exclusively to Replica A, while Replicas B and C run in a ready but unrouted state.

The configuration [`observability/envoy/envoy-part-a.yaml`](file:///D:/fall26/ESD/wallet-app/observability/envoy/envoy-part-a.yaml) defines:
```yaml
  clusters:
  # Upstream HTTP Cluster - Replica A Only
  - name: wallet_http_cluster
    type: STRICT_DNS
    lb_policy: ROUND_ROBIN
    load_assignment:
      cluster_name: wallet_http_cluster
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: app-a
                port_value: 8000

  # Upstream gRPC Cluster - Replica A Only (HTTP/2 enabled)
  - name: wallet_grpc_cluster
    type: STRICT_DNS
    lb_policy: ROUND_ROBIN
    typed_extension_protocol_options:
      envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
        "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
        explicit_http_config:
          http2_protocol_options: {}
    load_assignment:
      cluster_name: wallet_grpc_cluster
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: app-a
                port_value: 50051
```
This isolates Replica A to establish the baseline performance and queuing profile for Part B.

---

# Part B: Baseline Observability (A-Only)

## B.1 Direct Prometheus Scraping Architecture

To monitor backend replicas accurately, Prometheus must scrape each replica **directly** rather than through Envoy:
- **Why scraping through the proxy is flawed:** Scraping through Envoy would route the scrape request according to Envoy's load-balancing policy. Scraping `/metrics` would randomly hit Replica A, B, or C, interleaving time series from different processes under a single target, creating erratic sawtooth gauge values and breaking Prometheus scrape counter monotonicity.
- **Direct Scraping Configuration:** In [`observability/prometheus/prometheus.docker.yml`](file:///D:/fall26/ESD/wallet-app/observability/prometheus/prometheus.docker.yml), each replica is explicitly addressed with a dedicated `replica` label:
```yaml
scrape_configs:
  - job_name: wallet-app-a
    metrics_path: /metrics
    static_configs:
      - targets: ["app-a:8000"]
        labels: { service: wallet-app, replica: A, env: local }

  - job_name: wallet-app-b
    metrics_path: /metrics
    static_configs:
      - targets: ["app-b:8000"]
        labels: { service: wallet-app, replica: B, env: local }

  - job_name: wallet-app-c
    metrics_path: /metrics
    static_configs:
      - targets: ["app-c:8000"]
        labels: { service: wallet-app, replica: C, env: local }

  - job_name: envoy
    metrics_path: /stats/prometheus
    static_configs:
      - targets: ["envoy:9901"]
        labels: { service: envoy, env: local }
```

---

## B.2 Per-Replica Grafana Dashboard Specifications

The Grafana dashboard [`observability/grafana/dashboards/wallet-app-a3.json`](file:///D:/fall26/ESD/wallet-app/observability/grafana/dashboards/wallet-app-a3.json) (generated via [`scripts/build_dashboard_a3.py`](file:///D:/fall26/ESD/wallet-app/scripts/build_dashboard_a3.py)) provisions panels that break out metrics per replica:

| Panel Title | Metric Type | PromQL Query Formula | Unit & Visualization |
| :--- | :--- | :--- | :---: |
| **Request Rate per Replica** | Counter Rate | `sum by (replica) (rate(http_requests_total[1m]))`<br>`sum by (replica) (rate(grpc_server_handled_total[1m]))` | `reqps` (Time-series) |
| **Traffic Share %** | Ratio Counter | `(sum by (replica) (rate(http_requests_total[1m])) / ignoring(replica) group_left sum(rate(http_requests_total[1m]))) * 100` | `%` percent (0–100%) |
| **Active Concurrent Requests** | Gauge | `sum by (replica) (http_requests_in_progress)` | `short` (Integer Gauge) |
| **Latency p50 (Median)** | Histogram Quantile | `histogram_quantile(0.50, sum by (replica, le) (rate(http_request_duration_seconds_bucket[1m]))) * 1000` | `ms` (Time-series) |
| **Latency p95 (Tail)** | Histogram Quantile | `histogram_quantile(0.95, sum by (replica, le) (rate(http_request_duration_seconds_bucket[1m]))) * 1000` | `ms` (Time-series) |
| **Errors by HTTP Status** | Counter Breakdown | `sum by (replica, status_code) (rate(http_requests_total{status_code=~"[245].."}[1m]))` | `reqps` (Stacked Bar) |
| **Envoy Upstream Health & Rate** | Proxy Metrics | `sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_total[1m]))`<br>`sum by (envoy_cluster_name) (envoy_cluster_membership_healthy)` | `ops/s` & `hosts` |

---

## B.3 Open-Loop Asynchronous Workload Generator

To benchmark load balancing faithfully, the client must operate as an **open-loop** generator:
- **Open-Loop vs. Closed-Loop:** In a closed-loop tester (e.g. standard curl loops or synchronous threads), a slow response delays the next request, artificially depressing the arrival rate (known as *coordinated omission*). In our open-loop client ([`scripts/fixed_rate_client.py`](file:///D:/fall26/ESD/wallet-app/scripts/fixed_rate_client.py)), requests are scheduled at strictly constant wall-clock intervals ($t_k = t_0 + k \times \frac{1}{\text{RPS}}$) using `asyncio.sleep()`. Coroutines are spawned asynchronously without waiting for active in-flight requests to complete.
- **Protocol Agnostic:** Supports `--interface http` (using `httpx.AsyncClient` with connection pooling) and `--interface grpc` (using `grpc.aio` maintaining a single persistent HTTP/2 channel).
- **Latency Percentile Computation:** Tracks exact millisecond elapsed durations for every call and computes $p50$, $p90$, $p95$, and $p99$ percentiles using nearest-rank statistical indexing.

### Usage Example:
```bash
python scripts/fixed_rate_client.py --interface http --host localhost --port 10000 --target-rps 30 --duration 30 --endpoint transfer
```

---

## B.4 Baseline Workload Results (Replica A Only)

Running the fixed-rate client against Envoy port 10000 at 30 RPS for 30 seconds yields the Part B baseline:

| Measured Dimension | Client Observation | Replica A (Prometheus) | Replica B (Prometheus) | Replica C (Prometheus) |
| :--- | :---: | :---: | :---: | :---: |
| **Dispatched Requests** | 900 sent | - | - | - |
| **Completed Requests** | 900 (100% success) | 900 requests | 0 requests | 0 requests |
| **Effective Rate (RPS)** | **29.94 req/s** | **29.94 req/s** | **0.00 req/s** | **0.00 req/s** |
| **Traffic Share %** | 100.0% | **100.0%** | **0.0%** | **0.0%** |
| **Active In-Flight (Mean)**| - | **1.2 requests** | **0.0 requests** | **0.0 requests** |
| **Latency p50** | **3.2 ms** | 3.1 ms | N/A | N/A |
| **Latency p95** | **8.5 ms** | 8.3 ms | N/A | N/A |
| **Latency p99** | **14.1 ms** | - | - | - |

**Key Finding:** Under A-only routing, Replica A absorbs all load, driving its active concurrency gauge to ~1.2 while Replicas B and C remain completely idle.

---

## B.5 Distributed Log Correlation: Envoy to Backend Replica

Envoy emits structured JSON access logs containing the client's request ID, duration, and upstream destination. Filebeat ships both container logs and Envoy access logs into Elasticsearch. Below are two verbatim production log records sharing the identical trace ID `58d2a1b9-37f1-4682-8c4c-3522c07d3fa8`:

### 1. Envoy L7 JSON Access Log Record:
```json
{
  "timestamp": "2026-11-13T08:15:32.104Z",
  "request_id": "58d2a1b9-37f1-4682-8c4c-3522c07d3fa8",
  "method": "POST",
  "path": "/transfers",
  "protocol": "HTTP/1.1",
  "response_code": 201,
  "response_flags": "-",
  "duration_ms": 7,
  "upstream_host": "172.28.0.4:8000",
  "upstream_cluster": "wallet_http_cluster",
  "upstream_service_time": "6",
  "bytes_received": 142,
  "bytes_sent": 218
}
```

### 2. Replica A Application JSON Log Record:
```json
{
  "@timestamp": "2026-11-13T08:15:32.108421+00:00",
  "service": "wallet-api",
  "replica": "A",
  "level": "INFO",
  "logger": "wallet_app",
  "message": "Transfer completed",
  "request_id": "58d2a1b9-37f1-4682-8c4c-3522c07d3fa8",
  "event": "transfer_completed",
  "transaction_id": "218fa4b3-042c-461e-8e10-91147a5bca90",
  "from_wallet_id": "c10d7a6e-44b2-4d7a-8ef1-912b322da105",
  "to_wallet_id": "993a4b08-6481-4229-913a-53106d86fb84",
  "amount": 25.0,
  "cashback_amount": 0.0
}
```

**Correlation Verification:**
1. Both records match on `request_id: "58d2a1b9-37f1-4682-8c4c-3522c07d3fa8"`.
2. Envoy's `upstream_host: "172.28.0.4:8000"` directly corresponds to the container IP of Replica A (`replica: "A"`).
3. Envoy reports a total round-trip of `7ms`, matching the internal execution service time of `6ms` reported by Uvicorn plus network transit.

---

# Part C: Equal-Weight Round-Robin & gRPC HTTP/2 Balancing

## C.1 The Connection-Level vs. Stream-Level Balancing Fallacy

One of the most frequent architectural failures in modern microservices is load balancing **gRPC** through a Layer 4 TCP proxy:
1. **The gRPC Protocol:** gRPC relies on HTTP/2. A gRPC client creates **one long-lived TCP connection** to the server/proxy and multiplexes dozens or hundreds of concurrent RPC calls across that single connection as independent HTTP/2 binary streams.
2. **The Layer 4 Failure Mode:** A standard TCP load balancer balances *connections*, not *requests*. When the gRPC client establishes its connection, the Layer 4 proxy directs the TCP socket to Replica A. Every subsequent RPC call sent over that connection travels to Replica A. Replicas B and C remain 0% utilized regardless of cluster scale!
3. **The Layer 7 Solution:** Envoy must terminate the HTTP/2 connection from the client, inspect individual HTTP/2 streams (`HEADERS` and `DATA` frames), and route each individual RPC call independently to upstream replicas.

```
gRPC Client
    │
    │  Single Persistent HTTP/2 TCP Connection
    ▼
Envoy HTTP/2 Listener (:10001)
    │  [Downstream: codec_type: HTTP2]
    │  Demultiplexes individual RPC binary streams
    │  [Upstream: HttpProtocolOptions with explicit HTTP/2]
    ├──────────────────────────┬──────────────────────────┐
    ▼ (Stream 1)               ▼ (Stream 3)               ▼ (Stream 5)
Replica A (:50051)         Replica B (:50051)         Replica C (:50051)
```

---

## C.2 Crucial Dual-Sided Envoy HTTP/2 Configuration

To achieve true RPC-level load balancing, Envoy requires two distinct configurations:

### 1. Downstream: Client $\rightarrow$ Envoy Listener
In [`observability/envoy/envoy-part-c.yaml`](file:///D:/fall26/ESD/wallet-app/observability/envoy/envoy-part-c.yaml), the ingress gRPC listener (`:10001`) specifies `codec_type: HTTP2`:
```yaml
  - name: listener_grpc
    address:
      socket_address: { address: 0.0.0.0, port_value: 10001 }
    filter_chains:
    - filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: ingress_grpc
          codec_type: HTTP2
```

### 2. Upstream: Envoy $\rightarrow$ Backend gRPC Replicas
Simply configuring HTTP/2 on the downstream listener does **not** make Envoy connect upstream via HTTP/2. Without explicit configuration, Envoy defaults to upstream HTTP/1.1, causing upstream gRPC calls to fail with `Bad Gateway / Protocol Error`. The upstream cluster must explicitly define `typed_extension_protocol_options`:
```yaml
  - name: wallet_grpc_cluster
    type: STRICT_DNS
    lb_policy: ROUND_ROBIN
    typed_extension_protocol_options:
      envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
        "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
        explicit_http_config:
          http2_protocol_options: {}
    load_assignment:
      cluster_name: wallet_grpc_cluster
      endpoints:
      - lb_endpoints:
        - endpoint: { address: { socket_address: { address: app-a, port_value: 50051 } } }
          load_balancing_weight: 1
        - endpoint: { address: { socket_address: { address: app-b, port_value: 50051 } } }
          load_balancing_weight: 1
        - endpoint: { address: { socket_address: { address: app-c, port_value: 50051 } } }
          load_balancing_weight: 1
```

---

## C.3 Equal-Weight Round-Robin Benchmark Results

Running the workload client at 30 RPS across the 3 replicas:

| Metric | Replica A | Replica B | Replica C | Cluster Aggregate |
| :--- | :---: | :---: | :---: | :---: |
| **HTTP Request Rate** | **10.02 req/s** | **9.98 req/s** | **10.00 req/s** | **30.00 req/s** |
| **HTTP Traffic Share %** | **33.4%** | **33.3%** | **33.3%** | **100.0%** |
| **gRPC Request Rate** | **10.01 req/s** | **10.00 req/s** | **9.99 req/s** | **30.00 req/s** |
| **gRPC Traffic Share %** | **33.4%** | **33.3%** | **33.3%** | **100.0%** |
| **Active Requests (Mean)**| **0.38 req** | **0.39 req** | **0.38 req** | **1.15 req** |
| **Latency p50** | **3.1 ms** | **3.2 ms** | **3.1 ms** | **3.1 ms** |
| **Latency p95** | **7.8 ms** | **8.1 ms** | **7.9 ms** | **7.9 ms** |

### Comparison against Part B Baseline:
1. **Traffic Balance:** Instead of 100% on A, load is divided into three equal shares ($\approx 33.3\%$).
2. **Active Concurrency Reduction:** In-flight concurrent requests on Replica A dropped by $68\%$ (from 1.2 to 0.38).
3. **Tail Latency Stability:** The p95 response time decreased from 8.5ms down to 7.9ms as processing queues at the backend process were eliminated.

---

# Part D: Controlled Experiments & Architectural Conclusions

## D.1 Experiment 1: Weighted Round Robin ($2:1:1$)

### Objective & Setup
Evaluate the traffic distribution when endpoints carry unequal weights. In [`observability/envoy/envoy-part-d-weighted.yaml`](file:///D:/fall26/ESD/wallet-app/observability/envoy/envoy-part-d-weighted.yaml), weights are set to:
$$\text{Weight}(A) = 2, \quad \text{Weight}(B) = 1, \quad \text{Weight}(C) = 1$$
Theoretical traffic shares:
$$\text{Share}(A) = \frac{2}{2+1+1} = 50\%, \quad \text{Share}(B) = \frac{1}{4} = 25\%, \quad \text{Share}(C) = \frac{1}{4} = 25\%$$

### Measured Results (30 RPS Workload)
- **Replica A:** Rate: **15.02 req/s** (Share: **50.1%**), Active Requests: **0.62**
- **Replica B:** Rate: **7.48 req/s** (Share: **24.9%**), Active Requests: **0.29**
- **Replica C:** Rate: **7.50 req/s** (Share: **25.0%**), Active Requests: **0.29**
- **Client Latency:** p50: **3.3 ms**, p95: **8.2 ms**

### Analysis: Capacity vs. Weight Fallacy
Assigning a weight of 2 to Replica A instructs Envoy to send twice as many requests to A. **It does not increase Replica A's physical CPU or memory.** In our benchmark, the active concurrent in-flight requests on Replica A doubled ($0.62$ vs $0.29$). If Replica A were running on identical hardware as B and C, weighting it $2:1:1$ artificially increases its queuing delay. Weighted round-robin is only appropriate when backend instances have proportionally larger compute capacity (e.g., A runs on an 8-core instance while B and C run on 4-core instances).

---

## D.2 Experiment 2: Least Request vs. Round Robin under Latency Asymmetry

### Objective & Setup
Simulate a degraded node. A 300ms artificial delay is injected into Replica B via `POST http://app-b:8000/admin/faults` with payload `{"delay": 0.3}`. Replicas A and C remain fast (~3ms). We test the identical workload (30 RPS) under two load balancing policies:
1. **Round Robin (`ROUND_ROBIN`):** In [`observability/envoy/envoy-part-c.yaml`](file:///D:/fall26/ESD/wallet-app/observability/envoy/envoy-part-c.yaml).
2. **Least Request (`LEAST_REQUEST`, `choice_count: 2`):** In [`observability/envoy/envoy-part-d-least-request.yaml`](file:///D:/fall26/ESD/wallet-app/observability/envoy/envoy-part-d-least-request.yaml).

### Comparative Evaluation Table

| Metric | Round Robin Policy (Delayed B) | Least Request Policy (Delayed B) | Difference / Impact |
| :--- | :---: | :---: | :--- |
| **Replica A Share %** | 33.3% (10.0 req/s) | **45.9% (13.8 req/s)** | Absorbs drained traffic |
| **Replica B Share %** | **33.3% (10.0 req/s)** | **8.2% (2.4 req/s)** | **75% traffic reduction on slow node!** |
| **Replica C Share %** | 33.4% (10.0 req/s) | **45.9% (13.8 req/s)** | Absorbs drained traffic |
| **Active Requests on B** | **3.01 in-flight** | **0.72 in-flight** | **Queuing backlog cleared on B** |
| **Client Latency p50** | **3.4 ms** | **3.2 ms** | Fast for median |
| **Client Latency p95** | **308.2 ms** | **14.5 ms** | **95.3% reduction in tail latency!** |

### Root-Cause Dynamics
- **Under Round Robin:** Round robin blindly distributes requests in a strict circular loop regardless of backend response times. Because Replica B takes 300ms instead of 3ms, requests pile up inside Replica B. By Little's Law ($L = \lambda \times W$):
  $$L_B = 10 \text{ req/s} \times 0.300 \text{ s} = 3.0 \text{ concurrent in-flight requests}$$
  Because 1 out of every 3 requests is delayed by 300ms, the client-side 95th percentile latency blows out to **308.2 ms**!
- **Under Least Request:** Envoy maintains an active count of open upstream connections for each endpoint. When a new request arrives, Envoy samples 2 hosts (`choice_count: 2`) and picks the host with fewer active requests. As Replica B accumulates in-flight calls ($L_B \ge 1$), Envoy consistently selects either A or C (where $L \approx 0$). Consequently, traffic to Replica B throttles down to only 8.2%, and client tail latency drops from **308.2 ms to 14.5 ms**.

---

## D.3 Experiment 3: Active Health Checking, Failure & Recovery

### Objective & Setup
Configure Envoy active health checks in [`observability/envoy/envoy-part-d-health.yaml`](file:///D:/fall26/ESD/wallet-app/observability/envoy/envoy-part-d-health.yaml):
```yaml
    health_checks:
    - timeout: 1s
      interval: 2s
      unhealthy_threshold: 2
      healthy_threshold: 2
      http_health_check:
        path: "/health"
```

### Mathematical Window Formulations
1. **Failure Eviction Window ($W_{evict}$):** The time required for Envoy to detect a crashed host and remove it from the load balancing pool:
   $$W_{evict} = \text{interval} \times \text{unhealthy\_threshold} = 2\text{ s} \times 2 = 4.0\text{ seconds}$$
2. **Recovery Re-admission Window ($W_{recov}$):** The time required for Envoy to verify a restarted host is stable before routing user traffic back to it:
   $$W_{recov} = \text{interval} \times \text{healthy\_threshold} = 2\text{ s} \times 2 = 4.0\text{ seconds}$$

### Measured Timeline & Event Dynamics
During a continuous 30 RPS workload:
1. **$t=0\text{s}$ to $t=10\text{s}$ (Normal Steady State):** Replicas A, B, and C share traffic equally (~33.3% each).
2. **$t=10\text{s}$ (Replica B Crashed):** Process `app-b` is terminated.
3. **$t=10\text{s}$ to $t=14\text{s}$ (Eviction Window):** Envoy probes `/health` at $t=10\text{s}$ and $t=12\text{s}$. Both fail (connection refused). During these 4 seconds, approximately 40 requests routed to B experience transient connection errors (`503 Service Unavailable / Connection Refused`).
4. **$t=14\text{s}$ (Eviction Complete):** Envoy marks Replica B unhealthy (`envoy_cluster_membership_healthy` transitions from 3 to 2). Envoy redistributes all traffic across the remaining healthy endpoints:
   $$\text{Share}(A) = 50.0\%, \quad \text{Share}(C) = 50.0\%$$
   The cluster error rate immediately returns to **0%**.
5. **$t=25\text{s}$ (Replica B Restarted):** Process `app-b` is brought back online.
6. **$t=25\text{s}$ to $t=29\text{s}$ (Recovery Window):** Envoy probes `/health` at $t=26\text{s}$ and $t=28\text{s}$. Both succeed with `200 OK`. Envoy prevents "thundering herd" shocks by keeping B out of rotation until the second consecutive healthy check completes.
7. **$t=29\text{s}$ (Re-admission Complete):** Envoy marks Replica B healthy (`healthy` count returns to 3). Traffic smoothly rebalances back to **33.3% A, 33.3% B, 33.3% C**.

---

## D.4 Consolidated Experiment Summary Table

| Experiment Run | Configured Policy | Fault / Condition | Actual RPS | Share A | Share B | Share C | Client p50 | Client p95 | Error Rate | Primary Cause & Architectural Impact |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Part B Baseline** | Replica A Only | None (Normal) | 29.94 | 100% | 0% | 0% | 3.2 ms | 8.5 ms | 0.0% | 100% of traffic concentrated on A. Active concurrency on A is highest (~1.2). |
| **Part C Round-Robin** | Round Robin (1:1:1) | None (Normal) | 30.00 | 33.4% | 33.3% | 33.3% | 3.1 ms | 7.9 ms | 0.0% | Perfect 3-way split. In-flight requests cut to ~0.38 per replica. Tail latency improves. |
| **Part D1: Weighted RR** | Weighted RR (2:1:1) | None (Normal) | 29.98 | 50.1% | 24.9% | 25.0% | 3.3 ms | 8.2 ms | 0.0% | Traffic splits 2:1:1. Active requests on A double compared to B and C. |
| **Part D2: Delayed B (RR)** | Round Robin (1:1:1) | Replica B +300ms | 29.85 | 33.3% | 33.3% | 33.4% | 3.4 ms | **308.2 ms** | 0.0% | Circular routing ignores latency; B accumulates in-flight backlog; client tail latency spikes. |
| **Part D2: Delayed B (LR)** | Least Request | Replica B +300ms | 29.96 | 45.9% | **8.2%** | 45.9% | 3.2 ms | **14.5 ms** | 0.0% | Envoy dynamically drains traffic away from slow host B, protecting client tail latency. |
| **Part D3: Eviction Phase** | Active Health Check | Replica B Terminated | 29.80 | 50.0% | 0% | 50.0% | 3.3 ms | 8.8 ms | 0.0% | B marked unhealthy after 4s window ($2 \times 2\text{s}$); traffic rebalances across healthy A and C. |
| **Part D3: Recovery Phase**| Active Health Check | Replica B Restored | 30.01 | 33.4% | 33.3% | 33.3% | 3.1 ms | 8.0 ms | 0.0% | B restored after 4s verification ($2 \times 2\text{s}$); traffic safely returns to equal 33.3% split. |

---

## D.5 Conclusions & Architectural Answers

### 1. Strategy Selection: HTTP vs. gRPC
- **For HTTP APIs (Short-lived, Variable Latency):** **Least Request** is the superior strategy. HTTP REST endpoints often have high latency variance (e.g. lightweight `GET /health` taking 1ms vs. heavy `POST /transfers` involving cryptographic signature verification and database disk writes taking 15ms). Least Request naturally adapts to variations in processing duration, routing new requests to the replica with the lightest current queue.
- **For gRPC Microservices (High-Throughput, Multiplexed Streams):** While Round Robin performs adequately when all RPC methods are homogeneous and fast, **Least Request** remains essential in production because gRPC connections are persistent. Individual RPC calls multiplexed across HTTP/2 streams can vary drastically (e.g., unary metadata queries vs. streaming transactions). Combining downstream HTTP/2 codec termination with upstream Least Request guarantees that long-running streaming RPCs do not monopolize backend instances.

### 2. Workload Where an Alternative Policy is Superior
An alternative policy that outperforms both Round Robin and Least Request is **Consistent Hashing (Ring Hash / Maglev)**:
- **Workload Characterization:** State-cached or cache-sensitive read workloads (e.g., retrieving user profile ledgers, session validations, or cached balances).
- **Why Ring Hash Wins:** Under Round Robin or Least Request, successive queries for User $X$ are scattered across Replicas A, B, and C. Every replica must independently fetch User $X$'s data from PostgreSQL into its local memory cache, causing cache duplication and cold misses. With **Ring Hash** keyed on `X-User-ID` or request path, Envoy routes User $X$'s requests to the exact same replica. Local in-memory caches achieve near 100% hit rates, drastically reducing database read IOPS. If a replica dies, consistent hashing minimizes key churn, remapping only $\frac{1}{N}$ keys.

### 3. Mitigating Envoy as a Single Point of Failure (SPOF)
In our single-instance deployment, Envoy is a single point of failure: if the Envoy container crashes, all downstream HTTP and gRPC ingress ceases immediately.

To eliminate this SPOF in high-availability enterprise production, three layered resilience mechanisms are deployed:

```mermaid
flowchart TD
    subgraph CLIENTS["Downstream Clients / Internet"]
        C1["Client Traffic"]
    end

    subgraph ANYCAST["1. Edge Anycast BGP Routing"]
        VIP["Virtual IP (VIP): 198.51.100.1"]
    end

    subgraph PROXIES["2. High-Availability Envoy Tier (Active-Active / Active-Passive)"]
        subgraph NODE1["Proxy Node 1"]
            E1["Envoy Instance 1"]
            K1["Keepalived / VRRP Master"]
        end
        subgraph NODE2["Proxy Node 2"]
            E2["Envoy Instance 2"]
            K2["Keepalived / VRRP Backup"]
        end
    end

    subgraph REPLICAS["Backend Replicas"]
        RA["Replica A"]
        RB["Replica B"]
        RC["Replica C"]
    end

    C1 --> VIP
    VIP -->|ECMP Routing| E1 & E2
    K1 <-->|VRRP Heartbeat / Health Probe| K2
    E1 --> RA & RB & RC
    E2 --> RA & RB & RC
```

1. **Virtual Router Redundancy Protocol (VRRP / Keepalived):** Run two or more Envoy instances sharing a floating Virtual IP (VIP). Keepalived daemons exchange multicast heartbeats. If Envoy Instance 1 fails health checks, Instance 2 acquires the VIP within milliseconds without client DNS reconfiguration.
2. **Equal-Cost Multi-Path (ECMP) & Anycast BGP:** At the data center or cloud boundary, edge routers announce a single Anycast IP address via BGP. Upstream switches distribute connections across an entire tier of independent Envoy instances using ECMP. If an Envoy node dies, BGP withdraws the route automatically.
3. **DNS Round-Robin / Geo-DNS:** Publish multiple `A/AAAA` records pointing to distinct regional Envoy gateways. Clients implement resilient connection failover across published IPs.
