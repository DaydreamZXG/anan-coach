from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.contracts.external_learning.models import LearningTrack, Sha256Hex


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300_000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class ExternalAIAnswerPayload(StrictModel):
    contract_version: Literal["external-question-answer-output@v1"] = Field(
        default="external-question-answer-output@v1", alias="contractVersion"
    )
    answer: NonBlank
    explanation: NonBlank
    key_points: tuple[Annotated[str, StringConstraints(min_length=1, max_length=2_000)], ...] = Field(
        alias="keyPoints", min_length=1, max_length=12
    )
    confidence: Literal["low", "medium", "high"]
    limitations: tuple[Annotated[str, StringConstraints(min_length=1, max_length=2_000)], ...] = Field(
        max_length=12
    )
    source_segment_refs: tuple[str, ...] = Field(
        alias="sourceSegmentRefs", min_length=1, max_length=100
    )

    @model_validator(mode="after")
    def unique_refs(self) -> "ExternalAIAnswerPayload":
        if len(self.source_segment_refs) != len(set(self.source_segment_refs)):
            raise ValueError("AI answer source segment references must be unique")
        return self


class ExternalAIAnswerVersion(StrictModel):
    contract_version: Literal["external-ai-answer-version@v1"] = Field(
        default="external-ai-answer-version@v1", alias="contractVersion"
    )
    answer_version_id: UUID = Field(alias="answerVersionId")
    answer_id: UUID = Field(alias="answerId")
    import_id: UUID = Field(alias="importId")
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    recognition_revision_id: UUID = Field(alias="recognitionRevisionId")
    question_unit_id: Sha256Hex = Field(alias="questionUnitId")
    owner_user_id: UUID = Field(alias="ownerUserId")
    version_no: int = Field(alias="versionNo", ge=1)
    state: Literal["requested", "processing", "candidate", "confirmed", "failed"]
    learning_track: LearningTrack = Field(alias="learningTrack")
    input_fingerprint: Sha256Hex = Field(alias="inputFingerprint")
    output_fingerprint: Sha256Hex | None = Field(default=None, alias="outputFingerprint")
    answer_payload: ExternalAIAnswerPayload | None = Field(default=None, alias="answerPayload")
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = Field(default=None, alias="promptVersion")
    schema_version: str | None = Field(default=None, alias="schemaVersion")
    reservation_id: UUID | None = Field(default=None, alias="reservationId")
    reservation_source: Literal["catalog", "community_reward"] | None = Field(
        default=None, alias="reservationSource"
    )
    job_id: UUID | None = Field(default=None, alias="jobId")
    model_call_id: UUID | None = Field(default=None, alias="modelCallId")
    provider_request_id_fingerprint: Sha256Hex | None = Field(
        default=None, alias="providerRequestIdFingerprint"
    )
    failure_code: str | None = Field(default=None, alias="failureCode")
    supersedes_answer_version_id: UUID | None = Field(
        default=None, alias="supersedesAnswerVersionId"
    )
    created_at: AwareDatetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def coherent(self) -> "ExternalAIAnswerVersion":
        has_answer = self.answer_payload is not None and self.output_fingerprint is not None
        if (self.state in {"candidate", "confirmed"}) != has_answer:
            raise ValueError("candidate and confirmed AI answers require output")
        if (self.state == "failed") != (self.failure_code is not None):
            raise ValueError("only failed AI answers carry a failure code")
        if (self.version_no == 1) != (self.supersedes_answer_version_id is None):
            raise ValueError("AI answer version lineage is incomplete")
        return self


class ExternalAIAnswerProviderResult(StrictModel):
    output: ExternalAIAnswerPayload
    usage_input_tokens: int = Field(alias="usageInputTokens", ge=0)
    usage_cached_input_tokens: int = Field(alias="usageCachedInputTokens", ge=0)
    usage_output_tokens: int = Field(alias="usageOutputTokens", ge=0)
    latency_ms: int = Field(alias="latencyMs", ge=0)
    output_fingerprint: Sha256Hex = Field(alias="outputFingerprint")
    provider_request_id_fingerprint: Sha256Hex | None = Field(
        default=None, alias="providerRequestIdFingerprint"
    )
