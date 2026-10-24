"""
Python client for the CreateTransfer RPC (no browser, no HTTP endpoint involved).

    python -m clients.grpc_client --email alice@example.com --password password123 \
        --recipient bob@example.com --amount 250 --timeout 2

* The token: pass --token, or --email/--password and the client logs in once over
  HTTP (login is not part of the gRPC service) to obtain a bearer token.
* --timeout becomes the gRPC DEADLINE: if the server has not answered in that many
  seconds the call fails with DEADLINE_EXCEEDED (and the server is told to stop).
* The request id is sent as `x-request-id` metadata and printed, so the same id can be
  searched in logs/wallet-grpc.json (or Kibana) afterwards.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid

import grpc

import grpc_service  # noqa: F401  (puts generated/ on sys.path)
from wallet.v1 import wallet_pb2, wallet_pb2_grpc

DEFAULT_TARGET = os.getenv("GRPC_TARGET", "127.0.0.1:50051")
DEFAULT_HTTP = os.getenv("BASE_URL", "http://127.0.0.1:8000")


def http_login(base_url: str, email: str, password: str) -> str:
    data = urllib.parse.urlencode({"username": email, "password": password}).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{base_url}/auth/login", data=data), timeout=10) as r:
        return json.loads(r.read())["access_token"]


def create_transfer(target, token, recipient, amount, idempotency_key=None, timeout=2.0, request_id=None) -> dict:
    """Calls the RPC; always returns a dict (never raises for an RPC failure)."""
    request_id = request_id or str(uuid.uuid4())
    metadata = [("x-request-id", request_id)]
    if token:
        metadata.append(("authorization", f"Bearer {token}"))
    request = wallet_pb2.CreateTransferRequest(
        recipient=recipient, amount=amount, idempotency_key=idempotency_key or str(uuid.uuid4()))
    with grpc.insecure_channel(target) as channel:
        stub = wallet_pb2_grpc.TransferServiceStub(channel)
        try:
            response, call = stub.CreateTransfer.with_call(request, timeout=timeout, metadata=metadata)
            return {"ok": True, "status": "OK", "request_id": request_id,
                    "response": {f.name: v for f, v in response.ListFields()},
                    "replayed": response.replayed}
        except grpc.RpcError as err:
            trailing = dict(err.trailing_metadata() or ())
            return {"ok": False, "status": err.code().name, "message": err.details(),
                    "error_code": trailing.get("x-error-code"), "request_id": request_id}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=DEFAULT_TARGET, help="gRPC host:port")
    ap.add_argument("--http-base", default=DEFAULT_HTTP, help="HTTP base URL, used only to log in")
    ap.add_argument("--token")
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--recipient", required=True)
    ap.add_argument("--amount", type=float, required=True)
    ap.add_argument("--idempotency-key", help="default: a fresh UUID")
    ap.add_argument("--timeout", type=float, default=2.0, help="deadline in seconds")
    ap.add_argument("--request-id")
    a = ap.parse_args()

    token = a.token or (http_login(a.http_base, a.email, a.password) if a.email else None)
    result = create_transfer(a.target, token, a.recipient, a.amount, a.idempotency_key, a.timeout, a.request_id)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
