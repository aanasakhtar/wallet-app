"""
Assignment 2, Part B: the gRPC CreateTransfer service. Run: python3 test_grpc.py

The gRPC server is started in-process on a free port; the HTTP app runs in the same process
against the SAME database file, which is exactly the "same source of truth" requirement.
"""
import json
import os
import time
import uuid

os.environ.update(DATABASE_URL="sqlite:///./test_grpc.db", LOG_DIR="test_grpc_logs", RATE_LIMIT_ENABLED="false")
if os.path.exists("test_grpc.db"):
    os.remove("test_grpc.db")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.services import transfer_service  # noqa: E402
from clients.grpc_client import create_transfer  # noqa: E402
from grpc_service.server import build_server  # noqa: E402

http = TestClient(app)
server, port = build_server("127.0.0.1:0")
server.start()
TARGET = f"127.0.0.1:{port}"


def expect(cond, msg):
    if not cond:
        raise AssertionError(f"FAILED: {msg}")
    print(f"OK: {msg}")


def signup(name, email, phone):
    http.post("/auth/signup", json={"name": name, "email": email, "phone": phone, "password": "password123"})
    return http.post("/auth/login", data={"username": email, "password": "password123"}).json()["access_token"]


def bal(token):
    return http.get("/wallet/balance", headers={"Authorization": f"Bearer {token}"}).json()["balance"]


alice, bob = signup("Alice", "alice@example.com", "+923000000001"), signup("Bob", "bob@example.com", "+923000000002")
http.post("/wallet/topups", json={"amount": 1000}, headers={"Authorization": f"Bearer {alice}"})

print("--- success ---")
rid = str(uuid.uuid4())
r = create_transfer(TARGET, alice, "bob@example.com", 100, request_id=rid)
expect(r["ok"] and r["status"] == "OK" and r["response"]["status"] == "completed", f"CreateTransfer -> OK ({r['response']['transaction_id'][:8]}...)")
expect(bal(alice) == 900.0 and bal(bob) == 100.0, "HTTP GET /wallet/balance sees the gRPC transfer (same database)")
txid = r["response"]["transaction_id"]
expect(http.get(f"/transfers/{txid}", headers={"Authorization": f"Bearer {alice}"}).status_code == 200,
       "the transfer is readable through the HTTP API")

print("\n--- failures: gRPC status + machine code (HTTP equivalent) ---")
cases = [
    ("insufficient balance", dict(token=bob, recipient="alice@example.com", amount=99999), "FAILED_PRECONDITION", "insufficient_balance"),
    ("unknown recipient", dict(token=alice, recipient="nobody@example.com", amount=5), "NOT_FOUND", "recipient_not_found"),
    ("self transfer", dict(token=alice, recipient="alice@example.com", amount=5), "INVALID_ARGUMENT", "self_transfer"),
    ("amount with 3 decimals", dict(token=alice, recipient="bob@example.com", amount=1.234), "INVALID_ARGUMENT", "validation_error"),
    ("amount = 0", dict(token=alice, recipient="bob@example.com", amount=0), "INVALID_ARGUMENT", "validation_error"),
    ("no token", dict(token=None, recipient="bob@example.com", amount=5), "UNAUTHENTICATED", "unauthenticated"),
    ("garbage token", dict(token="not-a-jwt", recipient="bob@example.com", amount=5), "UNAUTHENTICATED", "unauthenticated"),
]
for name, kw, status, code in cases:
    r = create_transfer(TARGET, **kw)
    expect(not r["ok"] and r["status"] == status and r["error_code"] == code, f"{name} -> {status} / {code}")
r = create_transfer(TARGET, alice, "bob@example.com", 5, idempotency_key="short")
expect(r["status"] == "INVALID_ARGUMENT", "malformed idempotency_key -> INVALID_ARGUMENT")
expect(bal(alice) == 900.0, "no failed call moved money")

print("\n--- same business rules: idempotency works ACROSS the two protocols ---")
key = str(uuid.uuid4())
h = http.post("/transfers", json={"recipient": "bob@example.com", "amount": 40},
              headers={"Authorization": f"Bearer {alice}", "Idempotency-Key": key})
g = create_transfer(TARGET, alice, "bob@example.com", 40, idempotency_key=key)
expect(h.status_code == 201 and g["ok"] and g["replayed"] and g["response"]["transaction_id"] == h.json()["id"],
       "HTTP created the transfer, the gRPC call with the same key got it back (replayed=true)")
expect(bal(alice) == 860.0, "money moved once")
g2 = create_transfer(TARGET, alice, "bob@example.com", 41, idempotency_key=key)
expect(g2["status"] == "INVALID_ARGUMENT" and g2["error_code"] == "idempotency_key_reuse", "same key + different amount -> INVALID_ARGUMENT")

print("\n--- request-id correlation across the gRPC boundary ---")
rid = str(uuid.uuid4())
create_transfer(TARGET, alice, "bob@example.com", 3, request_id=rid)
create_transfer(TARGET, bob, "alice@example.com", 99999, request_id=rid + "-fail")
time.sleep(0.2)
lines = [json.loads(l) for l in open(os.path.join("test_grpc_logs", "wallet-app.json"), encoding="utf-8") if l.strip()]
mine = [l for l in lines if l["request_id"] == rid]
events = {l.get("event") for l in mine}
expect({"transfer_completed", "grpc_call"} <= events, f"client-chosen id appears on the business log AND the RPC log: {sorted(events)}")
fail = [l for l in lines if l["request_id"] == rid + "-fail"]
expect(any(l.get("event") == "transfer_failed" for l in fail), "failed RPC is traceable by its id too")
expect(any(l.get("event") == "grpc_call" and l["grpc_status"] == "FAILED_PRECONDITION" for l in fail), "RPC log records the gRPC status")

print("\n--- deadline ---")
orig = transfer_service.create_transfer
transfer_service.create_transfer = lambda *a, **k: (time.sleep(0.6), orig(*a, **k))[1]
r = create_transfer(TARGET, alice, "bob@example.com", 1, timeout=0.15)
transfer_service.create_transfer = orig
expect(r["status"] == "DEADLINE_EXCEEDED", "a 150 ms deadline against a 600 ms handler -> DEADLINE_EXCEEDED")

print("\n--- HTTP and gRPC agree on every business error (single mapping table) ---")
from app import errors  # noqa: E402
for cls in (errors.InsufficientBalance, errors.RecipientNotFound, errors.SelfTransfer, errors.IdempotencyKeyReuse):
    print(f"   {cls.code:<22} HTTP {cls.http_status}  <->  gRPC {cls.grpc_status}")
server.stop(0)
print("\nAll gRPC tests passed.")
