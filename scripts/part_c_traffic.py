"""
Repeatable traffic for the Part C dashboard: the same mix of successes and failures through the
HTTP API and through gRPC.

    RATE_LIMIT_ENABLED=false uvicorn app.main:app      # so the 429 limiter does not distort the counts
    python -m grpc_service.server
    python scripts/part_c_traffic.py --rounds 40 --pause 0.25

Each ROUND sends, over HTTP and again over gRPC:
    2 new transfers (success)          HTTP 201            / gRPC OK
    1 replay of the first one          HTTP 201 (replayed) / gRPC OK (replayed=true)
    1 insufficient balance             HTTP 409            / gRPC FAILED_PRECONDITION
    1 unknown recipient                HTTP 404            / gRPC NOT_FOUND
    1 invalid amount (0)               HTTP 422            / gRPC INVALID_ARGUMENT
so N rounds give EXACTLY 3N, N, N, N calls per protocol. The script prints what it observed so the
numbers can be compared with Prometheus / Grafana (any difference, e.g. 429s, is shown).
"""
import argparse
import collections
import json
import os
import pathlib
import sys
import time
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
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=h, method=method), timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=40)
    ap.add_argument("--pause", type=float, default=0.25, help="seconds between rounds")
    a = ap.parse_args()

    uid = uuid.uuid4().hex[:6]
    n = int(uid, 16) % 10**5
    users = {}
    for name in ("sender1", "sender2", "poor", "payee"):
        email = f"pc-{name}-{uid}@example.com"
        http("POST", "/auth/signup", {"name": name, "email": email, "phone": f"+92361{n:05d}{len(users)}", "password": "password123"})
        users[name] = (email, http_login(BASE, email, "password123"))
    for name in ("sender1", "sender2"):
        http("POST", "/wallet/topups", {"amount": 1_000_000}, {"Authorization": f"Bearer {users[name][1]}"})
    payee, poor = users["payee"][0], users["poor"][1]

    seen_http, seen_grpc = collections.Counter(), collections.Counter()
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] sending {a.rounds} rounds ...", flush=True)
    for r in range(a.rounds):
        s1, s2 = users["sender1"][1], users["sender2"][1]
        # ---- HTTP ----
        k1, k2 = str(uuid.uuid4()), str(uuid.uuid4())
        post = lambda tok, body, key: http("POST", "/transfers", body, {"Authorization": f"Bearer {tok}", "Idempotency-Key": key})
        seen_http[post(s1, {"recipient": payee, "amount": 5}, k1)] += 1
        seen_http[post(s2, {"recipient": payee, "amount": 5}, k2)] += 1
        seen_http[post(s1, {"recipient": payee, "amount": 5}, k1)] += 1                       # replay
        seen_http[post(poor, {"recipient": payee, "amount": 50}, str(uuid.uuid4()))] += 1      # 409
        seen_http[post(s1, {"recipient": "nobody@example.com", "amount": 5}, str(uuid.uuid4()))] += 1  # 404
        seen_http[post(s1, {"recipient": payee, "amount": 0}, str(uuid.uuid4()))] += 1         # 422
        # ---- gRPC ----
        g1, g2 = str(uuid.uuid4()), str(uuid.uuid4())
        call = lambda tok, rcpt, amt, key: create_transfer(TARGET, tok, rcpt, amt, idempotency_key=key)["status"]
        seen_grpc[call(s1, payee, 5, g1)] += 1
        seen_grpc[call(s2, payee, 5, g2)] += 1
        seen_grpc[call(s1, payee, 5, g1)] += 1                                                 # replay
        seen_grpc[call(poor, payee, 50, str(uuid.uuid4()))] += 1
        seen_grpc[call(s1, "nobody@example.com", 5, str(uuid.uuid4()))] += 1
        seen_grpc[call(s1, payee, 0, str(uuid.uuid4()))] += 1
        time.sleep(a.pause)

    print(f"[{time.strftime('%H:%M:%S')}] done in {time.time() - t0:.1f}s")
    print(f"HTTP  POST /transfers observed: {dict(sorted(seen_http.items()))}")
    print(f"gRPC  CreateTransfer  observed: {dict(sorted(seen_grpc.items()))}")
    N = a.rounds
    print(f"expected per protocol: success/replay {3*N}, rejected-by-balance {N}, not-found {N}, invalid {N}")


if __name__ == "__main__":
    main()
