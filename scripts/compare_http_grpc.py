"""
Runs the SAME two requests (one success, one failure) through the HTTP API and through gRPC
and prints both answers side by side.    python scripts/compare_http_grpc.py
(needs: HTTP app on :8000 and the gRPC server on :50051, both on the same database)
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from clients.grpc_client import create_transfer, http_login  # noqa: E402

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")
TARGET = os.getenv("GRPC_TARGET", "127.0.0.1:50051")


def http(method, path, body=None, headers=None):
    h = dict(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=h, method=method), timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


uid = uuid.uuid4().hex[:6]
for n, i in (("cmp-a", 1), ("cmp-b", 2)):
    http("POST", "/auth/signup", {"name": n, "email": f"{n}-{uid}@example.com", "phone": f"+9235{int(uid,16)%10**6:06d}{i}", "password": "password123"})
tok = http_login(BASE, f"cmp-a-{uid}@example.com", "password123")
auth = {"Authorization": f"Bearer {tok}"}
http("POST", "/wallet/topups", {"amount": 500}, auth)
bob = f"cmp-b-{uid}@example.com"

print("=== SUCCESS: send 100 to a valid recipient ===")
s, b = http("POST", "/transfers", {"recipient": bob, "amount": 100}, {**auth, "Idempotency-Key": str(uuid.uuid4())})
print(f"HTTP  POST /transfers      -> {s} Created   transaction_id={b['id']}  status={b['status']}")
g = create_transfer(TARGET, tok, bob, 100)
print(f"gRPC  CreateTransfer       -> {g['status']}        transaction_id={g['response']['transaction_id']}  status={g['response']['status']}")

print("\n=== FAILURE: send 100000 with a balance of 300 ===")
s, b = http("POST", "/transfers", {"recipient": bob, "amount": 100000}, {**auth, "Idempotency-Key": str(uuid.uuid4())})
print(f"HTTP  POST /transfers      -> {s}  {json.dumps(b['error'])}")
g = create_transfer(TARGET, tok, bob, 100000)
print(f"gRPC  CreateTransfer       -> {g['status']}  message={g['message']!r}  x-error-code={g['error_code']}")

print("\n=== same idempotency key through BOTH protocols ===")
key = str(uuid.uuid4())
s, b = http("POST", "/transfers", {"recipient": bob, "amount": 7}, {**auth, "Idempotency-Key": key})
g = create_transfer(TARGET, tok, bob, 7, idempotency_key=key)
print(f"HTTP  created transfer {b['id']}  |  gRPC returned {g['response']['transaction_id']} replayed={g['replayed']}")
