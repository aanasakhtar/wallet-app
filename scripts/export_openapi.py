"""Exports the OpenAPI document to openapi/wallet-api.yaml.

    python scripts/export_openapi.py          # rewrite the file
    python scripts/export_openapi.py --check  # exit 1 if the committed file is out of date

The spec is generated from the route definitions (app/openapi.py), so it matches the
code; test_openapi.py additionally checks the committed file has not drifted.
"""
import json
import os
import pathlib
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///./openapi_export.db")
os.environ.setdefault("LOG_DIR", "openapi_export_logs")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402
from app.main import app  # noqa: E402

target = pathlib.Path(__file__).resolve().parent.parent / "openapi" / "wallet-api.yaml"
spec = json.loads(json.dumps(app.openapi()))  # normalise to plain JSON types
text = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)

if "--check" in sys.argv:
    sys.exit(0 if target.exists() and yaml.safe_load(target.read_text()) == spec else 1)
target.parent.mkdir(exist_ok=True)
target.write_text(text, encoding="utf-8")
print(f"wrote {target} ({len(spec['paths'])} paths)")
