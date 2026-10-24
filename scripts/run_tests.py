"""Runs every test script in its own interpreter (each uses its own throwaway DB and log dir)."""
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent.parent
tests = ["test_smoke.py", "test_observability.py", "test_api.py", "test_openapi.py", "test_grpc.py", "test_resilient_client.py", "test_grpc_metrics.py"]
failed = []
for t in tests:
    r = subprocess.run([sys.executable, t], cwd=root, capture_output=True, text=True)
    ok = r.returncode == 0
    n = sum(1 for l in r.stdout.splitlines() if l.startswith("OK:"))
    print(f"{'PASS' if ok else 'FAIL'}  {t:<28} {n} checks")
    if not ok:
        failed.append(t)
        print(r.stdout[-1500:], r.stderr[-1500:])
sys.exit(1 if failed else 0)
