from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, Field

from app.domains.essay_intelligence.schemas import EssayContractModel
from app.domains.external_ingestion.recognition.models import RecognitionCandidatePayload


class EssayHandwritingStartCommand(EssayContractModel):
    artifact_version_id: UUID = Field(alias="artifactVersionId", strict=False)


class EssayHandwritingCorrectionCommand(EssayContractModel):
    expected_revision_no: int = Field(alias="expectedRevisionNo", ge=1)
    corrected_payload: RecognitionCandidatePayload = Field(alias="correctedPayload")
    reason: str = Field(min_length=1, max_length=500)


class EssayHandwritingDecisionCommand(EssayContractModel):
    expected_revision_no: int = Field(alias="expectedRevisionNo", ge=1)


class EssayHandwritingArtifactAccess(EssayContractModel):
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    image_url: str = Field(alias="imageUrl", pattern=r"^https://")
    mime_type: str = Field(alias="mimeType", pattern=r"^image/")
    expires_at: AwareDatetime = Field(alias="expiresAt")
