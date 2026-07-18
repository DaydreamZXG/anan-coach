from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import RecognitionRevision
from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.ability_estimation.service import AbilityEstimateContractError
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    RecognitionError,
    RecognitionNotFound,
)
from app.domains.reporting.external_reports import (
    ExternalMockReportError,
    ExternalMockReportNotFound,
    ExternalMockReportService,
)
from app.domains.reporting.external_score_ocr import ExternalScoreReportOcrService
from app.domains.reporting.schemas import (
    ExternalMockReportConfirmCommand,
    ExternalMockReportDraftCommand,
    ExternalMockReportProjection,
    ExternalScoreRecognitionCorrectionCommand,
    ExternalScoreRecognitionDecisionCommand,
    ExternalScoreRecognitionStartCommand,
    ReportCenterProjection,
    ReportProjection,
)
from app.domains.reporting.service import ReportNotFound, ReportingService
from app.platform.http import (
    COMMON_API_ERROR_RESPONSES,
    PublicApiEnvelope,
    public_success_envelope,
)


router = APIRouter(
    prefix="/api/v2/reporting",
    tags=["reporting"],
    responses=COMMON_API_ERROR_RESPONSES,
)


def _idempotency_key(
    value: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=160,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$",
        ),
    ],
) -> str:
    return value


@router.post(
    "/external-score-recognitions",
    response_model=PublicApiEnvelope[RecognitionRevision],
)
def start_external_score_recognition(
    request: Request,
    response: Response,
    command: ExternalScoreRecognitionStartCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = ExternalScoreReportOcrService().start(
            session,
            settings=get_settings(),
            user_id=UUID(current_user.user_id),
            command=command,
            idempotency_key=command_id,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except ProcessorConfigurationError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="外部成绩识别服务配置无效。") from exc
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put(
    "/external-score-recognitions/{recognition_revision_id}/candidate",
    response_model=PublicApiEnvelope[RecognitionRevision],
)
def correct_external_score_recognition(
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: ExternalScoreRecognitionCorrectionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = ExternalScoreReportOcrService().correct(
            session,
            settings=get_settings(),
            user_id=UUID(current_user.user_id),
            recognition_revision_id=recognition_revision_id,
            command=command,
            idempotency_key=command_id,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/external-score-recognitions/{recognition_revision_id}/confirm",
    response_model=PublicApiEnvelope[RecognitionRevision],
)
def confirm_external_score_recognition(
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: ExternalScoreRecognitionDecisionCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = ExternalScoreReportOcrService().confirm(
            session,
            settings=get_settings(),
            user_id=UUID(current_user.user_id),
            recognition_revision_id=recognition_revision_id,
            command=command,
            idempotency_key=command_id,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/external-mocks",
    response_model=PublicApiEnvelope[ExternalMockReportProjection],
)
def create_external_mock_report_draft(
    request: Request,
    response: Response,
    command: ExternalMockReportDraftCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ExternalMockReportProjection]:
    try:
        result = ExternalMockReportService().create_draft(
            session,
            user_id=current_user.user_id,
            idempotency_key=command_id,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except ExternalMockReportError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/external-mocks/{report_version_id}/confirm",
    response_model=PublicApiEnvelope[ExternalMockReportProjection],
)
def confirm_external_mock_report(
    report_version_id: UUID,
    request: Request,
    response: Response,
    command: ExternalMockReportConfirmCommand,
    command_id: Annotated[str, Depends(_idempotency_key)],
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ExternalMockReportProjection]:
    try:
        result = ExternalMockReportService().confirm(
            session,
            user_id=current_user.user_id,
            report_version_id=str(report_version_id),
            idempotency_key=command_id,
            command=command,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except ExternalMockReportNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="外部模考报告不存在。") from exc
    except (ExternalMockReportError, AbilityEstimateContractError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/external-mocks/{report_version_id}",
    response_model=PublicApiEnvelope[ExternalMockReportProjection],
)
def get_external_mock_report(
    report_version_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ExternalMockReportProjection]:
    try:
        result = ExternalMockReportService().get(
            session,
            user_id=current_user.user_id,
            report_version_id=str(report_version_id),
        )
        return public_success_envelope(request=request, response=response, data=result)
    except ExternalMockReportNotFound as exc:
        raise HTTPException(status_code=404, detail="外部模考报告不存在。") from exc


@router.get("/center", response_model=PublicApiEnvelope[ReportCenterProjection])
def get_report_center(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ReportCenterProjection]:
    result = ReportingService().center(
        session, user_id=current_user.user_id
    )
    return public_success_envelope(request=request, response=response, data=result)


@router.get(
    "/reports/{report_kind}", response_model=PublicApiEnvelope[ReportProjection]
)
def get_report_projection(
    report_kind: Literal["single_set", "weekly", "mock", "position_gap", "global"],
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
    subject_ref: Annotated[str, Query(alias="subjectRef", min_length=1, max_length=160)] = "current",
) -> PublicApiEnvelope[ReportProjection]:
    try:
        result = ReportingService().report(
            session,
            user_id=current_user.user_id,
            report_kind=report_kind,
            subject_ref=subject_ref,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except ReportNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail="报告主体不存在。") from exc
