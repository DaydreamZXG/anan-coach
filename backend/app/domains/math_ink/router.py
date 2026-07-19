from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.math_ink.provider import MyScriptConfigurationError, MyScriptMathClient
from app.domains.math_ink.repository import (
    MathInkConfigurationError,
    MathInkConflict,
    MathInkNotFound,
    MathInkRepository,
)
from app.domains.math_ink.schemas import (
    ConfirmFormulaCommand,
    FormulaVersion,
    MathInkRecognitionResult,
    RecognizeMathInkCommand,
)
from app.domains.math_ink.service import MathInkService
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/math-ink",
    tags=["math-ink"],
    responses=COMMON_API_ERROR_RESPONSES,
)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$",
    ),
]


def _service(session: Session, *, requires_provider: bool = True) -> MathInkService:
    settings = get_settings()
    provider = None
    if requires_provider:
        provider = MyScriptMathClient(
            endpoint=settings.myscript_endpoint,
            host_allowlist=settings.myscript_https_host_allowlist,
            application_key=settings.myscript_application_key,
            hmac_key=settings.myscript_hmac_key,
        )
    return MathInkService(
        MathInkRepository(session, settings),
        provider,
    )


@router.post(
    "/recognitions", response_model=PublicApiEnvelope[MathInkRecognitionResult]
)
def recognize_formula(
    command: RecognizeMathInkCommand,
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[MathInkRecognitionResult]:
    try:
        result = _service(session).recognize(
            owner_user_id=current_user.user_id,
            command=command,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except (MyScriptConfigurationError, MathInkConfigurationError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=503, detail="数学手写识别尚未完成服务端配置。"
        ) from exc
    except MathInkConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/formulas/{formula_version_id}/confirm",
    response_model=PublicApiEnvelope[FormulaVersion],
)
def confirm_formula(
    formula_version_id: UUID,
    command: ConfirmFormulaCommand,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[FormulaVersion]:
    try:
        result = _service(session, requires_provider=False).confirm(
            owner_user_id=current_user.user_id,
            formula_version_id=formula_version_id,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except MathInkNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="公式候选不存在。") from exc
    except MathInkConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
