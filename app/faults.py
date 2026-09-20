"""
Deliberate, reversible fault injection for the Part E anomaly experiment.

Design goals:
  - Toggleable at RUNTIME (via /admin/faults) so a single Prometheus/Grafana
    time range can show before / during / after without restarting the app.
  - Off by default, and a single call turns everything off again.
  - Every injected fault increments a counter, so the experiment can prove
    the fault fired rather than relying on the wall clock.

Default experiment from the assignment brief: add a 500 ms delay to every
fifth request.
"""
import threading
import time
from dataclasses import dataclass, asdict

from .logging_config import app_logger
from .metrics import injected_faults_total


@dataclass
class FaultConfig:
    latency_enabled: bool = False
    latency_ms: int = 500
    latency_every_n: int = 5  # delay every Nth request; 1 = every request

    error_enabled: bool = False
    error_every_n: int = 10  # fail every Nth request


class FaultInjector:
    def __init__(self) -> None:
        self._config = FaultConfig()
        self._counter = 0
        self._lock = threading.Lock()

    # -- configuration ------------------------------------------------------

    def get_config(self) -> dict:
        return asdict(self._config)

    def update(self, **kwargs) -> dict:
        with self._lock:
            for key, value in kwargs.items():
                if value is not None and hasattr(self._config, key):
                    setattr(self._config, key, value)
        app_logger.warning(
            "Fault injection config changed: %s", self.get_config(),
            extra={"event": "fault_config_changed", **self.get_config()},
        )
        return self.get_config()

    def reset(self) -> dict:
        with self._lock:
            self._config = FaultConfig()
            self._counter = 0
        app_logger.warning(
            "Fault injection reset to defaults (all faults off)",
            extra={"event": "fault_reset"},
        )
        return self.get_config()

    # -- the actual fault ---------------------------------------------------

    def maybe_inject(self) -> None:
        """Called once per request. Applies a delay and/or raises, per config."""
        cfg = self._config
        if not cfg.latency_enabled and not cfg.error_enabled:
            return

        with self._lock:
            self._counter += 1
            n = self._counter

        if cfg.latency_enabled and cfg.latency_every_n > 0 and n % cfg.latency_every_n == 0:
            injected_faults_total.labels(kind="latency").inc()
            app_logger.warning(
                "Injecting artificial latency of %d ms", cfg.latency_ms,
                extra={"event": "fault_injected", "fault_kind": "latency",
                       "fault_latency_ms": cfg.latency_ms},
            )
            time.sleep(cfg.latency_ms / 1000.0)

        if cfg.error_enabled and cfg.error_every_n > 0 and n % cfg.error_every_n == 0:
            injected_faults_total.labels(kind="error").inc()
            app_logger.error(
                "Injecting artificial server error",
                extra={"event": "fault_injected", "fault_kind": "error"},
            )
            raise InjectedFault("Injected fault for observability experiment")


class InjectedFault(Exception):
    pass


fault_injector = FaultInjector()
