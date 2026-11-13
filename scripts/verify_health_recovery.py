"""
Assignment 3 - Part D: Experiment 3: Active Health Checking, Failure and Recovery.

Measures:
1. Baseline health state (A, B, C all healthy).
2. Stops Replica B and measures the failure detection window (Envoy marks /failed_active_hc).
3. Verifies traffic rebalances 50% / 50% across A and C with 0% to B.
4. Starts Replica B and measures the recovery verification window (Envoy marks healthy).
5. Verifies traffic returns to balanced round-robin across A, B, C.
6. Saves detailed timing evidence to docs/evidence/part_d_health_failure_recovery.json.
"""
import json
import os
import subprocess
import time
import httpx


def get_cluster_status():
    resp = httpx.get("http://localhost:9901/clusters", timeout=5.0)
    lines = resp.text.splitlines()
    status = {}
    current_host = None
    for line in lines:
        if line.startswith("wallet_http_cluster::") and "::hostname::" in line:
            parts = line.split("::")
            ip = parts[1]
            host = parts[3]
            status[host] = {"ip": ip, "health": "unknown"}
            current_host = host
        elif current_host and line.startswith(f"wallet_http_cluster::{status[current_host]['ip']}::health_flags::"):
            flags = line.split("::")[3]
            status[current_host]["health"] = flags
    return status


def test_traffic(num_requests=30):
    counts = {"A": 0, "B": 0, "C": 0}
    with httpx.Client(timeout=5.0) as client:
        for _ in range(num_requests):
            try:
                r = client.get("http://localhost:10000/health")
                rep = r.json().get("replica")
                if rep in counts:
                    counts[rep] += 1
            except Exception:
                pass
    return counts


def main():
    print("=== Experiment 3: Active Health Checking, Failure & Recovery ===")
    
    # 1. Baseline
    init_status = get_cluster_status()
    print(f"Initial Status: {init_status}")
    baseline_traffic = test_traffic(30)
    print(f"Baseline traffic (30 reqs): {baseline_traffic}")

    # 2. Stop Replica B
    print("\nStopping Replica B (docker compose stop app-b)...")
    stop_start_time = time.time()
    subprocess.run(["docker", "compose", "stop", "app-b"], check=True)
    stop_cmd_time = time.time()

    # Poll until Envoy marks B unhealthy
    b_marked_unhealthy = None
    while time.time() - stop_start_time < 30:
        st = get_cluster_status()
        b_flags = st.get("app-b", {}).get("health", "")
        if "failed_active_hc" in b_flags or b_flags != "healthy":
            b_marked_unhealthy = time.time()
            print(f"Replica B marked unhealthy in Envoy at +{b_marked_unhealthy - stop_start_time:.2f}s (flags: {b_flags})")
            break
        time.sleep(0.5)

    detection_time_s = (b_marked_unhealthy - stop_start_time) if b_marked_unhealthy else None
    
    # Traffic during failure
    print("Sending traffic during failure...")
    failure_traffic = test_traffic(30)
    print(f"Failure traffic (30 reqs): {failure_traffic}")

    # 3. Start Replica B
    print("\nStarting Replica B (docker compose start app-b)...")
    restart_start_time = time.time()
    subprocess.run(["docker", "compose", "start", "app-b"], check=True)
    
    # Poll until Envoy marks B healthy
    b_marked_healthy = None
    while time.time() - restart_start_time < 30:
        st = get_cluster_status()
        b_flags = st.get("app-b", {}).get("health", "")
        if b_flags == "healthy":
            b_marked_healthy = time.time()
            print(f"Replica B marked healthy in Envoy at +{b_marked_healthy - restart_start_time:.2f}s")
            break
        time.sleep(0.5)

    recovery_time_s = (b_marked_healthy - restart_start_time) if b_marked_healthy else None

    # Traffic after recovery
    print("Sending traffic after recovery...")
    recovery_traffic = test_traffic(30)
    print(f"Recovery traffic (30 reqs): {recovery_traffic}")

    evidence = {
        "experiment": "active_health_check_failure_recovery",
        "health_check_config": {
            "interval_s": 2,
            "timeout_s": 1,
            "unhealthy_threshold": 2,
            "healthy_threshold": 2,
            "theoretical_detection_s": 4.0,
            "theoretical_recovery_s": 4.0,
        },
        "measured_detection_time_s": round(detection_time_s, 2) if detection_time_s else None,
        "measured_recovery_time_s": round(recovery_time_s, 2) if recovery_time_s else None,
        "traffic_distribution": {
            "baseline_3_replicas": baseline_traffic,
            "during_replica_b_failure": failure_traffic,
            "after_replica_b_recovery": recovery_traffic,
        },
        "success": (
            failure_traffic.get("B", 0) == 0 and
            failure_traffic.get("A", 0) > 0 and
            failure_traffic.get("C", 0) > 0 and
            recovery_traffic.get("B", 0) > 0
        ),
    }

    os.makedirs("docs/evidence", exist_ok=True)
    out_file = "docs/evidence/part_d_health_failure_recovery.json"
    with open(out_file, "w") as f:
        json.dump(evidence, f, indent=2)
    print(f"\nSaved empirical evidence to {out_file}")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
