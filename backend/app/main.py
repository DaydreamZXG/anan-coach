from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.routing import iter_route_contexts
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.api.router import router
from app.core.config import get_settings
from app.domains.commerce.stage8_repository import (
    Stage8CommerceConflict,
    Stage8CommerceNotFound,
)
from app.domains.commerce.stage8_router import stage8_exception_response
from app.domains.commerce.stage8_service import (
    Stage8CommerceUnavailable,
    Stage8PaymentPending,
    Stage8RefundIneligible,
)
from app.domains.commerce.wechat_pay import (
    WechatPayConfigurationError,
    WechatPayProtocolError,
)
from app.domains.data_rights.repository import DataRightsConflict, DataRightsNotFound
from app.domains.data_rights.router import data_rights_exception_response
from app.platform.http import (
    REQUEST_ID_HEADER,
    http_exception_response,
    resolve_request_id,
    unexpected_exception_response,
    validation_exception_response,
)


API_CACHE_CONTROL = "no-store"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def apply_http_contract(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        if (
            request.url.path == "/health"
            or request.url.path.startswith("/api/")
            or request.url.path.startswith("/internal-runtime/")
        ):
            response.headers["Cache-Control"] = API_CACHE_CONTROL
        return response

    app.add_exception_handler(StarletteHTTPException, http_exception_response)
    app.add_exception_handler(RequestValidationError, validation_exception_response)
    app.add_exception_handler(Exception, unexpected_exception_response)
    for exception_type in (
        Stage8CommerceConflict,
        Stage8CommerceUnavailable,
        Stage8CommerceNotFound,
        Stage8PaymentPending,
        Stage8RefundIneligible,
        WechatPayConfigurationError,
        WechatPayProtocolError,
    ):
        app.add_exception_handler(exception_type, stage8_exception_response)
    for exception_type in (DataRightsConflict, DataRightsNotFound):
        app.add_exception_handler(exception_type, data_rights_exception_response)
    app.include_router(router)
    # FastAPI builds the included-router candidate graph lazily. Materialize the
    # public route-context iterator while construction is still single-threaded
    # so concurrent first requests cannot observe a partial candidate cache.
    tuple(iter_route_contexts(app.routes))
    return app


app = create_app()
