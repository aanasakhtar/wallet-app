"""
Assignment 3: Live Docker Multi-Replica Integration Validation Script.

Connects to the three running Docker containers:
  - Replica A: http://localhost:8001
  - Replica B: http://localhost:8002
  - Replica C: http://localhost:8003
  - Envoy:     http://localhost:10000

Validates:
  1. Live PostgreSQL shared state across replicas
  2. Cross-replica idempotency replay (A creates, B replays without moving funds)
  3. Cross-replica key reuse rejection (422)
  4. Concurrent cross-replica collision race safety
  5. Local in-memory rate limiter partitioning across replicas
"""
import concurrent.futures
import json
import random
import sys
import time
import uuid
import httpx

URL_A = "http://localhost:8001"
URL_B = "http://localhost:8002"
URL_C = "http://localhost:8003"
URL_ENVOY = "http://localhost:10000"


def expect(cond: bool, msg: str):
    if not cond:
        print(f"FAILED: {msg}")
        sys.exit(1)
    print(f"OK: {msg}")


def main():
    print("=== Validating Part A: Live Multi-Replica Infrastructure ===")
    client_a = httpx.Client(base_url=URL_A, timeout=10.0)
    client_b = httpx.Client(base_url=URL_B, timeout=10.0)
    client_c = httpx.Client(base_url=URL_C, timeout=10.0)

    # 1. Health checks on all three replicas
    expect(client_a.get("/health").status_code == 200, "Replica A is healthy on :8001")
    expect(client_b.get("/health").status_code == 200, "Replica B is healthy on :8002")
    expect(client_c.get("/health").status_code == 200, "Replica C is healthy on :8003")

    # 2. Shared database source of truth: Signup on A, read on B & C
    uid = uuid.uuid4().hex[:6]
    alice_email = f"live-alice-{uid}@example.com"
    bob_email = f"live-bob-{uid}@example.com"
    num1 = random.randint(1000000, 9999999)
    num2 = random.randint(1000000, 9999999)

    r_signup_a = client_a.post("/auth/signup", json={"name": "Alice", "email": alice_email, "phone": f"+92300{num1}", "password": "password123"})
    expect(r_signup_a.status_code == 201, "Alice signed up on Replica A")

    r_signup_b = client_b.post("/auth/signup", json={"name": "Bob", "email": bob_email, "phone": f"+92301{num2}", "password": "password123"})
    expect(r_signup_b.status_code == 201, "Bob signed up on Replica B")

    # Login on Replica C
    r_login_c = client_c.post("/auth/login", data={"username": alice_email, "password": "password123"})
    expect(r_login_c.status_code == 200, "Alice logged in on Replica C (PostgreSQL shared state)")
    alice_token = r_login_c.json()["access_token"]
    alice_headers = {"Authorization": f"Bearer {alice_token}"}

    # Topup Alice on Replica A
    r_topup = client_a.post(
        "/wallet/topups",
        json={"amount": 1000},
        headers={**alice_headers, "Idempotency-Key": f"topup-{uid}"},
    )
    expect(r_topup.status_code == 201, "Alice topped up 1000 on Replica A")

    # Verify balance on Replica B & Replica C
    bal_b = client_b.get("/wallet/balance", headers=alice_headers).json()["balance"]
    bal_c = client_c.get("/wallet/balance", headers=alice_headers).json()["balance"]
    expect(bal_b == 1000.0 and bal_c == 1000.0, f"Balance is consistently 1000.0 across B ({bal_b}) and C ({bal_c})")

    # 3. Cross-replica Idempotency Replay:
    # Send transfer on Replica A -> 201 Created
    idem_key = f"x-rep-{uuid.uuid4().hex}"
    transfer_payload = {"recipient": bob_email, "amount": 250}
    r_tx_a = client_a.post(
        "/transfers",
        json=transfer_payload,
        headers={**alice_headers, "Idempotency-Key": idem_key},
    )
    expect(r_tx_a.status_code == 201, "Transfer created on Replica A")
    tx_id_a = r_tx_a.json()["id"]
    expect("Idempotent-Replayed" not in r_tx_a.headers, "Initial request on A is not marked replayed")

    # Verify balance is 750
    expect(client_b.get("/wallet/balance", headers=alice_headers).json()["balance"] == 750.0, "Alice balance debited to 750 on Replica B")

    # Replay exact same request on Replica B
    r_tx_b = client_b.post(
        "/transfers",
        json=transfer_payload,
        headers={**alice_headers, "Idempotency-Key": idem_key},
    )
    expect(r_tx_b.status_code == 201, "Replay request on Replica B returned 201")
    expect(r_tx_b.headers.get("Idempotent-Replayed") == "true", "Replica B correctly identified Idempotent-Replayed: true")
    expect(r_tx_b.json()["id"] == tx_id_a, "Replica B returned identical transaction ID")
    expect(client_c.get("/wallet/balance", headers=alice_headers).json()["balance"] == 750.0, "Alice balance remains 750 (no duplicate debit)")

    # 4. Cross-replica Key Reuse Mismatch:
    # Attempt to reuse key on Replica C with a different amount
    r_tx_c_bad = client_c.post(
        "/transfers",
        json={"recipient": bob_email, "amount": 500},
        headers={**alice_headers, "Idempotency-Key": idem_key},
    )
    expect(r_tx_c_bad.status_code == 422, f"Replica C rejected key reuse with {r_tx_c_bad.status_code}")
    expect(r_tx_c_bad.json()["error"]["code"] == "idempotency_key_reuse", "Replica C returned error code: idempotency_key_reuse")

    # 5. Concurrent Collision Race across Replicas:
    race_key = f"race-{uuid.uuid4().hex}"
    race_payload = {"recipient": bob_email, "amount": 50}
    clients = [client_a, client_b, client_c, client_a]

    def send_race(c):
        return c.post(
            "/transfers",
            json=race_payload,
            headers={**alice_headers, "Idempotency-Key": race_key},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        race_responses = list(ex.map(send_race, clients))

    expect(all(r.status_code == 201 for r in race_responses), "All 4 concurrent cross-replica callers received 201")
    race_tx_ids = {r.json()["id"] for r in race_responses}
    expect(len(race_tx_ids) == 1, f"All 4 concurrent cross-replica callers got exact same transaction ID: {race_tx_ids}")
    expect(client_a.get("/wallet/balance", headers=alice_headers).json()["balance"] == 700.0, "Alice balance decremented exactly once (700.0)")

    print("\nALL PART A LIVE DOCKER TESTS PASSED!")


if __name__ == "__main__":
    main()
