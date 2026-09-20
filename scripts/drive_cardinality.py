"""
Drives traffic at the cardinality demo app (Part E.2).

Usage:
    python3 scripts/drive_cardinality.py --requests 120

Each request to /demo generates a fresh UUID. In labelled mode this creates a
new time series until the 100-series cap; in unlabelled mode it reuses the one
existing series forever. Same traffic, very different storage cost.
"""
import argparse
import json
import os
import time
import urllib.request

# 127.0.0.1 rather than "localhost": on some systems localhost resolves to
# ::1 (IPv6) first, while uvicorn may only be listening on IPv4.
BASE = os.getenv("BASE_URL", "http://127.0.0.1:8001")


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=120)
    ap.add_argument("--delay", type=float, default=0.15,
                    help="seconds between requests; keep the run long enough "
                         "to span several Prometheus scrapes")
    args = ap.parse_args()

    try:
        stats = get("/stats")
    except Exception as e:
        print(f"Cannot reach the demo app at {BASE} ({e}).")
        print("Start it first:  python3 cardinality_demo.py")
        return

    print(f"Demo mode: {stats['mode']} "
          f"({'labelled by request_id' if stats['mode'] == 'on' else 'bounded label'})")
    print(f"Sending {args.requests} requests...\n")

    started = time.strftime("%H:%M:%S")
    for i in range(1, args.requests + 1):
        body = get("/demo")
        if i % 20 == 0:
            print(f"  {i:>4} requests -> {body['unique_series']} series"
                  f"{' (capped)' if body['capped'] else ''}")
        time.sleep(args.delay)

    final = get("/stats")
    print(f"\nDone. Window: {started} - {time.strftime('%H:%M:%S')}")
    print(f"Final series count for demo_requests_total: {final['series_count']}")
    print("\nCheck in Prometheus (http://localhost:9090):")
    print("  count(demo_requests_total)     <- series count")
    print("  sum(demo_requests_total)       <- total requests (same in both modes)")
    print("  prometheus_tsdb_head_series    <- Prometheus's total series in memory")


if __name__ == "__main__":
    main()
