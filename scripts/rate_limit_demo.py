"""
Shows the rate limit on POST /transfers: accepted requests, then 429 + Retry-After, then accepted again.

    RATE_LIMIT_TRANSFERS=5/10 uvicorn app.main:app      # small limit so the demo is short
    python scripts/rate_limit_demo.py
"""
import json
import os
import time
import urllib.error
import urllib.request
import uuid

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")


def call(method, path, body=None, headers=None, form=False):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = ("&".join(f"{k}={v}" for k, v in body.items()).encode() if form else json.dumps(body).encode())
        h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=h, method=method), timeout=10) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, json.loads(e.read() or b"{}")


uid = uuid.uuid4().hex[:6]
for n in ("rl-a", "rl-b"):
    call("POST", "/auth/signup", {"name": n, "email": f"{n}-{uid}@example.com", "phone": f"+9234{int(uid,16)%10**6:06d}{1 if n=='rl-a' else 2}", "password": "password123"})
tok = call("POST", "/auth/login", {"username": f"rl-a-{uid}@example.com", "password": "password123"}, form=True)[2]["access_token"]
auth = {"Authorization": f"Bearer {tok}"}
call("POST", "/wallet/topups", {"amount": 1000}, auth)

sent = 0
while True:
    sent += 1
    t = time.strftime("%H:%M:%S")
    status, hdr, body = call("POST", "/transfers", {"recipient": f"rl-b-{uid}@example.com", "amount": 1},
                             {**auth, "Idempotency-Key": str(uuid.uuid4())})
    rl = {k: v for k, v in hdr.items() if k.lower().startswith(("ratelimit", "retry-after"))}
    print(f"[{t}] request {sent:>2} -> {status}  {rl}")
    if status == 429:
        print(f"          body: {json.dumps(body['error'])}")
        wait = int(hdr["retry-after"])
        break
print(f"\nWaiting Retry-After = {wait}s ...")
time.sleep(wait + 0.2)
status, hdr, _ = call("POST", "/transfers", {"recipient": f"rl-b-{uid}@example.com", "amount": 1},
                      {**auth, "Idempotency-Key": str(uuid.uuid4())})
print(f"[{time.strftime('%H:%M:%S')}] request {sent + 1:>2} -> {status}  (accepted again)")
