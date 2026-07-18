from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.contracts.external_learning.models import RecognitionKind, RecognitionRevision
from app.core.config import Settings
from app.domains.essay_intelligence.handwriting_schemas import (
    EssayHandwritingArtifactAccess,
    EssayHandwritingCorrectionCommand,
    EssayHandwritingDecisionCommand,
    EssayHandwritingStartCommand,
)
from app.domains.essay_intelligence.handwriting_repository import (
    EssayHandwritingRepository,
)
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    RecognitionNotFound,
    RecognitionTransitionError,
)
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.recognition_runtime import build_recognition_service


class PrivateArtifactAccessSigner(Protocol):
    def sign_download(
        self, *, artifact_ref: str, expires_at: datetime
    ) -> str: ...


class EssayHandwritingService:
    """Essay-owned facade over the shared immutable OCR infrastructure."""

    def __init__(
        self,
        *,
        access_signer: PrivateArtifactAccessSigner | None = None,
        repository: EssayHandwritingRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._access_signer = access_signer
        self._repository = repository or EssayHandwritingRepository()
        self._clock = clock or (lambda: datetime.now(UTC))

    def start(
        self,
        session: Session,
        *,
        settings: Settings,
        user_id: UUID,
        command: EssayHandwritingStartCommand,
        idempotency_key: str,
    ) -> RecognitionRevision:
        route = settings.recognition_default_essay_handwriting_route.strip()
        if not route:
            raise ProcessorConfigurationError(
                "essay handwriting recognition processor route is not configured"
            )
        artifact = SQLRecognitionRepository(session).artifact(
            artifact_version_id=command.artifact_version_id,
            owner_user_id=user_id,
        )
        if artifact is None:
            raise RecognitionNotFound("essay handwriting source image does not exist")
        if artifact.source_kind.value not in {"camera_image", "image_import"}:
            raise RecognitionTransitionError(
                "essay handwriting recognition rejects answer-sheet and document sources"
            )
        if not artifact.mime_type.startswith("image/"):
            raise RecognitionTransitionError(
                "essay handwriting recognition only accepts source images"
            )
        return build_recognition_service(session=session, settings=settings).recognize(
            owner_user_id=user_id,
            artifact_version_id=command.artifact_version_id,
            recognition_kind=RecognitionKind.HANDWRITING_OCR,
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
        command: EssayHandwritingCorrectionCommand,
        idempotency_key: str,
    ) -> RecognitionRevision:
        self._require_owned_essay_revision(
            session,
            user_id=user_id,
            recognition_revision_id=recognition_revision_id,
        )
        if command.corrected_payload.kind != RecognitionKind.HANDWRITING_OCR:
            raise RecognitionTransitionError(
                "essay handwriting correction payload must remain handwriting_ocr"
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
        command: EssayHandwritingDecisionCommand,
        idempotency_key: str,
    ) -> RecognitionRevision:
        self._require_owned_essay_revision(
            session,
            user_id=user_id,
            recognition_revision_id=recognition_revision_id,
        )
        return build_recognition_service(session=session, settings=settings).confirm(
            owner_user_id=user_id,
            recognition_revision_id=recognition_revision_id,
            expected_revision_no=command.expected_revision_no,
            idempotency_key=idempotency_key,
        )

    def artifact_access(
        self,
        session: Session,
        *,
        user_id: UUID,
        artifact_version_id: UUID,
        ttl_seconds: int,
    ) -> EssayHandwritingArtifactAccess:
        if self._access_signer is None:
            raise ProcessorConfigurationError(
                "essay handwriting artifact access signer is not configured"
            )
        if not 60 <= ttl_seconds <= 900:
            raise ProcessorConfigurationError(
                "essay handwriting artifact access TTL must be 60-900 seconds"
            )
        artifact = self._repository.bound_answer_artifact(
            session,
            user_id=user_id,
            artifact_version_id=artifact_version_id,
        )
        if artifact is None:
            raise RecognitionNotFound(
                "confirmed essay handwriting source image does not exist"
            )
        if artifact.source_kind.value not in {"camera_image", "image_import"}:
            raise RecognitionTransitionError(
                "confirmed essay handwriting rejects answer-sheet sources"
            )
        if not artifact.mime_type.startswith("image/"):
            raise RecognitionTransitionError(
                "confirmed essay handwriting source artifact is not an image"
            )
        expires_at = self._clock() + timedelta(seconds=ttl_seconds)
        image_url = self._access_signer.sign_download(
            artifact_ref=artifact.object_ref,
            expires_at=expires_at,
        )
        return EssayHandwritingArtifactAccess(
            artifactVersionId=artifact.artifact_version_id,
            imageUrl=image_url,
            mimeType=artifact.mime_type,
            expiresAt=expires_at,
        )

    @staticmethod
    def _require_owned_essay_revision(
        session: Session,
        *,
        user_id: UUID,
        recognition_revision_id: UUID,
    ) -> None:
        revision = SQLRecognitionRepository(session).revision(
            recognition_revision_id=recognition_revision_id
        )
        if revision is None or revision.owner_user_id != user_id:
            raise RecognitionNotFound("essay handwriting recognition does not exist")
        if revision.recognition_kind != RecognitionKind.HANDWRITING_OCR:
            raise RecognitionTransitionError(
                "non-essay recognition cannot enter the essay handwriting workflow"
            )
