"""
OpenAPI document customisation.

FastAPI generates the spec from the route definitions, so it cannot drift from
the code - but two defaults would make it LIE about this API:
  * it documents 422 as FastAPI's own HTTPValidationError, while we return the
    shared error envelope (see error_handlers.py);
  * operationIds default to names like create_transfer_transfers_post.
Both are fixed here. openapi/wallet-api.yaml is exported from this function by
scripts/export_openapi.py and checked for drift by test_openapi.py.
"""
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute


def unique_id(route: APIRoute) -> str:
    return route.name  # e.g. "create_transfer"


def install_openapi(app: FastAPI) -> None:
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description,
            routes=app.routes, servers=[{"url": "http://localhost:8000", "description": "local"}],
        )
        for path_item in schema["paths"].values():
            for operation in path_item.values():
                resp = operation.get("responses", {})
                if "422" in resp:
                    resp["422"] = dict(resp["422"]) if resp["422"].get("content") else resp["422"]
                    resp["422"]["description"] = resp["422"].get("description") or "Validation error"
                    resp["422"]["content"] = {"application/json": {
                        "schema": {"$ref": "#/components/schemas/ErrorResponse"}}}
        components = schema.get("components", {}).get("schemas", {})
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
