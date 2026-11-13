"""
Assignment 3: Open-Loop Asynchronous Fixed-Rate Workload Generator.

Features:
  - Open-loop: Dispatches requests at fixed scheduled time intervals (1/RPS) regardless
    of server response time or backlog, accurately capturing queuing latency.
  - Asynchronous: Non-blocking coroutines for high-throughput HTTP (via httpx)
    and gRPC (via grpc.aio over a single HTTP/2 multiplexed channel).
  - Protocol agnostic: Supports --interface http and --interface grpc.
  - Granular telemetry: Computes request rate, success/error distribution, and
    latency percentiles (p50, p90, p95, p99).
  - JSON reporting: Can export benchmark results to JSON for automated analysis.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from typing import Dict, List, Optional

# Add project root to path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import httpx  # noqa: E402
import grpc.aio  # noqa: E402
import grpc_service  # noqa: F401, E402
from wallet.v1 import wallet_pb2, wallet_pb2_grpc  # noqa: E402


class WorkloadStats:
    def __init__(self):
        self.sent: int = 0
        self.completed: int = 0
        self.successes: int = 0
        self.failures: Dict[str, int] = {}
        self.latencies_ms: List[float] = []
        self.start_time: float = 0.0
        self.end_time: float = 0.0

    def record_success(self, latency_ms: float):
        self.completed += 1
        self.successes += 1
        self.latencies_ms.append(latency_ms)

    def record_failure(self, code: str, latency_ms: float):
        self.completed += 1
        self.failures[code] = self.failures.get(code, 0) + 1
        self.latencies_ms.append(latency_ms)

    def summary(self) -> dict:
        total_time = max(self.end_time - self.start_time, 0.001)
        actual_rps = self.completed / total_time
        lats = sorted(self.latencies_ms)
        n = len(lats)

        def pct(p: float) -> float:
            if not n:
                return 0.0
            k = int(round((n - 1) * p))
            return lats[k]

        return {
            "duration_seconds": round(total_time, 3),
            "requests_sent": self.sent,
            "requests_completed": self.completed,
            "successes": self.successes,
            "failures": self.failures,
            "actual_rps": round(actual_rps, 2),
            "latency_ms": {
                "min": round(lats[0], 2) if n else 0.0,
                "mean": round(sum(lats) / n, 2) if n else 0.0,
                "p50": round(pct(0.50), 2),
                "p90": round(pct(0.90), 2),
                "p95": round(pct(0.95), 2),
                "p99": round(pct(0.99), 2),
                "max": round(lats[-1], 2) if n else 0.0,
            },
        }


async def run_http_workload(
    host: str,
    port: int,
    target_rps: float,
    duration: float,
    token: str,
    recipient: str,
    endpoint: str = "transfer",
    max_in_flight: int = 500,
) -> WorkloadStats:
    stats = WorkloadStats()
    base_url = f"http://{host}:{port}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    sem = asyncio.Semaphore(max_in_flight)

    # Use persistent AsyncClient with HTTP/1.1 and connection pooling
    limits = httpx.Limits(max_keepalive_connections=100, max_connections=max_in_flight)
    async with httpx.AsyncClient(base_url=base_url, headers=headers, limits=limits, timeout=10.0) as client:
        active_tasks = []
        stats.start_time = time.perf_counter()
        interval = 1.0 / target_rps if target_rps > 0 else 0.01
        num_requests = int(target_rps * duration)

        async def send_one(req_num: int):
            async with sem:
                t0 = time.perf_counter()
                req_id = str(uuid.uuid4())
                call_headers = {"x-request-id": req_id}
                try:
                    if endpoint == "transfer":
                        call_headers["Idempotency-Key"] = f"bench-{req_id}"
                        res = await client.post(
                            "/transfers",
                            json={"recipient": recipient, "amount": 1},
                            headers=call_headers,
                        )
                    elif endpoint == "balance":
                        res = await client.get("/wallet/balance", headers=call_headers)
                    elif endpoint == "health":
                        res = await client.get("/health", headers=call_headers)
                    else:
                        res = await client.get(endpoint, headers=call_headers)

                    lat = (time.perf_counter() - t0) * 1000.0
                    if 200 <= res.status_code < 300:
                        stats.record_success(lat)
                    else:
                        stats.record_failure(f"HTTP_{res.status_code}", lat)
                except Exception as ex:
                    lat = (time.perf_counter() - t0) * 1000.0
                    stats.record_failure(type(ex).__name__, lat)

        # Open-loop dispatcher
        for i in range(num_requests):
            stats.sent += 1
            active_tasks.append(asyncio.create_task(send_one(i)))
            # Schedule next dispatch according to absolute timeline to eliminate drift
            expected_time = stats.start_time + (i + 1) * interval
            now = time.perf_counter()
            sleep_time = expected_time - now
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        # Wait for all in-flight requests to settle
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

        stats.end_time = time.perf_counter()

    return stats


async def run_grpc_workload(
    host: str,
    port: int,
    target_rps: float,
    duration: float,
    token: str,
    recipient: str,
    max_in_flight: int = 500,
) -> WorkloadStats:
    stats = WorkloadStats()
    target = f"{host}:{port}"
    sem = asyncio.Semaphore(max_in_flight)

    # Maintain single persistent HTTP/2 channel to Envoy
    async with grpc.aio.insecure_channel(target) as channel:
        stub = wallet_pb2_grpc.TransferServiceStub(channel)
        active_tasks = []
        stats.start_time = time.perf_counter()
        interval = 1.0 / target_rps if target_rps > 0 else 0.01
        num_requests = int(target_rps * duration)

        async def send_one(req_num: int):
            async with sem:
                t0 = time.perf_counter()
                req_id = str(uuid.uuid4())
                metadata = [("x-request-id", req_id)]
                if token:
                    metadata.append(("authorization", f"Bearer {token}"))
                req = wallet_pb2.CreateTransferRequest(
                    recipient=recipient,
                    amount=1.0,
                    idempotency_key=f"grpc-bench-{req_id}",
                )
                try:
                    resp = await stub.CreateTransfer(req, timeout=5.0, metadata=metadata)
                    lat = (time.perf_counter() - t0) * 1000.0
                    stats.record_success(lat)
                except grpc.aio.AioRpcError as err:
                    lat = (time.perf_counter() - t0) * 1000.0
                    stats.record_failure(err.code().name, lat)
                except Exception as ex:
                    lat = (time.perf_counter() - t0) * 1000.0
                    stats.record_failure(type(ex).__name__, lat)

        # Open-loop dispatcher
        for i in range(num_requests):
            stats.sent += 1
            active_tasks.append(asyncio.create_task(send_one(i)))
            expected_time = stats.start_time + (i + 1) * interval
            now = time.perf_counter()
            sleep_time = expected_time - now
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

        stats.end_time = time.perf_counter()

    return stats


def setup_auth(base_url: str) -> tuple[str, str]:
    """Helper to register/login Alice & Bob and topup Alice."""
    client = httpx.Client(base_url=base_url, timeout=10.0)
    uid = uuid.uuid4().hex[:6]
    alice_email = f"bench-alice-{uid}@example.com"
    bob_email = f"bench-bob-{uid}@example.com"

    # Signup Alice & Bob
    client.post("/auth/signup", json={"name": "Alice Bench", "email": alice_email, "phone": f"+92300{int(time.time())%10000000:07d}", "password": "password123"})
    client.post("/auth/signup", json={"name": "Bob Bench", "email": bob_email, "phone": f"+92301{int(time.time()+1)%10000000:07d}", "password": "password123"})

    # Login Alice
    res = client.post("/auth/login", data={"username": alice_email, "password": "password123"})
    token = res.json()["access_token"]

    # Topup Alice heavily for benchmark
    client.post("/wallet/topups", json={"amount": 1000000}, headers={"Authorization": f"Bearer {token}", "Idempotency-Key": f"topup-{uid}"})
    return token, bob_email


def main():
    parser = argparse.ArgumentParser(description="Open-loop asynchronous fixed-rate workload generator for Assignment 3.")
    parser.add_argument("--interface", choices=["http", "grpc"], default="http", help="Interface to benchmark (http or grpc)")
    parser.add_argument("--host", default="localhost", help="Target host (Envoy or backend)")
    parser.add_argument("--port", type=int, help="Target port (default: 10000 for http, 10001 for grpc)")
    parser.add_argument("--target-rps", type=float, default=20.0, help="Target requests per second (open-loop rate)")
    parser.add_argument("--duration", type=float, default=10.0, help="Duration in seconds")
    parser.add_argument("--endpoint", default="health", choices=["transfer", "balance", "health"], help="HTTP endpoint under test")
    parser.add_argument("--token", help="Bearer JWT token (if empty and needed, will auto-create)")
    parser.add_argument("--recipient", default="bob@example.com", help="Recipient email for transfers")
    parser.add_argument("--output-json", help="Path to export results JSON")
    args = parser.parse_args()

    port = args.port or (10000 if args.interface == "http" else 10001)

    print(f"=== Workload Generator ===")
    print(f"Interface:    {args.interface.upper()}")
    print(f"Target:       {args.host}:{port}")
    print(f"Target RPS:   {args.target_rps}")
    print(f"Duration:     {args.duration}s")
    print(f"Endpoint:     {args.endpoint}")

    token = args.token or ""
    recipient = args.recipient

    # If transfer benchmark and no token provided, try to provision test account
    if args.endpoint == "transfer" and not token and args.interface == "http":
        try:
            print("Auto-provisioning test accounts...")
            token, recipient = setup_auth(f"http://{args.host}:{port}")
            print(f"Authenticated as Alice, recipient: {recipient}")
        except Exception as e:
            print(f"Notice: Could not auto-authenticate against http://{args.host}:{port}: {e}")
            print("Will proceed with unauthenticated requests to measure rate/routing.")

    if args.interface == "http":
        stats = asyncio.run(run_http_workload(
            host=args.host,
            port=port,
            target_rps=args.target_rps,
            duration=args.duration,
            token=token,
            recipient=recipient,
            endpoint=args.endpoint,
        ))
    else:
        stats = asyncio.run(run_grpc_workload(
            host=args.host,
            port=port,
            target_rps=args.target_rps,
            duration=args.duration,
            token=token,
            recipient=recipient,
        ))

    summary = stats.summary()
    print("\n=== Benchmark Results ===")
    print(f"Duration:           {summary['duration_seconds']}s")
    print(f"Requests Sent:      {summary['requests_sent']}")
    print(f"Requests Completed: {summary['requests_completed']}")
    print(f"Successes:          {summary['successes']}")
    print(f"Failures:           {summary['failures']}")
    print(f"Actual RPS:         {summary['actual_rps']}")
    print(f"Latency (p50):      {summary['latency_ms']['p50']} ms")
    print(f"Latency (p90):      {summary['latency_ms']['p90']} ms")
    print(f"Latency (p95):      {summary['latency_ms']['p95']} ms")
    print(f"Latency (p99):      {summary['latency_ms']['p99']} ms")
    print(f"Latency (mean):     {summary['latency_ms']['mean']} ms")

    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Results saved to: {args.output_json}")


if __name__ == "__main__":
    main()
