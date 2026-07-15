from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.answer_ingestion.schemas import (
    AnswerIngestionWorkspace,
    AnswerTargetSnapshot,
    ConfirmAnswerCandidateCommand,
    CorrectAnswerCandidateCommand,
    CreateAnswerCandidateCommand,
)
from app.domains.answer_ingestion.service import AnswerIngestionError, AnswerIngestionService
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.content_catalog.licensing.service import ContentLicenseService
from app.domains.external_ingestion.recognition.errors import RecognitionError
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.recognition_runtime import (
    build_recognition_decision_service,
)
from app.platform.http import COMMON_API_ERROR_RESPONSES, PublicApiEnvelope, public_success_envelope


router = APIRouter(
    prefix="/api/v2/external-learning/answer-ingestions",
    tags=["external-learning-answers"],
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


def _service(session: Session) -> AnswerIngestionService:
    return AnswerIngestionService(
        session=session,
        license_evaluator=ContentLicenseService(session=session),
    )


@router.post("", response_model=PublicApiEnvelope[AnswerIngestionWorkspace])
def create_candidate(
    request: Request,
    response: Response,
    command: CreateAnswerCandidateCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[AnswerIngestionWorkspace]:
    try:
        result = _service(session).create_candidate(
            user_id=current_user.user_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(
            request=request,
            response=response,
            data=_service(session).workspace(user_id=current_user.user_id, ingestion=result),
        )
    except AnswerIngestionError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.put("/{ingestion_id}/candidate", response_model=PublicApiEnvelope[AnswerIngestionWorkspace])
def correct_candidate(
    ingestion_id: UUID,
    request: Request,
    response: Response,
    command: CorrectAnswerCandidateCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[AnswerIngestionWorkspace]:
    try:
        result = _service(session).correct_candidate(
            user_id=current_user.user_id,
            ingestion_id=ingestion_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(
            request=request,
            response=response,
            data=_service(session).workspace(user_id=current_user.user_id, ingestion=result),
        )
    except AnswerIngestionError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{ingestion_id}/confirm", response_model=PublicApiEnvelope[AnswerIngestionWorkspace])
def confirm_candidate(
    ingestion_id: UUID,
    request: Request,
    response: Response,
    command: ConfirmAnswerCandidateCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[AnswerIngestionWorkspace]:
    try:
        current = _service(session).get_current(
            user_id=current_user.user_id, ingestion_id=ingestion_id
        )
        recognition_repository = SQLRecognitionRepository(session)
        recognition_decisions = build_recognition_decision_service(session=session)
        for reference in current.source_lineage_refs:
            if not reference.startswith("recognition-revision:"):
                continue
            source = recognition_repository.revision(
                recognition_revision_id=UUID(
                    reference.removeprefix("recognition-revision:")
                )
            )
            if source is None or str(source.owner_user_id) != current_user.user_id:
                raise AnswerIngestionError(
                    "answer_recognition_unavailable",
                    "答案识别版本不存在。",
                    status_code=409,
                )
            head = recognition_repository.head(
                artifact_version_id=source.artifact_version_id,
                owner_user_id=source.owner_user_id,
                recognition_kind=source.recognition_kind.value,
            )
            if head is None:
                raise AnswerIngestionError(
                    "answer_recognition_unavailable",
                    "答案识别版本不存在。",
                    status_code=409,
                )
            if head.state.value == "candidate":
                recognition_decisions.confirm(
                    owner_user_id=head.owner_user_id,
                    recognition_revision_id=head.recognition_revision_id,
                    expected_revision_no=head.revision_no,
                    idempotency_key=(
                        f"answer-omr-confirm:{idempotency_key}:{head.recognition_revision_id}"
                    ),
                )
        result = _service(session).confirm_candidate(
            user_id=current_user.user_id,
            ingestion_id=ingestion_id,
            idempotency_key=idempotency_key,
            command=command,
        )
        session.commit()
        return public_success_envelope(
            request=request,
            response=response,
            data=_service(session).workspace(
                user_id=current_user.user_id, ingestion=result.ingestion
            ),
        )
    except (AnswerIngestionError, RecognitionError) as exc:
        session.rollback()
        if isinstance(exc, AnswerIngestionError):
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        raise HTTPException(status_code=409, detail="答案识别版本无法确认。") from exc


@router.get("/targets", response_model=PublicApiEnvelope[AnswerTargetSnapshot])
def list_targets(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[AnswerTargetSnapshot]:
    return public_success_envelope(
        request=request,
        response=response,
        data=_service(session).list_targets(user_id=current_user.user_id),
    )


@router.get("/{ingestion_id}", response_model=PublicApiEnvelope[AnswerIngestionWorkspace])
def get_ingestion(
    ingestion_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[AnswerIngestionWorkspace]:
    try:
        result = _service(session).get_current(
            user_id=current_user.user_id, ingestion_id=ingestion_id
        )
        return public_success_envelope(
            request=request,
            response=response,
            data=_service(session).workspace(user_id=current_user.user_id, ingestion=result),
        )
    except AnswerIngestionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
