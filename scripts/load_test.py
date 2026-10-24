"""
Repeatable load generator for the Part E anomaly experiment.

Part E requires a test you can run identically before, during and after the
fault. This sends a fixed-rate mix of requests for a fixed duration and
prints client-side latency stats, so you have numbers that are independent
of Prometheus as a cross-check.

Usage:
    python3 scripts/load_test.py --duration 120 --rps 5 --label baseline

Run each stage long enough for several Prometheus scrapes. At the default
5s scrape interval, 120s gives ~24 scrapes - comfortably enough to see a
clear plateau in Grafana rather than a single spike.
"""
import argparse
import json
import os
import random
import statistics
import sys
import time
import uuid
import urllib.error
import urllib.request

# 127.0.0.1 rather than "localhost": on some systems localhost resolves to
# ::1 (IPv6) first, while uvicorn may only be listening on IPv4.
BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")


def request(method, path, body=None, headers=None, form=False):
    url = f"{BASE}{path}"
    headers = dict(headers or {})
    data = None
    if body is not None:
        if form:
            data = "&".join(f"{k}={v}" for k, v in body.items()).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode() or "{}")
            return resp.status, payload, time.perf_counter() - start
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode() or "{}")
        except Exception:
            payload = {}
        return e.code, payload, time.perf_counter() - start
    except Exception as e:
        print(f"  request error: {e}", file=sys.stderr)
        return 0, {}, time.perf_counter() - start


def _login(name, email, phone):
    request("POST", "/auth/signup", {"name": name, "email": email, "phone": phone, "password": "loadtest123"})
    status, body, _ = request("POST", "/auth/login", {"username": email, "password": "loadtest123"}, form=True)
    if status != 200:
        print(f"Could not log in as {email} (status {status}). Is the app running?")
        sys.exit(1)
    return {"Authorization": f"Bearer {body['access_token']}"}


def ensure_users(n_senders):
    """Create the test users if missing (safe to re-run: a duplicate signup is a 409 and ignored).

    Assignment 2 added a per-user rate limit on POST /transfers (default 20 per 60 s). One sender
    at ~3 transfers/s would trip it, so the load is spread over a pool of senders - exactly what
    many real users would look like to the limiter.
    """
    senders = []
    for i in range(n_senders):
        h = _login(f"Load Sender {i}", f"load-sender-{i}@example.com", f"+9231100{i:04d}")
        request("POST", "/wallet/topups", {"amount": 500000}, headers=h)  # enough for the whole run
        senders.append(h)
    bob = _login("Load Bob", "load-bob@example.com", "+92311000002")
    return senders, bob


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=120, help="seconds to run")
    ap.add_argument("--rps", type=float, default=5, help="requests per second")
    ap.add_argument("--senders", type=int, default=12, help="distinct sender accounts (keeps each under the rate limit)")
    ap.add_argument("--label", default="run", help="name for this stage, e.g. baseline/fault/recovery")
    args = ap.parse_args()

    senders, bob = ensure_users(args.senders)

    interval = 1.0 / args.rps
    deadline = time.time() + args.duration
    latencies = []
    statuses = {}

    print(f"[{args.label}] sending ~{args.rps} rps for {args.duration}s "
          f"(~{int(args.rps * args.duration)} requests)")
    started = time.time()

    while time.time() < deadline:
        cycle_start = time.perf_counter()

        # A weighted mix so the dashboard shows several endpoints, with
        # transfers dominating because that is the business-critical path.
        roll = random.random()
        if roll < 0.55:
            status, _, elapsed = request(
                "POST", "/transfers",
                {"recipient": "load-bob@example.com", "amount": random.choice([50, 100, 250, 500, 1200, 3000])},
                headers={**random.choice(senders), "Idempotency-Key": str(uuid.uuid4())})
        elif roll < 0.75:
            status, _, elapsed = request("GET", "/wallet/balance", headers=random.choice(senders))
        elif roll < 0.90:
            status, _, elapsed = request("GET", "/transactions", headers=bob)
        else:
            status, _, elapsed = request("GET", "/health")

        latencies.append(elapsed)
        statuses[status] = statuses.get(status, 0) + 1

        sleep_for = interval - (time.perf_counter() - cycle_start)
        if sleep_for > 0:
            time.sleep(sleep_for)

    actual = time.time() - started
    latencies.sort()

    def pct(p):
        if not latencies:
            return 0.0
        idx = min(int(len(latencies) * p / 100), len(latencies) - 1)
        return latencies[idx]

    print(f"\n[{args.label}] results over {actual:.1f}s")
    print(f"  requests:      {len(latencies)}")
    print(f"  status codes:  {dict(sorted(statuses.items()))}")
    print(f"  mean latency:  {statistics.mean(latencies)*1000:.1f} ms")
    print(f"  median (p50):  {pct(50)*1000:.1f} ms")
    print(f"  p95:           {pct(95)*1000:.1f} ms")
    print(f"  p99:           {pct(99)*1000:.1f} ms")
    print(f"  max:           {max(latencies)*1000:.1f} ms")
    print(f"\n  Record this window in your report: "
          f"{time.strftime('%H:%M:%S', time.localtime(started))} - "
          f"{time.strftime('%H:%M:%S', time.localtime(time.time()))}")


if __name__ == "__main__":
    main()
