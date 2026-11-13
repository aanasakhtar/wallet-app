"""
Assignment 3: Open-Loop Asynchronous Fixed-Rate Workload Generator.

Features:
  - Open-loop: Dispatches requests at fixed scheduled time intervals (1/RPS) regardless
    of server response time or backlog, accurately capturing queuing latency.
  - Asynchronous: Non-blocking coroutines for high-throughput HTTP (via httpx)
    and gRPC (via grpc.aio over a single HTTP/2 multiplexed channel).
  - Protocol agnostic: Supports --interface http and --interface grpc.
  - Sender pooling: Automatically provisions a pool of senders so concurrent
    financial transactions execute in parallel without false database row-lock contention.
  - Granular telemetry: Computes request rate, success/error distribution, and
    latency percentiles (p50, p90, p95, p99).
  - JSON reporting: Exports benchmark results to JSON.
"""
import argparse
import asyncio
import json
import os
import random
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
    pairs: List[tuple[str, str]],
    endpoint: str = "transfer",
    max_in_flight: int = 500,
) -> WorkloadStats:
    stats = WorkloadStats()
    base_url = f"http://{host}:{port}"
    sem = asyncio.Semaphore(max_in_flight)

    # Use persistent AsyncClient with HTTP/1.1 and connection pooling
    limits = httpx.Limits(max_keepalive_connections=100, max_connections=max_in_flight)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=15.0) as client:
        active_tasks = []
        stats.start_time = time.perf_counter()
        interval = 1.0 / target_rps if target_rps > 0 else 0.01
        num_requests = int(target_rps * duration)

        async def send_one(req_num: int):
            async with sem:
                t0 = time.perf_counter()
                req_id = str(uuid.uuid4())
                call_headers = {"x-request-id": req_id}
                tok, recip = pairs[req_num % len(pairs)] if pairs else ("", "bob@example.com")
                if tok:
                    call_headers["Authorization"] = f"Bearer {tok}"

                try:
                    if endpoint == "transfer":
                        call_headers["Idempotency-Key"] = f"bench-{req_id}"
                        res = await client.post(
                            "/transfers",
                            json={"recipient": recip, "amount": 1},
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
    pairs: List[tuple[str, str]],
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
                tok, recip = pairs[req_num % len(pairs)] if pairs else ("", "bob@example.com")
                if tok:
                    metadata.append(("authorization", f"Bearer {tok}"))
                req = wallet_pb2.CreateTransferRequest(
                    recipient=recip,
                    amount=1.0,
                    idempotency_key=f"grpc-bench-{req_id}",
                )
                try:
                    resp = await stub.CreateTransfer(req, timeout=15.0, metadata=metadata)
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


def setup_auth_pool(base_url: str, num_pairs: int = 15) -> List[tuple[str, str]]:
    """Helper to provision distinct sender/recipient pairs with generous balances."""
    client = httpx.Client(base_url=base_url, timeout=15.0)
    uid = uuid.uuid4().hex[:6]
    pairs = []
    for i in range(num_pairs):
        sender_email = f"sender-{i}-{uid}@example.com"
        recip_email = f"recip-{i}-{uid}@example.com"
        num_s = random.randint(1000000, 9999999)
        num_r = random.randint(1000000, 9999999)
        client.post("/auth/signup", json={"name": f"Sender {i}", "email": sender_email, "phone": f"+92300{num_s}", "password": "password123"})
        client.post("/auth/signup", json={"name": f"Recipient {i}", "email": recip_email, "phone": f"+92301{num_r}", "password": "password123"})
        res = client.post("/auth/login", data={"username": sender_email, "password": "password123"})
        tok = res.json()["access_token"]
        client.post("/wallet/topups", json={"amount": 500000}, headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": f"topup-{i}-{uid}"})
        pairs.append((tok, recip_email))

    return pairs


def main():
    parser = argparse.ArgumentParser(description="Open-loop asynchronous fixed-rate workload generator for Assignment 3.")
    parser.add_argument("--interface", choices=["http", "grpc"], default="http", help="Interface to benchmark (http or grpc)")
    parser.add_argument("--host", default="localhost", help="Target host (Envoy or backend)")
    parser.add_argument("--port", type=int, help="Target port (default: 10000 for http, 10001 for grpc)")
    parser.add_argument("--target-rps", type=float, default=20.0, help="Target requests per second (open-loop rate)")
    parser.add_argument("--duration", type=float, default=10.0, help="Duration in seconds")
    parser.add_argument("--endpoint", default="health", choices=["transfer", "balance", "health"], help="HTTP endpoint under test")
    parser.add_argument("--token", help="Bearer JWT token (if provided, uses single token)")
    parser.add_argument("--senders", type=int, default=10, help="Number of distinct sender accounts to pool")
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

    pairs = [(args.token, args.recipient)] if args.token else []

    # If transfer benchmark and no token provided, auto-provision test pool
    if (args.endpoint == "transfer" or args.interface == "grpc") and not pairs:
        try:
            http_target = f"http://{args.host}:{10000 if args.interface == 'grpc' else port}"
            print(f"Auto-provisioning pool of {args.senders} sender/recipient pairs against {http_target}...")
            pairs = setup_auth_pool(http_target, num_pairs=args.senders)
            print(f"Successfully provisioned {len(pairs)} independent pairs.")
        except Exception as e:
            print(f"Notice: Could not auto-authenticate against {http_target}: {e}")
            print("Will proceed with unauthenticated requests.")

    if args.interface == "http":
        stats = asyncio.run(run_http_workload(
            host=args.host,
            port=port,
            target_rps=args.target_rps,
            duration=args.duration,
            pairs=pairs,
            endpoint=args.endpoint,
        ))
    else:
        stats = asyncio.run(run_grpc_workload(
            host=args.host,
            port=port,
            target_rps=args.target_rps,
            duration=args.duration,
            pairs=pairs,
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
        parent = os.path.dirname(args.output_json)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.output_json, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Results saved to: {args.output_json}")


if __name__ == "__main__":
    main()
