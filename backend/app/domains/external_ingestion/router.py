from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.external_ingestion.composition import build_upload_service
from app.domains.external_ingestion.schemas import (
    CompleteUploadProjection,
    ConfirmImportMatchCommand,
    ConfirmImportRecognitionCommand,
    CorrectImportRecognitionCommand,
    StartUploadCommand,
    StartUploadProjection,
    UploadSessionProjection,
    complete_upload_projection,
    start_upload_projection,
    upload_session_projection,
    CorrectRecognitionCommand,
    RecognitionDecisionCommand,
    ReprocessRecognitionCommand,
    ImportWorkspace,
    ProcessImportCommand,
    RequestImportAIAnswerCommand,
    ConfirmImportAIAnswerCommand,
    StartRecognitionCommand,
)
from app.contracts.external_learning.models import RecognitionRevision
from app.domains.external_ingestion.recognition.errors import RecognitionError
from app.domains.external_ingestion.recognition.errors import ProcessorConfigurationError
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.recognition.models import (
    OMRExpectedItem,
    OMRLayout,
)
from app.domains.external_ingestion.recognition_runtime import build_recognition_service
from app.domains.external_ingestion.matching.errors import MatchingError
from app.domains.external_ingestion.matching.runtime import (
    MatchingRuntimeConfig,
    build_matching_runtime,
)
from app.domains.external_ingestion.workspace import (
    ExternalImportError,
    ExternalImportWorkspaceService,
)
from app.domains.external_ingestion.upload.models import StartUploadRequest, UploadContractError
from app.domains.external_ingestion.answering.models import ExternalAIAnswerVersion
from app.domains.external_ingestion.answering.service import (
    ExternalAIAnswerError,
    ExternalAIAnswerService,
)
from app.domains.commerce.repository import CommerceQuotaExceededError
from app.domains.learning_packages.composition import build_learning_package_service
from app.domains.learning_packages.service import LearningPackageError
from app.domains.external_ingestion.upload.repository import PostgresUploadRepository
from app.domains.external_ingestion.upload.service import (
    UploadIdempotencyConflict,
    UploadIntegrityError,
    UploadNotFound,
    UploadOwnershipError,
    UploadRecoveryRequired,
    UploadUnavailable,
)
from app.platform.http import COMMON_API_ERROR_RESPONSES, PublicApiEnvelope, public_success_envelope
from app.domains.private_question_books.router import router as question_books_router


router = APIRouter(
    prefix="/api/v2/external-learning",
    tags=["external-learning-ingestion"],
    responses=COMMON_API_ERROR_RESPONSES,
)
router.include_router(question_books_router)

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$",
    ),
]


@router.post("/uploads", response_model=PublicApiEnvelope[StartUploadProjection])
def start_upload(
    request: Request,
    response: Response,
    command: StartUploadCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[StartUploadProjection]:
    try:
        result = build_upload_service(session=session, settings=get_settings()).start(
            StartUploadRequest(
                owner_user_id=UUID(current_user.user_id),
                idempotency_key=idempotency_key,
                source_kind=command.source_kind,
                original_filename=command.original_filename,
                mime_type=command.mime_type,
                byte_size=command.byte_size,
                content_sha256=command.content_sha256,
                page_count=command.page_count,
                part_sha256s=command.part_sha256s,
            )
        )
        session.commit()
        return public_success_envelope(
            request=request, response=response, data=start_upload_projection(result)
        )
    except Exception as exc:
        session.rollback()
        _raise_upload_error(exc)


@router.post(
    "/uploads/{upload_session_id}/complete",
    response_model=PublicApiEnvelope[CompleteUploadProjection],
)
def complete_upload(
    upload_session_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[CompleteUploadProjection]:
    try:
        result = build_upload_service(session=session, settings=get_settings()).complete(
            owner_user_id=UUID(current_user.user_id),
            session_id=upload_session_id,
        )
        session.commit()
        return public_success_envelope(
            request=request, response=response, data=complete_upload_projection(result)
        )
    except Exception as exc:
        session.rollback()
        _raise_upload_error(exc)


@router.get(
    "/uploads/{upload_session_id}", response_model=PublicApiEnvelope[UploadSessionProjection]
)
def get_upload(
    upload_session_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[UploadSessionProjection]:
    stored = PostgresUploadRepository(session=session).session(session_id=upload_session_id)
    if stored is None or stored.owner_user_id != UUID(current_user.user_id):
        raise HTTPException(status_code=404, detail="上传会话不存在。")
    return public_success_envelope(
        request=request, response=response, data=upload_session_projection(stored)
    )


def _import_service(session: Session) -> ExternalImportWorkspaceService:
    settings = get_settings()
    return ExternalImportWorkspaceService(
        session=session,
        recognition_service=build_recognition_service(
            session=session, settings=settings
        ),
        matching_service=build_matching_runtime(
            session=session,
            config=MatchingRuntimeConfig(
                policy_version=settings.matching_policy_version,
                similar_threshold=settings.matching_similar_threshold,
                ambiguity_margin=settings.matching_ambiguity_margin,
                text_weight=settings.matching_text_weight,
                visual_weight=settings.matching_visual_weight,
                essay_min_prompt_chars=settings.matching_essay_min_prompt_chars,
                essay_require_material=settings.matching_essay_require_material,
                maximum_candidates=settings.matching_maximum_candidates,
                sql_prefilter_threshold=settings.matching_sql_prefilter_threshold,
                sql_prefetch_factor=settings.matching_sql_prefetch_factor,
                sql_maximum_prefetch=settings.matching_sql_maximum_prefetch,
            ),
        ).service,
    )


@router.post(
    "/imports/{import_id}/ai-answers",
    response_model=PublicApiEnvelope[ExternalAIAnswerVersion],
)
def request_import_ai_answer(
    import_id: UUID,
    request: Request,
    response: Response,
    command: RequestImportAIAnswerCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ExternalAIAnswerVersion]:
    try:
        version = ExternalAIAnswerService(
            session=session, settings=get_settings()
        ).request(
            owner_user_id=UUID(current_user.user_id),
            import_id=import_id,
            question_unit_id=command.question_unit_id,
            learning_track=command.learning_track,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=version)
    except CommerceQuotaExceededError as exc:
        session.rollback()
        raise HTTPException(status_code=429, detail="本期 AI 拍题解析次数已用完。") from exc
    except ExternalAIAnswerError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/ai-answers/{answer_version_id}/confirm",
    response_model=PublicApiEnvelope[ExternalAIAnswerVersion],
)
def confirm_import_ai_answer(
    answer_version_id: UUID,
    request: Request,
    response: Response,
    command: ConfirmImportAIAnswerCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ExternalAIAnswerVersion]:
    try:
        version = ExternalAIAnswerService(
            session=session, settings=get_settings()
        ).confirm(
            owner_user_id=UUID(current_user.user_id),
            answer_version_id=answer_version_id,
            expected_output_fingerprint=command.expected_output_fingerprint,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=version)
    except ExternalAIAnswerError as exc:
        session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post(
    "/imports/{import_id}/process", response_model=PublicApiEnvelope[ImportWorkspace]
)
def process_import(
    import_id: UUID,
    request: Request,
    response: Response,
    command: ProcessImportCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ImportWorkspace]:
    settings = get_settings()
    if not settings.recognition_default_import_route.strip():
        raise HTTPException(status_code=503, detail="导入识别处理路线尚未配置。")
    try:
        workspace = _import_service(session).process(
            owner_user_id=UUID(current_user.user_id),
            import_id=import_id,
            learning_track=command.learning_track,
            processor_route=settings.recognition_default_import_route,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=workspace)
    except (ExternalImportError, RecognitionError, MatchingError) as exc:
        session.rollback()
        _raise_import_error(exc)


@router.get("/imports/{import_id}", response_model=PublicApiEnvelope[ImportWorkspace])
def get_import(
    import_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ImportWorkspace]:
    try:
        workspace = _import_service(session).workspace(
            owner_user_id=UUID(current_user.user_id), import_id=import_id
        )
        return public_success_envelope(request=request, response=response, data=workspace)
    except (ExternalImportError, RecognitionError, MatchingError) as exc:
        _raise_import_error(exc)


@router.put(
    "/imports/{import_id}/recognitions/{recognition_revision_id}",
    response_model=PublicApiEnvelope[ImportWorkspace],
)
def correct_import_recognition(
    import_id: UUID,
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: CorrectImportRecognitionCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ImportWorkspace]:
    try:
        workspace = _import_service(session).correct_recognition(
            owner_user_id=UUID(current_user.user_id),
            import_id=import_id,
            recognition_revision_id=recognition_revision_id,
            command=command,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=workspace)
    except (ExternalImportError, RecognitionError, MatchingError) as exc:
        session.rollback()
        _raise_import_error(exc)


@router.post(
    "/imports/{import_id}/recognitions/{recognition_revision_id}/confirm",
    response_model=PublicApiEnvelope[ImportWorkspace],
)
def confirm_import_recognition(
    import_id: UUID,
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: ConfirmImportRecognitionCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ImportWorkspace]:
    try:
        workspace = _import_service(session).confirm_recognition_and_match(
            owner_user_id=UUID(current_user.user_id),
            import_id=import_id,
            recognition_revision_id=recognition_revision_id,
            expected_revision_no=command.expected_revision_no,
            learning_track=command.learning_track,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=workspace)
    except (ExternalImportError, RecognitionError, MatchingError) as exc:
        session.rollback()
        _raise_import_error(exc)


@router.post(
    "/imports/{import_id}/matches/{content_match_version_id}/confirm",
    response_model=PublicApiEnvelope[ImportWorkspace],
)
def confirm_import_match(
    import_id: UUID,
    content_match_version_id: UUID,
    request: Request,
    response: Response,
    command: ConfirmImportMatchCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[ImportWorkspace]:
    try:
        workspace = _import_service(session).confirm_match(
            owner_user_id=UUID(current_user.user_id),
            import_id=import_id,
            content_match_version_id=content_match_version_id,
            command=command,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=workspace)
    except (ExternalImportError, RecognitionError, MatchingError) as exc:
        session.rollback()
        _raise_import_error(exc)


@router.post("/recognitions", response_model=PublicApiEnvelope[RecognitionRevision])
def start_recognition(
    request: Request,
    response: Response,
    command: StartRecognitionCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        settings = get_settings()
        processor_route = command.processor_route or (
            settings.recognition_default_answer_route
            if command.recognition_kind.value == "answer_sheet_omr"
            else settings.recognition_default_import_route
        )
        if not processor_route.strip():
            raise ProcessorConfigurationError(
                "default recognition processor route is not configured"
            )
        result = build_recognition_service(session=session, settings=settings).recognize(
            owner_user_id=UUID(current_user.user_id),
            artifact_version_id=command.artifact_version_id,
            recognition_kind=command.recognition_kind,
            processor_route=processor_route,
            idempotency_key=idempotency_key,
            omr_layout=(
                _owned_omr_layout(
                    session=session,
                    settings=settings,
                    user_id=current_user.user_id,
                    packet_version_id=command.packet_version_id,
                )
                if command.packet_version_id is not None
                else None
            ),
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionError as exc:
        session.rollback()
        if isinstance(exc, ProcessorConfigurationError):
            raise HTTPException(status_code=503, detail="识别处理服务配置无效。") from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/recognitions/{recognition_revision_id}", response_model=PublicApiEnvelope[RecognitionRevision])
def get_recognition(
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    revision = SQLRecognitionRepository(session).revision(
        recognition_revision_id=recognition_revision_id
    )
    if revision is None or str(revision.owner_user_id) != current_user.user_id:
        raise HTTPException(status_code=404, detail="识别结果不存在。")
    return public_success_envelope(request=request, response=response, data=revision)


@router.put("/recognitions/{recognition_revision_id}/candidate", response_model=PublicApiEnvelope[RecognitionRevision])
def correct_recognition(
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: CorrectRecognitionCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = build_recognition_service(session=session, settings=get_settings()).correct(
            owner_user_id=UUID(current_user.user_id),
            recognition_revision_id=recognition_revision_id,
            expected_revision_no=command.expected_revision_no,
            corrected_payload=command.corrected_payload,
            reason=command.reason,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/recognitions/{recognition_revision_id}/confirm", response_model=PublicApiEnvelope[RecognitionRevision])
def confirm_recognition(
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: RecognitionDecisionCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = build_recognition_service(session=session, settings=get_settings()).confirm(
            owner_user_id=UUID(current_user.user_id),
            recognition_revision_id=recognition_revision_id,
            expected_revision_no=command.expected_revision_no,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/recognitions/{recognition_revision_id}/reject", response_model=PublicApiEnvelope[RecognitionRevision])
def reject_recognition(
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: RecognitionDecisionCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = build_recognition_service(session=session, settings=get_settings()).reject(
            owner_user_id=UUID(current_user.user_id),
            recognition_revision_id=recognition_revision_id,
            expected_revision_no=command.expected_revision_no,
            idempotency_key=idempotency_key,
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/recognitions/{recognition_revision_id}/reprocess", response_model=PublicApiEnvelope[RecognitionRevision])
def reprocess_recognition(
    recognition_revision_id: UUID,
    request: Request,
    response: Response,
    command: ReprocessRecognitionCommand,
    idempotency_key: IdempotencyKey,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_db_session)],
) -> PublicApiEnvelope[RecognitionRevision]:
    try:
        result = build_recognition_service(session=session, settings=get_settings()).reprocess(
            owner_user_id=UUID(current_user.user_id),
            recognition_revision_id=recognition_revision_id,
            expected_revision_no=command.expected_revision_no,
            processor_route=command.processor_route,
            idempotency_key=idempotency_key,
            omr_layout=(
                _owned_omr_layout(
                    session=session,
                    settings=get_settings(),
                    user_id=current_user.user_id,
                    packet_version_id=command.packet_version_id,
                )
                if command.packet_version_id is not None
                else None
            ),
        )
        session.commit()
        return public_success_envelope(request=request, response=response, data=result)
    except RecognitionError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _owned_omr_layout(
    *,
    session: Session,
    settings,
    user_id: str,
    packet_version_id: UUID | None,
) -> OMRLayout:
    if packet_version_id is None:
        raise ProcessorConfigurationError("OMR packet version is required")
    try:
        packages = build_learning_package_service(session=session, settings=settings)
        packet = packages.get_packet(
            user_id=user_id,
            packet_version_id=packet_version_id,
        )
        if packet.state != "ready":
            raise ProcessorConfigurationError("OMR packet is not ready")
        if any(item.track.value != "xingce" for item in packet.items):
            raise ProcessorConfigurationError("OMR packet contains non-objective content")
        render_request = packages.get_render_request(
            user_id=user_id,
            packet_version_id=packet_version_id,
        )
    except LearningPackageError as exc:
        raise ProcessorConfigurationError("OMR packet manifest is unavailable") from exc
    contents = {item.order_no: item for item in render_request.contents}
    expected: list[OMRExpectedItem] = []
    for item in sorted(packet.items, key=lambda value: value.order_no):
        rendered = contents.get(item.order_no)
        labels = tuple(
            str(option.get("label", "")).strip().upper()
            for option in (rendered.options if rendered is not None else ())
        )
        if (
            rendered is None
            or len(labels) < 2
            or len(labels) > 8
            or len(labels) != len(set(labels))
            or any(label not in "ABCDEFGH" for label in labels)
        ):
            raise ProcessorConfigurationError(
                "OMR packet does not contain a complete objective option layout"
            )
        expected.append(OMRExpectedItem(itemNo=item.order_no, optionLabels=labels))
    return OMRLayout(packetVersionId=packet_version_id, items=tuple(expected))


def _raise_upload_error(exc: Exception) -> None:
    if isinstance(exc, (UploadNotFound, UploadOwnershipError)):
        raise HTTPException(status_code=404, detail="上传会话不存在。") from exc
    if isinstance(exc, UploadIdempotencyConflict):
        raise HTTPException(status_code=409, detail="上传请求的幂等键已经被其他文件使用。") from exc
    if isinstance(exc, (UploadIntegrityError, UploadUnavailable, UploadRecoveryRequired)):
        raise HTTPException(status_code=409, detail="文件上传状态不允许当前操作。") from exc
    if isinstance(exc, UploadContractError):
        raise HTTPException(status_code=503, detail="文件安全处理服务尚未完成生产配置。") from exc
    raise exc


def _raise_import_error(exc: Exception) -> None:
    if isinstance(exc, ExternalImportError):
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if isinstance(exc, ProcessorConfigurationError):
        raise HTTPException(status_code=503, detail="导入识别处理服务配置无效。") from exc
    raise HTTPException(status_code=409, detail="导入任务状态已经变化，请刷新后重试。") from exc
