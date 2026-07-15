from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.main import create_app


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


def test_openapi_exposes_shared_error_schema() -> None:
    schema = create_app().openapi()
    operation = schema["paths"]["/api/v2/auth/wechat"]["post"]

    error_schema = operation["responses"]["422"]["content"]["application/json"]["schema"]
    assert error_schema == {"$ref": "#/components/schemas/PublicApiError"}
    properties = schema["components"]["schemas"]["PublicApiError"]["properties"]
    assert set(properties) == {"code", "message", "requestId", "details"}
