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

money_in_system_pkr = Gauge(
    "wallet_money_in_system_pkr",
    "Sum of all wallet balances in PKR. Refreshed on every balance-changing operation.",
)

registered_users = Gauge(
    "wallet_registered_users",
    "Total number of registered users.",
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


def refresh_business_gauges(db) -> None:
    """Recompute the gauges that describe overall system state.

    Called after any operation that changes balances or user count. Kept as a
    single function so there is exactly one place where gauge values come from.
    """
    from sqlalchemy import func

    from . import models

    total_balance = db.query(func.coalesce(func.sum(models.Wallet.balance), 0.0)).scalar()
    money_in_system_pkr.set(float(total_balance or 0.0))

    user_count = db.query(func.count(models.User.id)).scalar()
    registered_users.set(int(user_count or 0))
