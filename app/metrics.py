"""
All Prometheus metrics for the wallet app live here, in one place, so the
metrics table in the report can be generated from a single file.

Metric type coverage (assignment Part B requires all four):
  Counter   - wallet_http_requests_total, wallet_transactions_total,
              wallet_auth_failures_total, wallet_transfer_rejections_total
  Gauge     - wallet_pending_transfers, wallet_money_in_system_pkr,
              wallet_registered_users
  Histogram - wallet_http_request_duration_seconds, wallet_transfer_amount_pkr
  Summary   - wallet_transfer_processing_seconds

Note on Summary: the Python client's Summary exposes only _count and _sum,
so it can give an AVERAGE but not quantiles. That is why transfer latency
also has a Histogram (wallet_http_request_duration_seconds) - the histogram
is what p95/p99 is calculated from. The assignment calls this out explicitly.
"""
from prometheus_client import Counter, Gauge, Histogram, Summary

# ---------------------------------------------------------------------------
# APPLICATION METRICS
# ---------------------------------------------------------------------------

# Labels here are all LOW cardinality on purpose:
#   endpoint is the route TEMPLATE ("/wallet/balance"), never the raw path,
#   so a path with an id in it can't create unbounded series.
http_requests_total = Counter(
    "wallet_http_requests_total",
    "Total HTTP requests handled, by method, route template and status code.",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "wallet_http_request_duration_seconds",
    "HTTP request latency in seconds, by method and route template.",
    ["method", "endpoint"],
    # Buckets chosen around the assignment's example (0.1 / 0.5 / 1s) but
    # extended so an injected 500ms fault is clearly visible in the tail.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0),
)

http_requests_in_progress = Gauge(
    "wallet_http_requests_in_progress",
    "Number of HTTP requests currently being handled.",
)

# ---------------------------------------------------------------------------
# BUSINESS METRICS
# ---------------------------------------------------------------------------

transactions_total = Counter(
    "wallet_transactions_total",
    "Total wallet transactions recorded, by type and final status.",
    ["type", "status"],  # type: topup|transfer|cashback  status: completed|failed
)

transfer_amount_pkr = Histogram(
    "wallet_transfer_amount_pkr",
    "Distribution of completed transfer amounts in PKR.",
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000),
)

transfer_processing_seconds = Summary(
    "wallet_transfer_processing_seconds",
    "Time spent inside the transfer service, excluding HTTP overhead. "
    "Python summaries expose count and sum only, so this yields an average; "
    "use the HTTP duration histogram for p95/p99.",
)

pending_transfers = Gauge(
    "wallet_pending_transfers",
    "Transfers currently in flight (incremented on entry, decremented on completion or failure).",
)


# ---------------------------------------------------------------------------
# SELF-EXPLORED METRICS
# (Part B asks for at least one metric you explored yourself, beyond the
#  class examples. These two answer real product questions: "are people
#  failing to log in?" and "why are transfers being rejected?")
# ---------------------------------------------------------------------------

auth_failures_total = Counter(
    "wallet_auth_failures_total",
    "Failed authentication attempts, by reason.",
    ["reason"],  # bad_password | unknown_email | duplicate_signup
)

transfer_rejections_total = Counter(
    "wallet_transfer_rejections_total",
    "Transfers rejected before any money moved, by reason.",
    ["reason"],  # insufficient_balance | recipient_not_found | self_transfer
)

# ---------------------------------------------------------------------------
# FAULT-INJECTION VISIBILITY (Part E)
# ---------------------------------------------------------------------------

injected_faults_total = Counter(
    "wallet_injected_faults_total",
    "Deliberate faults injected for the anomaly experiment, by kind.",
    ["kind"],  # latency | error
)


# ---------------------------------------------------------------------------
# ASSIGNMENT 2 ADDITIONS
# ---------------------------------------------------------------------------

rate_limit_rejections_total = Counter(
    "wallet_rate_limit_rejections_total",
    "Requests rejected with 429 by the rate limiter, by endpoint template.",
    ["endpoint"],
)

idempotent_replays_total = Counter(
    "wallet_idempotent_replays_total",
    "Transfer requests answered from an earlier result because the Idempotency-Key was already used.",
)


# ---------------------------------------------------------------------------
# ASSIGNMENT 2, PART C
# ---------------------------------------------------------------------------

# --- State that lives in the DATABASE must be read at scrape time -------------
#
# Audit finding. In A1, wallet_money_in_system_pkr and wallet_registered_users were Gauges
# that the process SET after each operation it handled. With one process that is fine. With
# two processes (HTTP + gRPC) it is wrong: each process only updates its gauge when IT writes,
# so after an HTTP top-up the gRPC process kept reporting the old balance (and reported 0
# until its first write). A DB-derived value must not depend on which process wrote last.
# Fix: a custom collector that asks the database every time Prometheus scrapes, so every
# process always exposes the same, current value. (Cost: two cheap aggregate queries per scrape.)

from prometheus_client.core import REGISTRY, GaugeMetricFamily  # noqa: E402


class DatabaseStateCollector:
    def collect(self):
        import logging

        from sqlalchemy import func

        from . import models
        from .database import SessionLocal

        db = SessionLocal()
        try:
            money = db.query(func.coalesce(func.sum(models.Wallet.balance), 0.0)).scalar()
            users = db.query(func.count(models.User.id)).scalar()
        except Exception:  # a DB hiccup must not break the whole scrape; the series just goes absent
            logging.getLogger("wallet_app").warning("DB state metrics unavailable", exc_info=True)
            return
        finally:
            db.close()
        yield GaugeMetricFamily("wallet_money_in_system_pkr",
                                "Sum of all wallet balances in PKR, read from the database at scrape time.",
                                value=float(money or 0.0))
        yield GaugeMetricFamily("wallet_registered_users",
                                "Number of registered users, read from the database at scrape time.",
                                value=float(users or 0))


REGISTRY.register(DatabaseStateCollector())

# --- gRPC server metrics (recorded by grpc_service/interceptors.py) -----------
# Names mirror the HTTP metrics so the two can sit side by side in one dashboard.
# Labels are bounded: grpc_method comes from the registered service definition (one value
# today) and grpc_code is one of the 17 gRPC status names. No user / request / trace ids.

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0)

grpc_server_handled_total = Counter(
    "wallet_grpc_server_handled_total",
    "gRPC calls completed by the server, by method and final status code.",
    ["grpc_method", "grpc_code"],
)

grpc_server_handling_seconds = Histogram(
    "wallet_grpc_server_handling_seconds",
    "Server-side time spent in the RPC handler (interceptor entry to return), in seconds.",
    ["grpc_method"],
    buckets=_LATENCY_BUCKETS,
)

grpc_server_in_flight = Gauge(
    "wallet_grpc_server_in_flight",
    "gRPC calls currently being handled (the server runs a fixed pool of worker threads).",
    ["grpc_method"],
)
