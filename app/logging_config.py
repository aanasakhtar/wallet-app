"""
Structured JSON logging.

Every log line is a single-line JSON object with a stable set of fields, so
Filebeat can parse it with decode_json_fields and no grok/dissect patterns
are needed. This is why Part C's "explain parsing if using plain text" does
not apply here - there is nothing to parse, the app emits structured data.

Output goes to TWO places:
  1. stdout  - so `docker logs` / the terminal still show something readable,
               and so the Docker Compose path works via the json-file driver.
  2. a file  - logs/wallet-app.json, rotated. This is what Filebeat tails in
               the no-Docker setup.

Field contract (Part C asks for time, service, severity, message, request id):
  @timestamp   ISO-8601 UTC, the field name Elasticsearch expects by default
  service      constant, "wallet-api" - lets one ES index hold several services
  level        INFO / WARNING / ERROR
  message      human-readable text
  request_id   correlation id from the request-context middleware
  logger       python logger name
  plus any extra={...} keys passed at the call site (event, user_id, amount, ...)

NEVER logged: passwords, password hashes, JWTs, full account identifiers.
See _REDACT_KEYS below.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from .middleware.request_context import get_request_id

SERVICE_NAME = os.getenv("SERVICE_NAME", "wallet-api")
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_FILE_NAME = os.getenv("LOG_FILE_NAME", "wallet-app.json")
LOG_FILE = os.path.join(LOG_DIR, LOG_FILE_NAME)
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))  # keep 5 rotations

# Keys that must never reach the log file even if passed via extra={...}.
_REDACT_KEYS = {"password", "password_hash", "token", "access_token", "authorization", "secret"}

# LogRecord attributes that are built in; anything else in __dict__ is a
# custom field the caller passed via extra={...}.
_STANDARD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "request_id", "message",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str = None):
        super().__init__()
        self.service = service or SERVICE_NAME

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "@timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "service": self.service,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS:
                continue
            if key.lower() in _REDACT_KEYS:
                payload[key] = "[REDACTED]"
                continue
            try:
                json.dumps(value)  # only keep JSON-serialisable extras
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = str(value)

        if record.exc_info:
            payload["error"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "stack_trace": self.formatException(record.exc_info),
            }

        return json.dumps(payload, ensure_ascii=False)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def configure_logging(service_name: str = None, log_file_name: str = None) -> None:
    """service_name / log_file_name let a second process (the gRPC service) write
    its own file with its own `service` field, instead of two processes rotating
    the same file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, log_file_name) if log_file_name else LOG_FILE

    formatter = JsonFormatter(service_name)
    request_id_filter = RequestIdFilter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(request_id_filter)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(request_id_filter)

    root = logging.getLogger()
    root.handlers = [stdout_handler, file_handler]
    root.setLevel(logging.INFO)

    # uvicorn's own loggers would otherwise emit plain text alongside our JSON,
    # which would give Filebeat lines it cannot decode. Route them through the
    # same handlers instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    # httpx is chatty at INFO during tests; it adds nothing to the log pipeline.
    logging.getLogger("httpx").setLevel(logging.WARNING)


app_logger = logging.getLogger("wallet_app")
