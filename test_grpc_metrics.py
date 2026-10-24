"""
Assignment 2, Part C: the new metrics. Run: python3 test_grpc_metrics.py
  - per-RPC counter by method + status, duration histogram, in-flight gauge
  - labels are bounded (no user / request / trace ids)
  - DB-derived gauges are read at scrape time (the audit fix)
"""
import os
import re
import threading
import time
import uuid

os.environ.update(DATABASE_URL="sqlite:///./test_gm.db", LOG_DIR="test_gm_logs", RATE_LIMIT_ENABLED="false")
if os.path.exists("test_gm.db"):
    os.remove("test_gm.db")

from fastapi.testclient import TestClient  # noqa: E402
from prometheus_client import REGISTRY, generate_latest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.services import transfer_service  # noqa: E402
from clients.grpc_client import create_transfer  # noqa: E402
from grpc_service.server import build_server  # noqa: E402

http = TestClient(app)
server, port = build_server("127.0.0.1:0")
server.start()
T = f"127.0.0.1:{port}"
M = "/wallet.v1.TransferService/CreateTransfer"


def expect(c, m):
    if not c:
        raise AssertionError(f"FAILED: {m}")
    print(f"OK: {m}")


def sv(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def signup(n, e, p):
    http.post("/auth/signup", json={"name": n, "email": e, "phone": p, "password": "password123"})
    return http.post("/auth/login", data={"username": e, "password": "password123"}).json()["access_token"]


a, b = signup("A", "a@x.com", "+923000000001"), signup("B", "b@x.com", "+923000000002")
http.post("/wallet/topups", json={"amount": 1000}, headers={"Authorization": f"Bearer {a}"})

print("--- calls by method and status ---")
for _ in range(3):
    create_transfer(T, a, "b@x.com", 10)
for _ in range(2):
    create_transfer(T, b, "a@x.com", 99999)          # insufficient balance
create_transfer(T, a, "nobody@x.com", 1)              # unknown recipient
create_transfer(T, a, "b@x.com", 1.234)               # validation
create_transfer(T, None, "b@x.com", 1)                # no token
for code, n in (("OK", 3), ("FAILED_PRECONDITION", 2), ("NOT_FOUND", 1), ("INVALID_ARGUMENT", 1), ("UNAUTHENTICATED", 1)):
    expect(sv("wallet_grpc_server_handled_total", grpc_method=M, grpc_code=code) == n, f"handled_total{{{code}}} == {n}")

print("\n--- duration histogram ---")
cnt = sv("wallet_grpc_server_handling_seconds_count", grpc_method=M)
expect(cnt == 8, f"histogram _count == number of calls (8)")
expect(sv("wallet_grpc_server_handling_seconds_bucket", grpc_method=M, le="+Inf") == cnt, "+Inf bucket == _count")
expect(sv("wallet_grpc_server_handling_seconds_bucket", grpc_method=M, le="5.0") == cnt, "every call finished under 5 s")
expect(0 < sv("wallet_grpc_server_handling_seconds_sum", grpc_method=M) < 8, "_sum is positive and plausible")

print("\n--- in-flight gauge ---")
expect(sv("wallet_grpc_server_in_flight", grpc_method=M) == 0, "0 in flight when idle")
orig = transfer_service.create_transfer
transfer_service.create_transfer = lambda *x, **k: (time.sleep(0.6), orig(*x, **k))[1]
th = threading.Thread(target=lambda: create_transfer(T, a, "b@x.com", 1, timeout=5))
th.start(); time.sleep(0.25)
expect(sv("wallet_grpc_server_in_flight", grpc_method=M) == 1, "1 in flight while a slow call is being handled")
th.join(); time.sleep(0.1)
expect(sv("wallet_grpc_server_in_flight", grpc_method=M) == 0, "back to 0 afterwards")

print("\n--- client deadline: what the server records ---")
before = sv("wallet_grpc_server_handled_total", grpc_method=M, grpc_code="CANCELLED")
r = create_transfer(T, a, "b@x.com", 1, timeout=0.15)
time.sleep(0.8)
transfer_service.create_transfer = orig
expect(r["status"] == "DEADLINE_EXCEEDED", "client sees DEADLINE_EXCEEDED")
expect(sv("wallet_grpc_server_handled_total", grpc_method=M, grpc_code="CANCELLED") == before + 1,
       "server records the abandoned call as CANCELLED (it cannot see the client's DEADLINE_EXCEEDED)")

print("\n--- labels are bounded ---")
fam = [f for f in REGISTRY.collect() if f.name.startswith("wallet_grpc_server")]
keys, methods = set(), set()
for f in fam:
    for s in f.samples:
        keys |= set(s.labels)
        if "grpc_method" in s.labels:
            methods.add(s.labels["grpc_method"])
expect(keys <= {"grpc_method", "grpc_code", "le"}, f"only grpc_method / grpc_code / le labels exist {sorted(keys)}")
expect(methods == {M}, f"grpc_method has exactly the registered RPC(s): {methods}")
uuid_like = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}")
expect(not uuid_like.search(generate_latest().decode().split("wallet_grpc_server_handled_total")[1][:2000]),
       "no id-looking label values in the gRPC series")

print("\n--- DB-derived gauges are read at scrape time (audit fix) ---")
def gauge(n):
    return REGISTRY.get_sample_value(n)
m0 = gauge("wallet_money_in_system_pkr")
db = SessionLocal()   # simulates ANOTHER process changing the database behind this process's back
db.execute(text("UPDATE wallets SET balance = balance + 500 WHERE user_id = (SELECT id FROM users WHERE email='a@x.com')"))
db.commit(); db.close()
expect(gauge("wallet_money_in_system_pkr") == m0 + 500, f"gauge saw the out-of-process change immediately ({m0} -> {m0 + 500})")
expect(gauge("wallet_registered_users") == 2, "registered_users read from the DB")
server.stop(0)
print("\nAll gRPC metrics tests passed.")
