"""gRPC front end for the wallet.

The generated stubs live in ./generated (package `wallet.v1`). Importing this
package puts that directory on sys.path so `from wallet.v1 import wallet_pb2`
works from the server, the clients and the tests alike.
"""
import pathlib
import sys

_GENERATED = str(pathlib.Path(__file__).resolve().parent.parent / "generated")
if _GENERATED not in sys.path:
    sys.path.insert(0, _GENERATED)
