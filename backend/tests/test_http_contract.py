from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.requests import Request

from app.main import create_app
from app.platform.http import public_success_response


class ContractRequest(BaseModel):
    code: str = Field(min_length=1)
    device_id: str = Field(alias="deviceId", min_length=1)


def test_request_id_is_echoed_for_successful_requests() -> None:
    response = TestClient(create_app()).get(
        "/health",
        headers={"X-Request-ID": "mp-health-contract-1"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "mp-health-contract-1"


def test_direct_success_response_encodes_public_envelope_without_route_serialization() -> None:
    app = create_app()

    @app.get("/api/v2/_direct-success-test")
    def direct_success(request: Request):
        return public_success_response(request=request, data={"answer": "可以学习资料分析"})

    response = TestClient(app).get(
        "/api/v2/_direct-success-test",
        headers={"X-Request-ID": "mp-direct-success-1"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "mp-direct-success-1"
    assert response.json()["data"] == {"answer": "可以学习资料分析"}


def test_invalid_request_id_is_replaced() -> None:
    response = TestClient(create_app()).get(
        "/health",
        headers={"X-Request-ID": "contains spaces and is invalid"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"].startswith("api-")


def test_api_validation_errors_use_public_contract() -> None:
    app = create_app()

    @app.post("/api/v2/_contract-test")
    def contract_test(payload: ContractRequest) -> dict[str, bool]:
        return {"ok": bool(payload.code)}

    response = TestClient(app).post(
        "/api/v2/_contract-test",
        headers={"X-Request-ID": "mp-validation-contract-1"},
        json={"code": ""},
    )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "mp-validation-contract-1"
    assert response.json() == {
        "code": "invalid_request",
        "message": "请求内容不完整或格式不正确。",
        "requestId": "mp-validation-contract-1",
        "details": {
            "errors": [
                {"location": ["body", "code"], "type": "string_too_short"},
                {"location": ["body", "deviceId"], "type": "missing"},
            ]
        },
    }


def test_api_not_found_uses_public_contract() -> None:
    response = TestClient(create_app()).get(
        "/api/v2/does-not-exist",
        headers={"X-Request-ID": "mp-not-found-contract-1"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "请求的内容不存在。",
        "requestId": "mp-not-found-contract-1",
    }


def test_database_saturation_returns_retryable_public_contract() -> None:
    app = create_app()

    @app.get("/api/v2/_database-timeout-test")
    def database_timeout_test() -> None:
        raise SQLAlchemyTimeoutError("pool exhausted")

    response = TestClient(app).get(
        "/api/v2/_database-timeout-test",
        headers={"X-Request-ID": "mp-database-busy-1"},
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json() == {
        "code": "database_busy",
        "message": "当前请求较多，请稍后重试。",
        "requestId": "mp-database-busy-1",
    }


def test_openapi_exposes_shared_error_schema() -> None:
    schema = create_app().openapi()
    operation = schema["paths"]["/api/v2/auth/wechat"]["post"]

    error_schema = operation["responses"]["422"]["content"]["application/json"]["schema"]
    assert error_schema == {"$ref": "#/components/schemas/PublicApiError"}
    properties = schema["components"]["schemas"]["PublicApiError"]["properties"]
    assert set(properties) == {"code", "message", "requestId", "details"}
