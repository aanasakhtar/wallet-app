"""Tests for the Python caller (timeout, retry+backoff, circuit breaker). Run: python3 test_resilient_client.py
Uses a local stub HTTP server, so no wallet app is needed."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clients.resilient_client import (ApiError, CircuitBreaker, CircuitOpenError, RateLimitedError,
                                      ServiceUnavailable, WalletClient)

STATE = {"script": [], "hits": [], "delay": 0.0}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _serve(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        STATE["hits"].append((self.command, self.path, self.headers.get("Idempotency-Key")))
        time.sleep(STATE["delay"])
        status, headers = STATE["script"].pop(0) if STATE["script"] else (201, {})
        body = json.dumps({"id": "t1"} if status < 300 else {"error": {"code": "x", "message": "m", "request_id": "r"}}).encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass  # client already timed out

    do_GET = do_POST = _serve


srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}"


def expect(cond, msg):
    if not cond:
        raise AssertionError(f"FAILED: {msg}")
    print(f"OK: {msg}")


def run(script, delay=0.0):
    STATE.update(script=list(script), hits=[], delay=delay)


class Clock:
    t = 0.0
    def __call__(self): return self.t


def client(clock=None, **kw):
    br = CircuitBreaker(3, 5.0, clock=clock or time.monotonic)
    return WalletClient(URL, "tok", timeout=kw.pop("timeout", 1.0), breaker=br, sleep=lambda s: None, **kw)


print("--- retry with backoff ---")
run([(503, {}), (201, {})])
c = client(); r = c.transfer("b@example.com", 5, idempotency_key="key-123456")
expect(r["id"] == "t1" and len(STATE["hits"]) == 2, "503 then 201: the call succeeds on the 2nd attempt")
expect({h[2] for h in STATE["hits"]} == {"key-123456"}, "every retry reuses the SAME Idempotency-Key")
run([(503, {})] * 5)
try:
    client(max_retries=2).transfer("b@example.com", 5); expect(False, "should fail")
except ServiceUnavailable:
    expect(len(STATE["hits"]) == 3, "retries are limited: 1 try + 2 retries = 3 requests, then give up")
run([(503, {})] * 3)
try:
    client()._call("POST", "/transfers", {"x": 1}, None, protected=False)
except ServiceUnavailable:
    expect(len(STATE["hits"]) == 1, "a POST WITHOUT an idempotency key is NOT retried (not safe)")
run([(503, {}), (200, {})])
expect(client().balance()["id"] == "t1" and len(STATE["hits"]) == 2, "a GET (safe) is retried")

print("\n--- what is NOT a failure ---")
run([(409, {})] * 3); c = client()
try:
    c.transfer("b@example.com", 5)
except ApiError as e:
    expect(e.status == 409 and len(STATE["hits"]) == 1 and c.breaker.failures == 0, "4xx: no retry, breaker untouched")
run([(429, {"Retry-After": "7"})]); c = client()
try:
    c.transfer("b@example.com", 5)
except RateLimitedError as e:
    expect(e.retry_after == 7 and len(STATE["hits"]) == 1 and c.breaker.failures == 0, "429: surfaces Retry-After=7, no retry, breaker untouched")

print("\n--- timeout ---")
run([], delay=0.4)
t0 = time.time()
try:
    client(timeout=0.1, max_retries=1).transfer("b@example.com", 5); expect(False, "should time out")
except ServiceUnavailable as e:
    expect("timeout" in str(e) and time.time() - t0 < 1.0, f"slow server -> timeout after 0.1s per attempt ({e})")
time.sleep(0.6)

print("\n--- circuit breaker: closed -> open -> half-open -> closed ---")
clk = Clock(); run([(503, {})] * 50); c = client(clk, max_retries=0)
for i in range(3):
    try: c.transfer("b@example.com", 1)
    except ServiceUnavailable: pass
expect(c.breaker.state == "OPEN", "3 consecutive failed calls open the breaker")
hits = len(STATE["hits"])
try:
    c.transfer("b@example.com", 1); expect(False, "must be blocked")
except CircuitOpenError:
    expect(len(STATE["hits"]) == hits, "while OPEN the call is rejected instantly with NO request sent")
clk.t = 4.9
try: c.balance(); expect(False, "still blocked")
except CircuitOpenError: expect(True, "still blocked just before the 5 s recovery period ends")
clk.t = 5.1; run([(503, {})])
try: c.balance()
except ServiceUnavailable: pass
expect(c.breaker.state == "OPEN", "half-open trial call fails -> straight back to OPEN")
clk.t = 11.0; run([(201, {})])
expect(c.balance()["id"] == "t1" and c.breaker.state == "CLOSED", "after recovery the trial call succeeds -> CLOSED again")
print("\nAll resilient-client tests passed.")
