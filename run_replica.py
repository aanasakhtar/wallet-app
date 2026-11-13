"""
Multi-service runner for a single Wallet App replica (Part A requirement).

Runs both:
  1. FastAPI HTTP application (on HTTP_PORT, default 8000)
  2. gRPC TransferService (on GRPC_PORT, default 50051)
  3. Dedicated gRPC /metrics endpoint (on GRPC_METRICS_PORT, default 8002)

Injects REPLICA_ID (A, B, C) into:
  - Structured JSON logs: {"replica": "A", "service": "wallet-api", ...}
  - Metric labels: wallet_replica_info{replica="A"}
  - Direct Prometheus scrapes

Usage:
  python run_replica.py --replica A --http-port 8010 --grpc-port 50151 --metrics-port 8012
  python run_replica.py --replica B --http-port 8020 --grpc-port 50251 --metrics-port 8022
  python run_replica.py --replica C --http-port 8030 --grpc-port 50351 --metrics-port 8032
"""
import argparse
import os
import signal
import sys
import threading
import time

# Parse replica ID and ports early, before application imports
parser = argparse.ArgumentParser(description="Wallet App Replica Supervisor")
parser.add_argument("--replica", default=os.getenv("REPLICA_ID", "A"), help="Replica identifier (A, B, C)")
parser.add_argument("--http-host", default=os.getenv("HTTP_HOST", "0.0.0.0"), help="HTTP bind address")
parser.add_argument("--http-port", type=int, default=int(os.getenv("HTTP_PORT", "8000")), help="HTTP bind port")
parser.add_argument("--grpc-port", type=int, default=int(os.getenv("GRPC_PORT", "50051")), help="gRPC bind port")
parser.add_argument("--metrics-port", type=int, default=int(os.getenv("GRPC_METRICS_PORT", "8002")), help="Auxiliary metrics port")
args, _ = parser.parse_known_args()

replica_id = args.replica.upper()
os.environ["REPLICA_ID"] = replica_id
os.environ["SERVICE_NAME"] = os.getenv("SERVICE_NAME", f"wallet-replica-{replica_id.lower()}")
os.environ["LOG_FILE_NAME"] = os.getenv("LOG_FILE_NAME", f"wallet-replica-{replica_id.lower()}.json")

# Now import application modules
from app.database import Base, engine  # noqa: E402
from app.logging_config import app_logger, configure_logging  # noqa: E402
from app.metrics import replica_info  # noqa: E402
from grpc_service.server import build_server  # noqa: E402
import prometheus_client  # noqa: E402
import uvicorn  # noqa: E402

# Ensure replica_info gauge reflects this replica
replica_info.labels(replica=replica_id).set(1)

# Configure logging with explicit replica identifier
configure_logging(
    service_name=os.environ["SERVICE_NAME"],
    log_file_name=os.environ["LOG_FILE_NAME"],
    replica_id=replica_id,
)


def main():
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        app_logger.warning("Database create_all raced or completed: %s", e)

    # 1. Start gRPC Server
    grpc_server, bound_grpc_port = build_server(f"[::]:{args.grpc_port}")
    grpc_server.start()
    app_logger.info(
        "Replica gRPC server started",
        extra={
            "event": "replica_grpc_startup",
            "replica": replica_id,
            "grpc_port": bound_grpc_port,
        },
    )

    # 2. Start auxiliary Prometheus metrics server on metrics_port if requested
    try:
        prometheus_client.start_http_server(args.metrics_port)
        app_logger.info(
            "Replica auxiliary metrics server started",
            extra={
                "event": "replica_metrics_startup",
                "replica": replica_id,
                "metrics_port": args.metrics_port,
            },
        )
    except Exception as e:
        app_logger.info(
            "Auxiliary metrics server skipped (already active or in use): %s",
            e,
            extra={"replica": replica_id},
        )

    # 3. Handle graceful shutdown
    stop_event = threading.Event()

    def handle_signal(sig, frame):
        app_logger.info(
            "Replica received termination signal",
            extra={"event": "replica_stopping", "replica": replica_id, "signal": sig},
        )
        stop_event.set()
        grpc_server.stop(grace=2).wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    app_logger.info(
        "Starting Replica HTTP application via Uvicorn",
        extra={
            "event": "replica_http_startup",
            "replica": replica_id,
            "http_host": args.http_host,
            "http_port": args.http_port,
        },
    )

    # 4. Run Uvicorn
    # log_config=None ensures uvicorn routes through our custom structured JsonFormatter
    config = uvicorn.Config(
        "app.main:app",
        host=args.http_host,
        port=args.http_port,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        grpc_server.stop(grace=1).wait()
        app_logger.info("Replica shutdown completed", extra={"event": "replica_stopped", "replica": replica_id})


if __name__ == "__main__":
    main()
