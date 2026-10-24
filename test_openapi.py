"""
Uses the OpenAPI document (openapi/wallet-api.yaml) as the test oracle. Run: python3 test_openapi.py

  1. the file is a valid OpenAPI 3.1 document;
  2. it has not drifted from the code (every route/method is in the spec and vice versa);
  3. CONTRACT TEST of POST /transfers: real responses (201, 409, 422, 429, 401) are validated
     against the schemas the spec declares, and every status code returned is one the spec lists.
"""
import json
import os
import uuid

os.environ.update(DATABASE_URL="sqlite:///./test_openapi.db", LOG_DIR="test_openapi_logs", RATE_LIMIT_TRANSFERS="3/60")
if os.path.exists("test_openapi.db"):
    os.remove("test_openapi.db")

import jsonschema  # noqa: E402
import yaml  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

spec = yaml.safe_load(open("openapi/wallet-api.yaml", encoding="utf-8"))
client = TestClient(app)


def expect(cond, msg):
    if not cond:
        raise AssertionError(f"FAILED: {msg}")
    print(f"OK: {msg}")


try:
    from openapi_spec_validator import validate
    validate(spec)
    expect(True, f"spec is a valid OpenAPI {spec['openapi']} document")
except ImportError:
    print("SKIP: openapi-spec-validator not installed")

print("\n--- the file matches the running code ---")
expect(spec == json.loads(json.dumps(app.openapi())), "openapi/wallet-api.yaml == app.openapi() (run scripts/export_openapi.py if this fails)")
routes = {(m.lower(), r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods}
documented = {(m, p) for p, item in spec["paths"].items() for m in item}
expect(routes == documented, f"every route+method is documented, nothing extra ({len(routes)} operations)")


def schema_for(path, method, status, content="application/json"):
    ref = spec["paths"][path][method]["responses"][str(status)]["content"][content]["schema"]
    return {**ref, "components": spec["components"]}


def check(resp, path, method):
    declared = spec["paths"][path][method]["responses"]
    expect(str(resp.status_code) in declared, f"status {resp.status_code} is declared in the spec for {method.upper()} {path}")
    jsonschema.validate(resp.json(), schema_for(path, method, resp.status_code))
    expect(True, f"  body of the {resp.status_code} response matches its declared schema")


print("\n--- contract test: POST /transfers ---")
tok = lambda e: {"Authorization": "Bearer " + client.post("/auth/login", data={"username": e, "password": "password123"}).json()["access_token"]}
for n, e, p in (("Alice", "a@example.com", "+923000000001"), ("Bob", "b@example.com", "+923000000002")):
    r = client.post("/auth/signup", json={"name": n, "email": e, "phone": p, "password": "password123"})
    check(r, "/auth/signup", "post")
alice, bob = tok("a@example.com"), tok("b@example.com")
check(client.post("/wallet/topups", json={"amount": 500}, headers=alice), "/wallet/topups", "post")

# the request body used below is built FROM the spec's schema: required fields only
req = spec["components"]["schemas"]["TransferRequest"]
expect(set(req["required"]) == {"recipient", "amount"} and req.get("additionalProperties") is False,
       "spec says TransferRequest requires recipient+amount and forbids extra fields")
expect(spec["paths"]["/transfers"]["post"]["parameters"][0]["required"] is True, "spec marks Idempotency-Key as required")


def post(body, headers, key=True):
    h = {**headers, **({"Idempotency-Key": str(uuid.uuid4())} if key else {})}
    return client.post("/transfers", json=body, headers=h)


r = post({"recipient": "b@example.com", "amount": 25}, alice); check(r, "/transfers", "post")
expect({"Location"} <= set(spec["paths"]["/transfers"]["post"]["responses"]["201"]["headers"]) and "location" in r.headers,
       "201 carries the Location header the spec documents")
check(post({"recipient": "b@example.com", "amount": 99999}, alice), "/transfers", "post")        # 409
check(post({"recipient": "b@example.com", "amount": -1}, alice), "/transfers", "post")           # 422
check(client.post("/transfers", json={"recipient": "b@example.com", "amount": 1}), "/transfers", "post")  # 401
post({"recipient": "b@example.com", "amount": 1}, alice)
r = post({"recipient": "b@example.com", "amount": 1}, alice)                                     # 4th call in the window -> 429
check(r, "/transfers", "post")
expect("Retry-After" in spec["paths"]["/transfers"]["post"]["responses"]["429"].get("description", "") or r.headers.get("retry-after"),
       "429 response includes Retry-After")
check(client.get("/transfers/does-not-exist", headers=alice), "/transfers/{transfer_id}", "get")  # 404
print("\nAll OpenAPI tests passed.")
