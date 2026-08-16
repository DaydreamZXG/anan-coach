from __future__ import annotations

from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from .catalog import DemoCatalog, LicensedProductionCatalog
from .config import Settings
from .payment import FakePaymentGateway, PaymentOrder, demo_payment_required
from .service import QBankService, ServiceError
from .store import Store


class MembershipRequest(BaseModel):
    plan: Literal["week", "month"]
    requestId: str = Field(min_length=8)


class SessionRequest(BaseModel):
    requestId: str = Field(min_length=8)
    kind: Literal["practice", "mock"]
    exam: Literal["guokao", "shengkao", "sydw"]
    questionCount: Optional[int] = Field(default=None, ge=5, le=20)
    templateId: Optional[str] = None

    @model_validator(mode="after")
    def validate_kind_fields(self):
        if self.kind == "practice" and self.templateId is not None:
            raise ValueError("practice does not accept templateId")
        if self.kind == "mock" and self.questionCount is not None:
            raise ValueError("mock uses its complete template and does not accept questionCount")
        return self


class SubmittedAnswer(BaseModel):
    questionId: str
    answer: str


class SubmitRequest(BaseModel):
    requestId: str = Field(min_length=8)
    answers: list[SubmittedAnswer]


def create_app(
    settings: Optional[Settings] = None,
    *,
    gateway: Optional[FakePaymentGateway] = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    payment_gateway = gateway or FakePaymentGateway()
    catalog = DemoCatalog() if config.catalog_mode == "demo" else LicensedProductionCatalog()
    service = QBankService(
        store=Store(config.database_path),
        catalog=catalog,
        gateway=payment_gateway,
        api_key_secret=config.api_key_secret,
        rate_limit=config.rate_limit_per_minute,
    )
    app = FastAPI(
        title="岸岸题库云 API",
        version="0.1.0",
        description="为国考、省考和事业编应用提供会话式练习与完整套卷。",
    )
    app.state.service = service
    app.state.gateway = payment_gateway

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, error: ServiceError):
        return JSONResponse(status_code=error.status, content={"code": error.code, "message": str(error)})

    def bearer(authorization: Optional[str] = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise ServiceError("UNAUTHORIZED", 401, "Authorization: Bearer <api-key> is required")
        return authorization[7:]

    def principal(api_key: str = Depends(bearer)) -> dict:
        return service.authenticate(api_key)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "catalogMode": catalog.mode, "livePayment": False}

    @app.post("/v1/membership/invoke")
    def membership(
        payload: MembershipRequest,
        x_out_trade_no: Optional[str] = Header(default=None, alias="X-Out-Trade-No"),
    ):
        if x_out_trade_no:
            return service.fulfill_order(x_out_trade_no)
        order = service.start_order(payload.plan, payload.requestId)
        payment_required = demo_payment_required(
            PaymentOrder(order["out_trade_no"], order["payment_code"]),
            order["amount_minor"],
            order["plan"],
        )
        return JSONResponse(
            status_code=402,
            headers={"WeixinPay-Required": payment_required, "X-Out-Trade-No": order["out_trade_no"]},
            content={"code": "PAYMENT_REQUIRED", "outTradeNo": order["out_trade_no"], "amountMinor": order["amount_minor"], "currency": "CNY", "demo": True},
        )

    @app.post("/v1/sessions")
    def create_session(payload: SessionRequest, user: dict = Depends(principal)):
        return service.create_session(user["key_id"], payload.model_dump(exclude_none=True))

    @app.get("/v1/sessions/{session_id}/questions")
    def read_page(
        session_id: str,
        user: dict = Depends(principal),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=20),
    ):
        return service.page(user["key_id"], session_id, offset, limit)

    @app.post("/v1/sessions/{session_id}/submit")
    def submit(session_id: str, payload: SubmitRequest, user: dict = Depends(principal)):
        return service.submit(
            user["key_id"], session_id, payload.requestId,
            [item.model_dump() for item in payload.answers],
        )

    return app


app = create_app()
