from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.contracts.external_learning.models import RecognitionKind, RecognitionRevision
from app.core.config import Settings
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    RecognitionNotFound,
    RecognitionTransitionError,
)
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.recognition_runtime import build_recognition_service
from app.domains.reporting.schemas import (
    ExternalScoreRecognitionCorrectionCommand,
    ExternalScoreRecognitionDecisionCommand,
    ExternalScoreRecognitionStartCommand,
)


class ExternalScoreReportOcrService:
    """Report-owned facade: printed score screenshots only, never answer-sheet OMR."""

    def start(
        self,
        session: Session,
        *,
        settings: Settings,
        user_id: UUID,
        command: ExternalScoreRecognitionStartCommand,
        idempotency_key: str,
    ) -> RecognitionRevision:
        route = settings.recognition_default_external_score_report_route.strip()
        if not route:
            raise ProcessorConfigurationError(
                "external score report recognition processor route is not configured"
            )
        artifact = SQLRecognitionRepository(session).artifact(
            artifact_version_id=command.artifact_version_id,
            owner_user_id=user_id,
        )
        if artifact is None:
            raise RecognitionNotFound("external score report image does not exist")
        if artifact.source_kind.value not in {"camera_image", "image_import"}:
            raise RecognitionTransitionError(
                "external score reports accept screenshot images only"
            )
        if not artifact.mime_type.startswith("image/"):
            raise RecognitionTransitionError(
                "external score reports reject documents and answer sheets"
            )
        return build_recognition_service(session=session, settings=settings).recognize(
            owner_user_id=user_id,
            artifact_version_id=command.artifact_version_id,
            recognition_kind=RecognitionKind.PRINTED_OCR,
            processor_route=route,
            idempotency_key=idempotency_key,
        )

    def correct(
        self,
        session: Session,
        *,
        settings: Settings,
        user_id: UUID,
        recognition_revision_id: UUID,
        command: ExternalScoreRecognitionCorrectionCommand,
        idempotency_key: str,
    ) -> RecognitionRevision:
        self._require_owned_printed_revision(
            session, user_id=user_id, recognition_revision_id=recognition_revision_id
        )
        if command.corrected_payload.kind != RecognitionKind.PRINTED_OCR:
            raise RecognitionTransitionError(
                "external score correction must remain printed_ocr"
            )
        return build_recognition_service(session=session, settings=settings).correct(
            owner_user_id=user_id,
            recognition_revision_id=recognition_revision_id,
            expected_revision_no=command.expected_revision_no,
            corrected_payload=command.corrected_payload,
            reason=command.reason,
            idempotency_key=idempotency_key,
        )

    def confirm(
        self,
        session: Session,
        *,
        settings: Settings,
        user_id: UUID,
        recognition_revision_id: UUID,
        command: ExternalScoreRecognitionDecisionCommand,
        idempotency_key: str,
    ) -> RecognitionRevision:
        self._require_owned_printed_revision(
            session, user_id=user_id, recognition_revision_id=recognition_revision_id
        )
        return build_recognition_service(session=session, settings=settings).confirm(
            owner_user_id=user_id,
            recognition_revision_id=recognition_revision_id,
            expected_revision_no=command.expected_revision_no,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _require_owned_printed_revision(
        session: Session,
        *,
        user_id: UUID,
        recognition_revision_id: UUID,
    ) -> None:
        revision = SQLRecognitionRepository(session).revision(
            recognition_revision_id=recognition_revision_id
        )
        if revision is None or revision.owner_user_id != user_id:
            raise RecognitionNotFound("external score recognition does not exist")
        if revision.recognition_kind != RecognitionKind.PRINTED_OCR:
            raise RecognitionTransitionError(
                "non-printed recognition cannot enter the score-report workflow"
            )
