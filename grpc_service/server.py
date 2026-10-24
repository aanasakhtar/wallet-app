"""
Entry point of the gRPC process:   python -m grpc_service.server

Runs as its own process (own container in docker-compose), with:
  * gRPC on GRPC_PORT        (default 50051)
  * a Prometheus /metrics on GRPC_METRICS_PORT (default 8002). The shared business code
    records transactions_total etc. in THIS process's registry, so without this
    endpoint those counts would be lost. RPC-level metrics are added in Part C.
Logs go to logs/wallet-grpc.json with service="wallet-grpc" (a separate file from the
HTTP app so two processes never rotate the same file).
"""
import os
import signal
from concurrent import futures

import grpc
from prometheus_client import start_http_server

import grpc_service  # noqa: F401  (puts generated/ on sys.path)
from app.database import Base, engine
from app.logging_config import app_logger, configure_logging
from grpc_service.interceptors import RequestContextInterceptor
from grpc_service.servicer import TransferServicer
from wallet.v1 import wallet_pb2_grpc


def build_server(address: str = "[::]:0", max_workers: int = 10):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers),
                         interceptors=[RequestContextInterceptor()])
    wallet_pb2_grpc.add_TransferServiceServicer_to_server(TransferServicer(), server)
    port = server.add_insecure_port(address)
    return server, port


def main() -> None:
    configure_logging(service_name=os.getenv("SERVICE_NAME", "wallet-grpc"),
                      log_file_name=os.getenv("LOG_FILE_NAME", "wallet-grpc.json"))
    try:
        Base.metadata.create_all(bind=engine)  # no-op when the HTTP app already created the tables
    except Exception as e:  # two processes starting together can race on first creation
        app_logger.warning("create_all raced with another process: %s", e)

    grpc_port = int(os.getenv("GRPC_PORT", "50051"))
    metrics_port = int(os.getenv("GRPC_METRICS_PORT", "8002"))
    start_http_server(metrics_port)
    server, bound = build_server(f"[::]:{grpc_port}", int(os.getenv("GRPC_MAX_WORKERS", "10")))
    server.start()
    app_logger.info("gRPC server started", extra={"event": "grpc_startup", "grpc_port": bound,
                                                  "metrics_port": metrics_port})
    signal.signal(signal.SIGTERM, lambda *_: server.stop(grace=5))
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=5).wait()


if __name__ == "__main__":
    main()
