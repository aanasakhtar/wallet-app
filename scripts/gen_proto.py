"""Regenerates generated/wallet/v1/*_pb2*.py from proto/wallet/v1/wallet.proto.

Equivalent to:
    python -m grpc_tools.protoc -I proto --python_out=generated \
        --grpc_python_out=generated --pyi_out=generated proto/wallet/v1/wallet.proto
"""
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent.parent
out = root / "generated"
out.mkdir(exist_ok=True)
subprocess.check_call([
    sys.executable, "-m", "grpc_tools.protoc", "-I", str(root / "proto"),
    f"--python_out={out}", f"--grpc_python_out={out}", f"--pyi_out={out}",
    str(root / "proto" / "wallet" / "v1" / "wallet.proto"),
])
for pkg in (out / "wallet", out / "wallet" / "v1"):  # protoc does not create these
    (pkg / "__init__.py").touch()
print("generated:", *sorted(p.name for p in (out / "wallet" / "v1").glob("*.py*")))
