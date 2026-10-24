"""
Assignment 2, Part A: HTTP API behaviour. Run: python3 test_api.py
Covers, for the "send money" task: a valid request, invalid input, a repeated request,
the consistent error format, and the rate limit (limit shrunk to 5 per 3 s for the test).
"""
import os
import threading
import time
import uuid

os.environ.update(DATABASE_URL="sqlite:///./test_api.db", LOG_DIR="test_api_logs",
                  RATE_LIMIT_TRANSFERS="5/3", ADMIN_TOKEN="test-token")
for f in ("test_api.db",):
    if os.path.exists(f):
        os.remove(f)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.rate_limit import transfer_limiter  # noqa: E402
from app.services import transfer_service  # noqa: E402

client = TestClient(app)


def expect(cond, msg):
    if not cond:
        raise AssertionError(f"FAILED: {msg}")
    print(f"OK: {msg}")


def signup(name, email, phone):
    r = client.post("/auth/signup", json={"name": name, "email": email, "phone": phone, "password": "password123"})
    assert r.status_code == 201, r.text
    t = client.post("/auth/login", data={"username": email, "password": "password123"}).json()["access_token"]
    return {"Authorization": f"Bearer {t}"}


def send(headers, recipient, amount, key=None):
    return client.post("/transfers", json={"recipient": recipient, "amount": amount},
                       headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())})


def balance(h):
    return client.get("/wallet/balance", headers=h).json()["balance"]


def is_envelope(r, code, status):
    body = r.json()
    e = body.get("error", {})
    return (r.status_code == status and e.get("code") == code and e.get("message")
            and e.get("request_id") == r.headers.get("x-request-id"))


alice = signup("Alice", "alice@example.com", "+923000000001")
bob = signup("Bob", "bob@example.com", "+923000000002")
carol = signup("Carol", "carol@example.com", "+923000000003")
client.post("/wallet/topups", json={"amount": 1000}, headers=alice)

print("--- VALID request ---")
r = send(alice, "bob@example.com", 100)
expect(r.status_code == 201 and r.json()["status"] == "completed", "POST /transfers returns 201 Created")
loc = r.headers.get("location", "")
expect(loc == f"/transfers/{r.json()['id']}", f"Location header points at the new resource ({loc})")
expect(client.get(loc, headers=alice).json()["id"] == r.json()["id"], "GET on the Location URL returns the transfer")
expect(client.get(loc, headers=bob).status_code == 200, "the recipient can read it too")
expect(client.get(loc, headers=carol).status_code == 404, "an unrelated user gets 404, not 403 (existence not leaked)")
expect(balance(alice) == 900.0 and balance(bob) == 100.0, "balances moved 900 / 100")
expect(r.headers.get("ratelimit-limit") == "5", "RateLimit-Limit header is present")

print("\n--- INVALID input -> 422 with the shared error envelope ---")
transfer_limiter.reset()
cases = {
    "amount = 0": ({"recipient": "bob@example.com", "amount": 0}, True),
    "negative amount": ({"recipient": "bob@example.com", "amount": -5}, True),
    "3 decimal places": ({"recipient": "bob@example.com", "amount": 1.234}, True),
    "above the maximum": ({"recipient": "bob@example.com", "amount": 5_000_000}, True),
    "missing recipient": ({"amount": 10}, True),
    "unknown field (typo)": ({"recipient": "bob@example.com", "amount": 10, "ammount": 1}, True),
    "wrong type": ({"recipient": "bob@example.com", "amount": "ten"}, True),
}
for name, (body, with_key) in cases.items():
    transfer_limiter.reset()  # invalid calls count against the rate limit too, so isolate each case
    r = client.post("/transfers", json=body, headers={**alice, "Idempotency-Key": str(uuid.uuid4())})
    expect(is_envelope(r, "validation_error", 422), f"{name} -> 422 validation_error")
transfer_limiter.reset()
r = client.post("/transfers", json={"recipient": "bob@example.com", "amount": 10}, headers=alice)
expect(is_envelope(r, "validation_error", 422) and "Idempotency-Key" in r.json()["error"]["message"],
       "missing Idempotency-Key header -> 422 naming the header")
r = client.post("/transfers", json={"recipient": "bob@example.com", "amount": 10},
                headers={**alice, "Idempotency-Key": "short"})
expect(is_envelope(r, "validation_error", 422), "malformed Idempotency-Key -> 422")
expect(balance(alice) == 900.0, "no invalid request moved any money")

print("\n--- business-rule errors map to distinct statuses ---")
transfer_limiter.reset()
expect(is_envelope(send(alice, "nobody@example.com", 10), "recipient_not_found", 404), "unknown recipient -> 404")
expect(is_envelope(send(alice, "alice@example.com", 10), "self_transfer", 422), "self transfer -> 422")
expect(is_envelope(send(bob, "alice@example.com", 5000), "insufficient_balance", 409), "insufficient balance -> 409")
r = client.get("/wallet/balance")
expect(is_envelope(r, "unauthenticated", 401) and "www-authenticate" in r.headers, "no token -> 401 + WWW-Authenticate")
expect(is_envelope(client.get("/nope"), "not_found", 404), "unknown route -> 404 in the same envelope")
expect(is_envelope(client.delete("/transfers"), "method_not_allowed", 405), "wrong method -> 405 in the same envelope")
expect(is_envelope(client.post("/auth/signup", json={"name": "X", "email": "carol@example.com", "password": "password123"}),
                   "email_already_registered", 409), "duplicate email -> 409")
expect(is_envelope(client.post("/auth/signup", json={"name": "X", "email": "x@example.com", "phone": "+923000000003",
                                                      "password": "password123"}),
                   "phone_already_registered", 409), "duplicate phone -> 409 (used to be an unhandled 500)")

print("\n--- REPEATED request (idempotency) ---")
transfer_limiter.reset()
key = str(uuid.uuid4())
r1 = send(alice, "bob@example.com", 50, key)
r2 = send(alice, "bob@example.com", 50, key)
expect(r1.status_code == r2.status_code == 201 and r1.json()["id"] == r2.json()["id"], "same key + body -> same transfer returned")
expect(r2.headers.get("idempotent-replayed") == "true" and "idempotent-replayed" not in r1.headers, "replay is flagged with Idempotent-Replayed: true")
expect(balance(alice) == 850.0 and balance(bob) == 150.0, "money moved exactly once (850 / 150)")
r3 = send(alice, "bob@example.com", 60, key)
expect(is_envelope(r3, "idempotency_key_reuse", 422), "same key with a different amount -> 422")
expect(send(bob, "alice@example.com", 1, key).status_code == 201, "keys are scoped per user: Bob can use the same string")
k2 = str(uuid.uuid4())
expect(send(carol, "alice@example.com", 20, k2).status_code == 409, "failed attempt (insufficient funds) -> 409 ...")
client.post("/wallet/topups", json={"amount": 100}, headers=carol)
expect(send(carol, "alice@example.com", 20, k2).status_code == 201, "... and does NOT consume the key: retry with it succeeds")

print("\n--- concurrent duplicates ---")
transfer_limiter.reset()
before = balance(alice)
ck, results = str(uuid.uuid4()), []
def worker():
    results.append(send(alice, "bob@example.com", 7, ck).status_code)
threads = [threading.Thread(target=worker) for _ in range(4)]
[t.start() for t in threads]; [t.join() for t in threads]
expect(results.count(201) == 4, f"4 simultaneous requests with one key all succeed ({results})")
expect(balance(alice) == before - 7, "... but only ONE transfer happened")

print("\n--- unexpected bug -> still the envelope, never a stack trace ---")
orig = transfer_service.create_transfer
transfer_service.create_transfer = lambda *a, **k: 1 / 0
transfer_limiter.reset()
r = send(alice, "bob@example.com", 1)
transfer_service.create_transfer = orig
expect(is_envelope(r, "internal_error", 500) and "division" not in r.text, "500 uses the envelope and leaks no internals")

print("\n--- pagination and filters on GET /transactions ---")
expect(len(client.get("/transactions?limit=1", headers=alice).json()) == 1, "limit=1 returns one row")
expect(is_envelope(client.get("/transactions?limit=0", headers=alice), "validation_error", 422), "limit=0 -> 422")
expect(is_envelope(client.get("/transactions?type=bogus", headers=alice), "validation_error", 422), "unknown type filter -> 422")
expect(all(t["type"] == "topup" for t in client.get("/transactions?type=topup", headers=alice).json()), "type=topup filter works")

print("\n--- RATE LIMIT: 5 per 3 s per user on POST /transfers ---")
transfer_limiter.reset()
codes = [send(alice, "bob@example.com", 1).status_code for _ in range(5)]
expect(codes == [201] * 5, f"first 5 requests accepted {codes}")
r = send(alice, "bob@example.com", 1)
ra = int(r.headers.get("retry-after", 0))
expect(is_envelope(r, "rate_limited", 429), "6th request -> 429 in the shared envelope")
expect(1 <= ra <= 3, f"Retry-After header present ({ra}s) and within the window")
expect(r.headers.get("ratelimit-remaining") == "0", "RateLimit-Remaining: 0")
expect(send(bob, "alice@example.com", 1).status_code == 201, "another user is NOT affected (limit is per user)")
expect(client.get("/transactions", headers=alice).status_code == 200, "other endpoints of the limited user are not limited")
time.sleep(ra + 0.2)
expect(send(alice, "bob@example.com", 1).status_code == 201, f"after Retry-After ({ra}s) the call is accepted again")
expect('wallet_rate_limit_rejections_total{endpoint="/transfers"} 1.0' in client.get("/metrics").text, "rejection is counted in a metric")
print("\nAll API tests passed.")
