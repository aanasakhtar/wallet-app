"""
Demonstrates the Python caller (clients/resilient_client.py) against the REAL wallet API.

Server failures are produced with the fault injector from Assignment 1 (/admin/faults), so
nothing is mocked:   python scripts/reliability_demo.py     (app must be running on :8000)

Stages: 1 healthy -> 2 transient failure (retry succeeds) -> 3 sustained outage (retries exhaust,
breaker OPENS, calls blocked) -> 4 recovery (half-open trial, CLOSED) -> 5 a timeout where the
request actually went through (retry is safe because of the Idempotency-Key).
"""
import json
import os
import sys
import time
import urllib.request
import uuid
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from clients.resilient_client import (CircuitBreaker, CircuitOpenError, ServiceUnavailable, WalletClient)  # noqa: E402

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")
ADMIN = {"X-Admin-Token": os.getenv("ADMIN_TOKEN", "local-admin-token"), "Content-Type": "application/json"}

# ---- the caller's settings (quoted in the report) ----
TIMEOUT, MAX_RETRIES, BACKOFF, THRESHOLD, RECOVERY = 1.0, 2, 0.25, 3, 5.0


def http(method, path, body=None, headers=None, form=False):
    data = None
    h = dict(headers or {})
    if body is not None:
        data = ("&".join(f"{k}={v}" for k, v in body.items()).encode() if form else json.dumps(body).encode())
        h.setdefault("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=h, method=method), timeout=30) as r:
        return json.loads(r.read() or b"{}")


def fault(**cfg):
    http("POST", "/admin/faults/reset" if not cfg else "/admin/faults", cfg or None, ADMIN)


def user(email, phone):
    try:
        http("POST", "/auth/signup", {"name": email.split("@")[0], "email": email, "phone": phone, "password": "password123"})
    except Exception:
        pass  # already exists
    return http("POST", "/auth/login", {"username": email, "password": "password123"}, form=True)["access_token"]


def stamp(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    uid = uuid.uuid4().hex[:6]
    tok = user(f"caller-{uid}@example.com", f"+9232{int(uid, 16) % 10**7:07d}")
    user(f"payee-{uid}@example.com", f"+9233{int(uid, 16) % 10**7:07d}")
    http("POST", "/wallet/topups", {"amount": 10000}, {"Authorization": f"Bearer {tok}"})
    fault()
    breaker = CircuitBreaker(failure_threshold=THRESHOLD, recovery_timeout=RECOVERY)
    c = WalletClient(BASE, tok, timeout=TIMEOUT, max_retries=MAX_RETRIES, backoff_base=BACKOFF, breaker=breaker,
                     log=lambda m: stamp(m))
    payee = f"payee-{uid}@example.com"
    bal = lambda: http("GET", "/wallet/balance", headers={"Authorization": f"Bearer {tok}"})["balance"]

    def call(label, fn):
        t0 = time.perf_counter()
        try:
            r = fn()
            stamp(f"{label}: SUCCESS  id={str(r.get('id', r.get('balance')))[:8]} replayed={r.get('_replayed')}  "
                  f"({(time.perf_counter()-t0)*1000:.0f} ms)  breaker={breaker.state}")
        except CircuitOpenError as e:
            stamp(f"{label}: BLOCKED  {e}  ({(time.perf_counter()-t0)*1000:.1f} ms, no request sent)  breaker={breaker.state}")
        except ServiceUnavailable as e:
            stamp(f"{label}: FAILED   {e}  breaker={breaker.state} (failed calls in a row: {breaker.failures})")

    print(f"Settings: timeout={TIMEOUT}s  max_retries={MAX_RETRIES}  backoff={BACKOFF}s*2^n  "
          f"breaker: open after {THRESHOLD} failed calls, recovery {RECOVERY}s\n")

    print("== 1. healthy service ==")
    for i in range(3):
        call(f"transfer #{i+1}", lambda: c.transfer(payee, 10))
    call("balance   ", c.balance)

    print("\n== 2. transient failure: every 2nd request returns 503, the retry succeeds ==")
    fault(error_enabled=True, error_every_n=2)
    http("GET", "/health")  # consume the 'good' slot so the transfer's first attempt is the failing one
    call("transfer  ", lambda: c.transfer(payee, 10))
    fault()

    print("\n== 3. sustained outage: EVERY request returns 503 ==")
    fault(error_enabled=True, error_every_n=1)
    for i in range(THRESHOLD):
        call(f"transfer (call {i+1})", lambda: c.transfer(payee, 10))
    print("-- breaker is now OPEN: further calls must not reach the network --")
    for i in range(3):
        call(f"transfer (blocked {i+1})", lambda: c.transfer(payee, 10))

    print("\n== 4. recovery ==")
    fault()
    stamp("fault removed; waiting for the recovery period...")
    time.sleep(RECOVERY + 0.3)
    call("trial call (half-open)", lambda: c.transfer(payee, 10))
    call("transfer  ", lambda: c.transfer(payee, 10))
    call("balance   ", c.balance)

    print("\n== 5. timeout that did NOT mean failure (why the Idempotency-Key matters) ==")
    before = bal()
    fault(latency_enabled=True, latency_ms=1500, latency_every_n=1)
    key = str(uuid.uuid4())
    stamp(f"balance before: {before}; sending 25 with key {key[:8]}... while the server takes 1.5s per request")
    call("transfer  ", lambda: c.transfer(payee, 25, idempotency_key=key))
    fault()
    time.sleep(1.5 * (MAX_RETRIES + 1) + 0.5)  # let the slow in-flight requests finish
    after = bal()
    stamp(f"balance after the timeouts: {after}  -> debited {before - after:g} (the client saw only errors)")
    call("retry, same key", lambda: c.transfer(payee, 25, idempotency_key=key))
    stamp(f"balance after the retry:    {bal()}  -> still debited exactly once")
    fault()


if __name__ == "__main__":
    main()
