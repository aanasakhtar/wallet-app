import logging
import sys

from .middleware.request_context import get_request_id


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [request_id=%(request_id)s] %(name)s: %(message)s"
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


# A logger any router/service can import directly.
app_logger = logging.getLogger("wallet_app")
