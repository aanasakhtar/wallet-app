"""
End-to-end smoke test for Phase 1. Run with: python3 test_smoke.py
Uses a throwaway SQLite file so it never touches whatever DATABASE_URL
you've set for real use.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_smoke.db"

# Remove any leftover DB from a previous run so we start clean.
if os.path.exists("test_smoke.db"):
    os.remove("test_smoke.db")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"OK: {message}")


def signup_and_login(name, email, phone, password):
    r = client.post(
        "/auth/signup",
        json={"name": name, "email": email, "phone": phone, "password": password},
    )
    expect(r.status_code == 201, f"signup for {email} returns 201 (got {r.status_code}: {r.text})")

    r = client.post("/auth/login", data={"username": email, "password": password})
    expect(r.status_code == 200, f"login for {email} returns 200 (got {r.status_code}: {r.text})")
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def main():
    print("--- health check ---")
    r = client.get("/health")
    expect(r.status_code == 200 and r.json()["status"] == "ok", "health endpoint reports ok")

    print("\n--- signup + login ---")
    alice_headers = signup_and_login("Alice", "alice@example.com", "+92300000001", "password123")
    bob_headers = signup_and_login("Bob", "bob@example.com", "+92300000002", "password123")

    print("\n--- duplicate signup is rejected ---")
    r = client.post(
        "/auth/signup",
        json={"name": "Alice2", "email": "alice@example.com", "password": "password123"},
    )
    expect(r.status_code == 400, "duplicate email signup returns 400")

    print("\n--- wrong password is rejected ---")
    r = client.post("/auth/login", data={"username": "alice@example.com", "password": "wrong"})
    expect(r.status_code == 401, "wrong password returns 401")

    print("\n--- initial balances are zero ---")
    r = client.get("/wallet/balance", headers=alice_headers)
    expect(r.status_code == 200 and r.json()["balance"] == 0.0, "Alice starts with balance 0")

    print("\n--- top up ---")
    r = client.post("/wallet/topup", json={"amount": 1000}, headers=alice_headers)
    expect(r.status_code == 200 and r.json()["status"] == "completed", "top-up of 1000 completes")

    r = client.get("/wallet/balance", headers=alice_headers)
    expect(r.json()["balance"] == 1000.0, "Alice's balance is 1000 after top-up")

    print("\n--- transfer ---")
    r = client.post(
        "/transfers",
        json={"recipient": "bob@example.com", "amount": 250},
        headers=alice_headers,
    )
    expect(r.status_code == 200 and r.json()["status"] == "completed", "transfer of 250 completes")

    r = client.get("/wallet/balance", headers=alice_headers)
    expect(r.json()["balance"] == 750.0, "Alice's balance is 750 after sending 250")

    r = client.get("/wallet/balance", headers=bob_headers)
    expect(r.json()["balance"] == 250.0, "Bob's balance is 250 after receiving 250")

    print("\n--- insufficient balance is rejected ---")
    r = client.post(
        "/transfers",
        json={"recipient": "alice@example.com", "amount": 999999},
        headers=bob_headers,
    )
    expect(r.status_code == 400, "over-limit transfer returns 400")

    print("\n--- self-transfer is rejected ---")
    r = client.post(
        "/transfers",
        json={"recipient": "alice@example.com", "amount": 10},
        headers=alice_headers,
    )
    expect(r.status_code == 400, "self-transfer returns 400")

    print("\n--- transfer to unknown recipient is rejected ---")
    r = client.post(
        "/transfers",
        json={"recipient": "nobody@example.com", "amount": 10},
        headers=alice_headers,
    )
    expect(r.status_code == 400, "transfer to unknown recipient returns 400")

    print("\n--- transaction history ---")
    r = client.get("/transactions", headers=alice_headers)
    expect(r.status_code == 200, "Alice can fetch her transaction history")
    types = [t["type"] for t in r.json()]
    expect("topup" in types and "transfer" in types, f"Alice's history includes topup and transfer, got {types}")

    r = client.get("/transactions", headers=bob_headers)
    types = [t["type"] for t in r.json()]
    expect("transfer" in types, f"Bob's history includes the incoming transfer, got {types}")

    print("\n--- request ID header is present ---")
    expect("x-request-id" in r.headers, "responses carry an X-Request-ID header")

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
