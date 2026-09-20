"""
Phase 2 verification: metrics endpoint, all four metric types, fault
injection, and JSON log validity. Run with: python3 test_observability.py
"""
import json
import os
import time

os.environ["DATABASE_URL"] = "sqlite:///./test_obs.db"
os.environ["LOG_DIR"] = "test_logs"
os.environ["ADMIN_TOKEN"] = "test-token"

for f in ("test_obs.db",):
    if os.path.exists(f):
        os.remove(f)
if os.path.isdir("test_logs"):
    for f in os.listdir("test_logs"):
        os.remove(os.path.join("test_logs", f))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
ADMIN = {"X-Admin-Token": "test-token"}


def expect(condition, message):
    if not condition:
        raise AssertionError(f"FAILED: {message}")
    print(f"OK: {message}")


def setup_users():
    client.post("/auth/signup", json={
        "name": "Alice", "email": "alice@example.com",
        "phone": "+92300000001", "password": "password123"})
    client.post("/auth/signup", json={
        "name": "Bob", "email": "bob@example.com",
        "phone": "+92300000002", "password": "password123"})

    def token(email):
        r = client.post("/auth/login", data={"username": email, "password": "password123"})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return token("alice@example.com"), token("bob@example.com")


def scrape():
    r = client.get("/metrics")
    assert r.status_code == 200
    return r.text


def main():
    alice, bob = setup_users()

    print("--- generate some traffic ---")
    client.post("/wallet/topup", json={"amount": 5000}, headers=alice)
    for amount in (100, 250, 1200):
        client.post("/transfers", json={"recipient": "bob@example.com", "amount": amount},
                    headers=alice)
    client.post("/transfers", json={"recipient": "bob@example.com", "amount": 999999},
                headers=alice)  # insufficient balance
    client.post("/auth/login", data={"username": "alice@example.com", "password": "wrong"})
    client.get("/wallet/balance", headers=bob)
    print("OK: traffic generated")

    print("\n--- /metrics endpoint ---")
    body = scrape()
    expect("# TYPE" in body, "/metrics returns Prometheus exposition format")

    print("\n--- all four metric types are present ---")
    expect("# TYPE wallet_http_requests_total counter" in body, "COUNTER present (http requests)")
    expect("# TYPE wallet_transactions_total counter" in body, "COUNTER present (transactions)")
    expect("# TYPE wallet_pending_transfers gauge" in body, "GAUGE present (pending transfers)")
    expect("# TYPE wallet_money_in_system_pkr gauge" in body, "GAUGE present (money in system)")
    expect("# TYPE wallet_http_request_duration_seconds histogram" in body,
           "HISTOGRAM present (request duration)")
    expect("# TYPE wallet_transfer_amount_pkr histogram" in body,
           "HISTOGRAM present (transfer amounts)")
    expect("# TYPE wallet_transfer_processing_seconds summary" in body,
           "SUMMARY present (transfer processing)")

    print("\n--- histogram exposes buckets for percentile queries ---")
    expect("wallet_http_request_duration_seconds_bucket" in body,
           "histogram exposes _bucket series (needed for histogram_quantile)")
    expect('le="0.5"' in body, "histogram has a 0.5s bucket boundary")

    print("\n--- summary exposes count and sum (average, not quantiles) ---")
    expect("wallet_transfer_processing_seconds_count" in body, "summary exposes _count")
    expect("wallet_transfer_processing_seconds_sum" in body, "summary exposes _sum")

    print("\n--- business metrics recorded real activity ---")
    expect('wallet_transactions_total{status="completed",type="topup"} 1.0' in body,
           "topup counter incremented exactly once")
    expect('wallet_transactions_total{status="completed",type="transfer"} 3.0' in body,
           "three completed transfers counted")
    expect('wallet_transactions_total{status="failed",type="transfer"} 1.0' in body,
           "one failed transfer counted")
    expect('wallet_transfer_rejections_total{reason="insufficient_balance"} 1.0' in body,
           "rejection reason recorded (self-explored metric)")
    expect('wallet_auth_failures_total{reason="bad_password"} 1.0' in body,
           "auth failure reason recorded (self-explored metric)")

    print("\n--- gauges reflect current state, not cumulative counts ---")
    expect("wallet_registered_users 2.0" in body, "registered_users gauge is 2")
    expect("wallet_pending_transfers 0.0" in body,
           "pending_transfers returned to 0 after all transfers settled")
    money_line = [l for l in body.splitlines()
                  if l.startswith("wallet_money_in_system_pkr ")][0]
    expect(float(money_line.split()[1]) == 5000.0,
           "money_in_system equals total topped up (money is conserved by transfers)")

    print("\n--- endpoint label uses route template, not raw path ---")
    expect('endpoint="/wallet/balance"' in body, "endpoint label is the route template")
    expect("/metrics" not in
           "".join(l for l in body.splitlines() if "wallet_http_requests_total{" in l),
           "/metrics excludes itself from request counts")

    print("\n--- fault injection: latency ---")
    r = client.get("/admin/faults", headers=ADMIN)
    expect(r.status_code == 200 and r.json()["latency_enabled"] is False,
           "faults are off by default")

    r = client.get("/admin/faults")
    expect(r.status_code == 401, "admin endpoint rejects a missing token")

    client.post("/admin/faults",
                json={"latency_enabled": True, "latency_ms": 300, "latency_every_n": 2},
                headers=ADMIN)

    start = time.perf_counter()
    for _ in range(4):
        client.get("/health")
    elapsed = time.perf_counter() - start
    expect(elapsed > 0.55,
           f"every-2nd-request delay measurably slowed 4 requests (took {elapsed:.2f}s)")

    body = scrape()
    expect('wallet_injected_faults_total{kind="latency"} 2.0' in body,
           "injected latency counter shows exactly 2 injections over 4 requests")

    print("\n--- fault injection: errors ---")
    client.post("/admin/faults/reset", headers=ADMIN)
    client.post("/admin/faults",
                json={"error_enabled": True, "error_every_n": 3}, headers=ADMIN)
    codes = [client.get("/health").status_code for _ in range(3)]
    expect(503 in codes, f"injected error produced a 503 (got {codes})")

    print("\n--- fault reset restores normal behaviour ---")
    client.post("/admin/faults/reset", headers=ADMIN)
    cfg = client.get("/admin/faults", headers=ADMIN).json()
    expect(cfg["latency_enabled"] is False and cfg["error_enabled"] is False,
           "reset turned every fault off")
    start = time.perf_counter()
    for _ in range(4):
        r = client.get("/health")
        assert r.status_code == 200
    expect(time.perf_counter() - start < 0.5, "requests are fast again after reset")

    print("\n--- log file is valid, parseable JSON ---")
    log_path = os.path.join("test_logs", "wallet-app.json")
    expect(os.path.exists(log_path), "log file was created")

    with open(log_path, encoding="utf-8") as fh:
        lines = [l for l in fh.read().splitlines() if l.strip()]
    expect(len(lines) > 0, f"log file has {len(lines)} lines")

    parsed = []
    for i, line in enumerate(lines):
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise AssertionError(f"line {i+1} is not valid JSON: {e}\n{line}")
    expect(True, f"all {len(parsed)} log lines parse as JSON")

    required = {"@timestamp", "service", "level", "message", "request_id"}
    missing = [p for p in parsed if not required.issubset(p.keys())]
    expect(not missing, "every log line has timestamp, service, level, message, request_id")

    events = {p.get("event") for p in parsed}
    for ev in ("user_signup", "topup_completed", "transfer_completed",
               "transfer_failed", "login_failed"):
        expect(ev in events, f"log contains event={ev}")

    print("\n--- request_id correlates logs to a single request ---")
    r = client.post("/transfers", json={"recipient": "bob@example.com", "amount": 25},
                    headers=alice)
    rid = r.headers["x-request-id"]
    with open(log_path, encoding="utf-8") as fh:
        matched = [json.loads(l) for l in fh if l.strip() and json.loads(l).get("request_id") == rid]
    expect(len(matched) >= 1,
           f"log lines can be found by the X-Request-ID returned to the client ({rid})")

    print("\n--- secrets never reach the logs ---")
    raw = open(log_path, encoding="utf-8").read()
    expect("password123" not in raw, "plaintext password never appears in logs")
    expect("$2b$" not in raw, "bcrypt hash never appears in logs")
    for p in parsed:
        for k in ("password", "token", "access_token"):
            if k in p:
                expect(p[k] == "[REDACTED]", f"{k} is redacted when passed as an extra")

    print("\nAll observability tests passed.")


if __name__ == "__main__":
    main()
