"""Audit check: do the HTTP process (:8000) and the gRPC process (:8002) report the same,
current DB-derived gauges?  Needs both services running on the same database."""
import json, pathlib, sys, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from clients.grpc_client import create_transfer, http_login

B = "http://127.0.0.1:8000"
def post(p, b, h={}):
    r = urllib.request.Request(B + p, data=json.dumps(b).encode(), headers={"Content-Type": "application/json", **h})
    return json.loads(urllib.request.urlopen(r).read())
def gauge(port):
    t = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics").read().decode()
    g = lambda n: ([l.split()[1] for l in t.splitlines() if l.startswith(n + " ")] or ["(absent)"])[0]
    return {"money": g("wallet_money_in_system_pkr"), "users": g("wallet_registered_users")}

for n, ph in (("a", "+923001110011"), ("b", "+923001110012")):
    try: post("/auth/signup", {"name": n, "email": f"{n}@x.com", "phone": ph, "password": "password123"})
    except Exception: pass
tok = http_login(B, "a@x.com", "password123"); auth = {"Authorization": "Bearer " + tok}
post("/wallet/topups", {"amount": 1000}, auth)
print("1) after HTTP top-up of 1000:        HTTP process", gauge(8000), "| gRPC process", gauge(8002))
create_transfer("127.0.0.1:50051", tok, "b@x.com", 100)
print("2) after one gRPC transfer:          HTTP process", gauge(8000), "| gRPC process", gauge(8002))
post("/wallet/topups", {"amount": 500}, auth)
print("3) after another HTTP top-up of 500: HTTP process", gauge(8000), "| gRPC process", gauge(8002))
