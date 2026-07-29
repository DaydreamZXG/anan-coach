from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar
from uuid import uuid4

from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette import status
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response


REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
T = TypeVar("T")


class PublicApiEnvelope(BaseModel, Generic[T]):
    model_config = ConfigDict(populate_by_name=True)

    data: T
    request_id: str = Field(alias="requestId")
    generated_at: datetime = Field(alias="generatedAt")


class PublicApiError(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    message: str
    request_id: str = Field(alias="requestId")
    details: dict[str, Any] | None = None


COMMON_API_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {
        "model": PublicApiError,
        "description": "The request is invalid for the current business state.",
    },
    status.HTTP_401_UNAUTHORIZED: {
        "model": PublicApiError,
        "description": "Authentication is required or no longer valid.",
    },
    status.HTTP_403_FORBIDDEN: {
        "model": PublicApiError,
        "description": "The authenticated account cannot perform this action.",
    },
    status.HTTP_404_NOT_FOUND: {
        "model": PublicApiError,
        "description": "The requested resource does not exist.",
    },
    status.HTTP_409_CONFLICT: {
        "model": PublicApiError,
        "description": "The request conflicts with the current resource revision.",
    },
    422: {
        "model": PublicApiError,
        "description": "The request body or parameters failed validation.",
    },
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "model": PublicApiError,
        "description": "The request exceeded a controlled rate or quota.",
    },
    status.HTTP_500_INTERNAL_SERVER_ERROR: {
        "model": PublicApiError,
        "description": "The server could not complete the request.",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": PublicApiError,
        "description": "A required service is temporarily unavailable.",
    },
}


def resolve_request_id(provided: str | None) -> str:
    if provided and REQUEST_ID_PATTERN.fullmatch(provided):
        return provided
    return f"api-{uuid4()}"


def request_id_from(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and REQUEST_ID_PATTERN.fullmatch(request_id):
        return request_id
    return resolve_request_id(request.headers.get(REQUEST_ID_HEADER))


def public_error_response(
    *,
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_from(request)
    payload = PublicApiError(
        code=code,
        message=message,
        requestId=request_id,
        details=details,
    )
    response_headers = {REQUEST_ID_HEADER: request_id}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        headers=response_headers,
        content=payload.model_dump(by_alias=True, exclude_none=True),
    )


def public_success_envelope(
    *,
    request: Request,
    response: Response,
    data: T,
) -> PublicApiEnvelope[T]:
    request_id = request_id_from(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return PublicApiEnvelope(
        data=data,
        request_id=request_id,
        generated_at=datetime.now(UTC),
    )


def http_exception_response(request: Request, exc: HTTPException) -> JSONResponse:
    if not request.url.path.startswith("/api/v2/"):
        return JSONResponse(
            status_code=exc.status_code,
            headers={REQUEST_ID_HEADER: request_id_from(request)},
            content={"detail": jsonable_encoder(exc.detail)},
        )
    code, message = _public_http_error(exc.status_code)
    return public_error_response(
        request=request,
        status_code=exc.status_code,
        code=code,
        message=message,
    )


def validation_exception_response(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    if not request.url.path.startswith("/api/v2/"):
        return JSONResponse(
            status_code=422,
            headers={REQUEST_ID_HEADER: request_id_from(request)},
            content={"detail": jsonable_encoder(exc.errors())},
        )
    errors = [
        {
            "location": [str(part) for part in error["loc"]],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return public_error_response(
        request=request,
        status_code=422,
        code="invalid_request",
        message="请求内容不完整或格式不正确。",
        details={"errors": errors},
    )


def unexpected_exception_response(request: Request, _: Exception) -> JSONResponse:
    return public_error_response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="服务暂时无法完成请求。",
    )


def database_resource_exception_response(request: Request, _: Exception) -> JSONResponse:
    return public_error_response(
        request=request,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="database_busy",
        message="当前请求较多，请稍后重试。",
        headers={"Retry-After": "1"},
    )


def _public_http_error(status_code: int) -> tuple[str, str]:
    errors = {
        status.HTTP_400_BAD_REQUEST: ("bad_request", "请求无法处理。"),
        status.HTTP_401_UNAUTHORIZED: ("authentication_required", "登录状态已失效，请重新登录。"),
        status.HTTP_403_FORBIDDEN: ("forbidden", "当前账户不能执行此操作。"),
        status.HTTP_404_NOT_FOUND: ("not_found", "请求的内容不存在。"),
        status.HTTP_405_METHOD_NOT_ALLOWED: ("method_not_allowed", "当前请求方式不受支持。"),
        status.HTTP_409_CONFLICT: ("revision_conflict", "数据已经更新，请刷新后重试。"),
        status.HTTP_429_TOO_MANY_REQUESTS: ("rate_limited", "请求过于频繁，请稍后重试。"),
        status.HTTP_503_SERVICE_UNAVAILABLE: ("service_unavailable", "所需服务暂时不可用。"),
    }
    return errors.get(status_code, ("request_failed", "请求未能完成。"))
