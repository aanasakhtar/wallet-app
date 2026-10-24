"""
Rate limiting for ONE endpoint: POST /transfers.

Algorithm: sliding-window log. For each key we remember the timestamps of the
requests accepted in the last WINDOW seconds. A request is rejected when LIMIT
timestamps are already inside the window, and Retry-After is the time until the
OLDEST of them leaves the window - i.e. exactly when the next call is allowed.

Who shares a limit: one budget per AUTHENTICATED USER (all of that user's
devices/tokens share it); different users never affect each other.

Limits of this implementation (stated in the report): state lives in process
memory, so it resets on restart and is per instance. With several instances it
would need a shared store such as Redis.
"""
import math
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Tuple

from fastapi import Depends, Response

from .config import settings
from .dependencies import get_current_user
from .errors import RateLimited
from .logging_config import app_logger
from .metrics import rate_limit_rejections_total
from .models import User


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self._clock = limit, window_seconds, clock
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> Tuple[bool, int, int]:
        """Returns (allowed, remaining, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                retry_after = max(1, math.ceil(q[0] + self.window - now))
                return False, 0, retry_after
            q.append(now)
            return True, self.limit - len(q), 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def _parse(spec: str) -> Tuple[int, float]:
    limit, window = spec.split("/")
    return int(limit), float(window)


_limit, _window = _parse(settings.rate_limit_transfers)
transfer_limiter = SlidingWindowLimiter(_limit, _window)


def enforce_transfer_rate_limit(response: Response, user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency: runs after authentication and BEFORE body validation,
    so a flooding client is rejected cheaply."""
    if not settings.rate_limit_enabled:
        return user
    allowed, remaining, retry_after = transfer_limiter.hit(user.id)
    limit_headers = {"RateLimit-Limit": str(transfer_limiter.limit), "RateLimit-Remaining": str(remaining)}
    if not allowed:
        rate_limit_rejections_total.labels(endpoint="/transfers").inc()
        app_logger.warning(
            "Rate limit exceeded", extra={"event": "rate_limited", "user_id": user.id,
                                          "endpoint": "/transfers", "retry_after_seconds": retry_after})
        raise RateLimited(
            f"Rate limit of {transfer_limiter.limit} requests per {transfer_limiter.window:g}s exceeded; "
            f"retry in {retry_after}s",
            headers={"Retry-After": str(retry_after), **limit_headers},
        )
    response.headers.update(limit_headers)
    return user
