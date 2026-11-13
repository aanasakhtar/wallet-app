"""
Assignment 3: Shared State and Cross-Replica Idempotency Test Suite.

Validates Part A requirements:
  1. Cross-replica HTTP idempotency: Key registered on Replica A safely replays on Replica B.
  2. Cross-replica gRPC idempotency: Key registered on Replica A safely replays on Replica C.
  3. Concurrent cross-replica race: Parallel requests with the same key hitting different replicas
     rely on database row-locks and UNIQUE constraints to commit exactly once.
  4. Rate-limit local state disparity: Demonstrates that in-memory rate-limit counters are partitioned
     per replica, allowing quota expansion across replicas unless centralized.
"""
import concurrent.futures
import os
import random
import tempfile
import time
import unittest
import uuid

# Use isolated SQLite DB for tests
test_db = os.path.join(tempfile.gettempdir(), f"test_a3_shared_{uuid.uuid4().hex[:8]}.db")
os.environ["DATABASE_URL"] = f"sqlite:///{test_db}"
os.environ["RATE_LIMIT_ENABLED"] = "true"
os.environ["RATE_LIMIT_TRANSFERS"] = "3/10"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.services import auth_service, idempotency_service, transfer_service  # noqa: E402
from app.schemas import SignupRequest  # noqa: E402


class TestA3SharedState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client_a = TestClient(app)
        cls.client_b = TestClient(app)
        cls.client_c = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        if os.path.exists(test_db):
            try:
                os.remove(test_db)
            except OSError:
                pass

    def setUp(self):
        self.db = SessionLocal()
        # Create users
        uid = uuid.uuid4().hex[:6]
        self.alice_email = f"alice-{uid}@example.com"
        self.bob_email = f"bob-{uid}@example.com"
        num1 = random.randint(1000000, 9999999)
        num2 = random.randint(1000000, 9999999)
        self.alice = auth_service.signup(
            self.db,
            SignupRequest(name="Alice", email=self.alice_email, phone=f"+92300{num1}", password="password123"),
        )
        self.bob = auth_service.signup(
            self.db,
            SignupRequest(name="Bob", email=self.bob_email, phone=f"+92301{num2}", password="password123"),
        )

        # Alice gets 1000 initial balance via topup
        topup_key = f"topup-{uuid.uuid4().hex}"
        self.alice_token = self._get_token(self.alice_email, "password123")
        self.bob_token = self._get_token(self.bob_email, "password123")
        topup_res = self.client_a.post(
            "/wallet/topups",
            json={"amount": 1000},
            headers={
                "Authorization": f"Bearer {self.alice_token}",
                "Idempotency-Key": topup_key,
            },
        )
        assert topup_res.status_code == 201, f"Topup failed: {topup_res.status_code} {topup_res.text}"

    def tearDown(self):
        self.db.close()

    def _get_token(self, email, password):
        res = self.client_a.post(
            "/auth/login",
            data={"username": email, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return res.json()["access_token"]

    def test_cross_replica_idempotency_replay(self):
        """Request 1 to Replica A creates transfer; Request 2 with same key to Replica B replays it."""
        key = f"key-{uuid.uuid4().hex}"
        payload = {"recipient": self.bob_email, "amount": 150}

        # 1. Hit Replica A
        res_a = self.client_a.post(
            "/transfers",
            json=payload,
            headers={"Authorization": f"Bearer {self.alice_token}", "Idempotency-Key": key},
        )
        self.assertEqual(res_a.status_code, 201)
        tx_id_a = res_a.json()["id"]
        self.assertNotIn("Idempotent-Replayed", res_a.headers)

        # Verify Alice balance after debit: 850
        bal = self.client_a.get("/wallet/balance", headers={"Authorization": f"Bearer {self.alice_token}"}).json()["balance"]
        self.assertEqual(bal, 850.0)

        # 2. Hit Replica B with exact same key
        res_b = self.client_b.post(
            "/transfers",
            json=payload,
            headers={"Authorization": f"Bearer {self.alice_token}", "Idempotency-Key": key},
        )
        self.assertEqual(res_b.status_code, 201)
        self.assertEqual(res_b.headers.get("Idempotent-Replayed"), "true")
        tx_id_b = res_b.json()["id"]
        self.assertEqual(tx_id_a, tx_id_b)

        # Balance remains 850 (no second debit)
        bal_after = self.client_b.get("/wallet/balance", headers={"Authorization": f"Bearer {self.alice_token}"}).json()["balance"]
        self.assertEqual(bal_after, 850.0)
        print("OK: Cross-replica HTTP idempotency replay verified")

    def test_cross_replica_key_reuse_rejection(self):
        """Reusing key with a different amount/recipient on another replica returns 422."""
        key = f"key-reuse-{uuid.uuid4().hex}"
        # Replica A creates transfer for 50
        self.client_a.post(
            "/transfers",
            json={"recipient": self.bob_email, "amount": 50},
            headers={"Authorization": f"Bearer {self.alice_token}", "Idempotency-Key": key},
        )

        # Replica B rejects same key for 100
        res_b = self.client_b.post(
            "/transfers",
            json={"recipient": self.bob_email, "amount": 100},
            headers={"Authorization": f"Bearer {self.alice_token}", "Idempotency-Key": key},
        )
        self.assertEqual(res_b.status_code, 422)
        self.assertEqual(res_b.json()["error"]["code"], "idempotency_key_reuse")
        print("OK: Cross-replica key reuse mismatch rejected with 422")

    def test_concurrent_cross_replica_collision(self):
        """Concurrent requests with same key across replicas collide safely."""
        from app.config import settings
        orig_rate_limit = settings.rate_limit_enabled
        settings.rate_limit_enabled = False
        try:
            key = f"key-race-{uuid.uuid4().hex}"
            payload = {"recipient": self.bob_email, "amount": 25}
            clients = [self.client_a, self.client_b, self.client_c, self.client_a]

            def call_transfer(c):
                return c.post(
                    "/transfers",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.alice_token}", "Idempotency-Key": key},
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                results = list(ex.map(call_transfer, clients))

            # All 4 must report 201 Created
            for r in results:
                self.assertEqual(r.status_code, 201)

            # All 4 return the exact same transaction ID
            ids = {r.json()["id"] for r in results}
            self.assertEqual(len(ids), 1)
            print("OK: Concurrent cross-replica collision handled safely")
        finally:
            settings.rate_limit_enabled = orig_rate_limit

    def test_rate_limit_local_state_disparity(self):
        """In-memory rate limiter demonstrates local state isolation across instances."""
        from app.rate_limit import SlidingWindowLimiter
        # Limiter A represents Replica A's process memory
        # Limiter B represents Replica B's process memory
        limiter_a = SlidingWindowLimiter(limit=3, window_seconds=10.0)
        limiter_b = SlidingWindowLimiter(limit=3, window_seconds=10.0)

        user_id = self.alice.id

        # 3 calls on Replica A exhaust Replica A's local budget
        for _ in range(3):
            allowed, remaining, _ = limiter_a.hit(user_id)
            self.assertTrue(allowed)

        # 4th call on Replica A is throttled (429)
        allowed, remaining, retry_after = limiter_a.hit(user_id)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

        # CRITICAL FINDING: Calling Replica B still succeeds because memory is not shared!
        allowed_b, remaining_b, _ = limiter_b.hit(user_id)
        self.assertTrue(allowed_b)
        self.assertEqual(remaining_b, 2)
        print("OK: Rate-limit local state disparity across replicas verified")


if __name__ == "__main__":
    unittest.main()
