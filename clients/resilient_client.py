"""
A small Python "caller": it behaves like ANOTHER SERVICE that depends on the wallet API.

Reliability features (all settings are constructor arguments):

  timeout            every HTTP attempt gives up after `timeout` seconds.
  retry with backoff transient failures (timeout, connection error, 502/503/504) are retried up to
                     `max_retries` times, sleeping backoff_base * 2**attempt (+ small jitter).
                     A call is retried ONLY if repeating it is safe: GET requests, or a POST
                     that carries an Idempotency-Key (the server then guarantees a repeat has
                     no second effect). The same key is reused for every attempt of one call.
  circuit breaker    after `failure_threshold` consecutive FAILED CALLS (i.e. after retries were
                     exhausted) the breaker OPENS and calls fail instantly, without touching
                     the network, for `recovery_timeout` seconds. It then goes HALF-OPEN and lets
                     exactly one trial call through: success closes it, failure re-opens it.

Deliberate choices:
  * 4xx answers are NOT failures: the service is healthy, the request was wrong. Not retried,
    and they do not count towards the breaker.
  * 429 is not retried and does not trip the breaker either: it is the caller's own budget
    running out, not an outage. It raises RateLimitedError carrying Retry-After.
"""
import json
import random
import socket
import time
import urllib.error
import urllib.request
import uuid
from typing import Callable, Optional

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TRANSIENT_STATUS = {502, 503, 504}


class CircuitOpenError(Exception):
    """Raised without any network I/O while the breaker is open."""


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, request_id: Optional[str] = None):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message, self.request_id = status, code, message, request_id


class RateLimitedError(ApiError):
    def __init__(self, retry_after: int, *a):
        super().__init__(*a)
        self.retry_after = retry_after


class ServiceUnavailable(Exception):
    """All attempts failed with a transient error."""


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 5.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold, self.recovery_timeout, self._clock = failure_threshold, recovery_timeout, clock
        self.state, self.failures, self.opened_at = self.CLOSED, 0, 0.0

    def allow(self) -> bool:
        if self.state == self.OPEN:
            if self._clock() - self.opened_at >= self.recovery_timeout:
                self.state = self.HALF_OPEN  # recovery period over: allow ONE trial call
                return True
            return False
        return True

    def record_success(self) -> None:
        self.state, self.failures = self.CLOSED, 0

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == self.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state, self.opened_at = self.OPEN, self._clock()

    def retry_in(self) -> float:
        return max(0.0, self.recovery_timeout - (self._clock() - self.opened_at))


class WalletClient:
    def __init__(self, base_url: str, token: str, timeout: float = 1.0, max_retries: int = 2,
                 backoff_base: float = 0.25, jitter: float = 0.05,
                 breaker: Optional[CircuitBreaker] = None, sleep: Callable[[float], None] = time.sleep,
                 log: Callable[[str], None] = lambda m: None):
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.max_retries, self.backoff_base, self.jitter = max_retries, backoff_base, jitter
        self.breaker = breaker or CircuitBreaker()
        self._sleep, self._log = sleep, log

    # ---- public calls -------------------------------------------------------
    def balance(self) -> dict:
        return self._call("GET", "/wallet/balance")

    def transfer(self, recipient: str, amount: float, idempotency_key: Optional[str] = None) -> dict:
        key = idempotency_key or str(uuid.uuid4())  # ONE key per logical transfer, reused by every retry
        return self._call("POST", "/transfers", {"recipient": recipient, "amount": amount},
                          {"Idempotency-Key": key}, protected=True)

    # ---- machinery ----------------------------------------------------------
    def _call(self, method, path, body=None, headers=None, protected=False) -> dict:
        if not self.breaker.allow():
            raise CircuitOpenError(f"circuit open; calls blocked for another {self.breaker.retry_in():.1f}s")
        retry_ok = method in SAFE_METHODS or protected
        attempts = 1 + (self.max_retries if retry_ok else 0)
        last = None
        for attempt in range(attempts):
            try:
                result = self._once(method, path, body, headers)
                self.breaker.record_success()
                return result
            except (ApiError, RateLimitedError):
                self.breaker.record_success()  # the service answered; the request was just rejected
                raise
            except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError, ServiceUnavailable) as e:
                last = e
                self._log(f"    attempt {attempt + 1}/{attempts} failed: {_describe(e)}")
                if attempt + 1 < attempts:
                    delay = self.backoff_base * (2 ** attempt) + random.uniform(0, self.jitter)
                    self._log(f"    backing off {delay:.2f}s before retrying")
                    self._sleep(delay)
        self.breaker.record_failure()
        raise ServiceUnavailable(f"{method} {path} failed after {attempts} attempt(s): {_describe(last)}")

    def _once(self, method, path, body, headers) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out = json.loads(resp.read() or b"{}")
                out["_replayed"] = resp.headers.get("Idempotent-Replayed") == "true"
                return out
        except urllib.error.HTTPError as e:
            payload = {}
            try:
                payload = json.loads(e.read() or b"{}").get("error", {})
            except ValueError:
                pass
            if e.code in TRANSIENT_STATUS or e.code == 500:
                raise ServiceUnavailable(f"HTTP {e.code} {payload.get('code', '')}".strip())
            if e.code == 429:
                raise RateLimitedError(int(e.headers.get("Retry-After", "1")), e.code,
                                       payload.get("code", "rate_limited"), payload.get("message", ""),
                                       payload.get("request_id"))
            raise ApiError(e.code, payload.get("code", "error"), payload.get("message", ""), payload.get("request_id"))


def _describe(e: Optional[BaseException]) -> str:
    if isinstance(e, urllib.error.URLError) and isinstance(e.reason, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(e, (TimeoutError, socket.timeout)):
        return "timeout"
    return str(e) if e else "unknown"
