from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.domains.auth.deps import require_bearer_token
from app.main import create_app

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})
BEARER_SCHEME_NAME = "BearerAuth"


def _route_contexts(app: FastAPI) -> Iterator[object]:
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        effective_route_contexts = getattr(route, "effective_route_contexts", None)
        if callable(effective_route_contexts):
            yield from effective_route_contexts()


def _dependency_calls(dependant: object) -> Iterator[object]:
    call = getattr(dependant, "call", None)
    if call is not None:
        yield call
    for child in getattr(dependant, "dependencies", ()):
        yield from _dependency_calls(child)


def _code_bearer_operations(app: FastAPI) -> set[tuple[str, str]]:
    operations: set[tuple[str, str]] = set()
    for route in _route_contexts(app):
        if not getattr(route, "include_in_schema"):
            continue
        calls = set(_dependency_calls(getattr(route, "dependant")))
        if require_bearer_token not in calls:
            continue
        path = getattr(route, "path")
        for method in getattr(route, "methods"):
            normalized_method = method.lower()
            if normalized_method in HTTP_METHODS:
                operations.add((path, normalized_method))
    return operations


def _openapi_bearer_operations(schema: dict[str, object]) -> set[tuple[str, str]]:
    operations: set[tuple[str, str]] = set()
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            security = operation.get("security", [])
            if any(BEARER_SCHEME_NAME in requirement for requirement in security):
                operations.add((path, method))
    return operations


def test_openapi_bearer_operations_match_code_dependency_graph() -> None:
    app = create_app()
    schema = app.openapi()

    assert schema["components"]["securitySchemes"] == {
        BEARER_SCHEME_NAME: {
            "type": "http",
            "description": "Opaque access token issued by the authentication endpoints.",
            "scheme": "bearer",
        }
    }
    code_operations = _code_bearer_operations(app)
    assert len(code_operations) == 231
    assert {
        ("/api/v2/xfyun/speech-to-text", "post"),
        ("/api/v2/xfyun/text-to-speech", "post"),
        ("/api/v2/xingce/question-favorites", "get"),
        ("/api/v2/xingce/question-favorites/{question_id}", "get"),
        ("/api/v2/xingce/question-favorites/{question_id}", "put"),
    }.issubset(code_operations)
    assert _openapi_bearer_operations(schema) == code_operations
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            if (path, method) in code_operations:
                assert operation["security"] == [{BEARER_SCHEME_NAME: []}]
                assert all(
                    parameter.get("name") != "Authorization"
                    for parameter in operation.get("parameters", [])
                )
            else:
                assert "security" not in operation


def test_bearer_metadata_does_not_change_header_validation_semantics() -> None:
    app = FastAPI()

    @app.get("/protected")
    def protected(access_token: str = Depends(require_bearer_token)) -> dict[str, str]:
        return {"accessToken": access_token}

    client = TestClient(app)

    missing = client.get("/protected")
    assert missing.status_code == 401
    assert missing.json() == {"detail": "Authorization bearer token is required"}

    malformed = client.get("/protected", headers={"Authorization": "Basic credentials"})
    assert malformed.status_code == 401
    assert malformed.json() == {"detail": "Authorization bearer token is invalid"}

    accepted = client.get("/protected", headers={"Authorization": "bEaReR opaque-token"})
    assert accepted.status_code == 200
    assert accepted.json() == {"accessToken": "opaque-token"}
