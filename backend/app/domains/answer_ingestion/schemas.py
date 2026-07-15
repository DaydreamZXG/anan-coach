from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.external_learning.models import AnswerIngestionVersion, LearningTrack


class AnswerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AnswerEntry(AnswerModel):
    binding_code: str = Field(alias="bindingCode", min_length=4, max_length=64)
    answer: str = Field(min_length=1, max_length=20_000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    requires_correction: bool = Field(default=False, alias="requiresCorrection")

    @model_validator(mode="after")
    def normalize(self) -> "AnswerEntry":
        self.binding_code = self.binding_code.strip().upper()
        self.answer = self.answer.strip()
        if not self.answer:
            raise ValueError("answer cannot be blank")
        return self


class CreateAnswerCandidateCommand(AnswerModel):
    packet_version_id: UUID = Field(alias="packetVersionId")
    track: LearningTrack
    input_mode: Literal[
        "answer_string", "answer_sheet_omr", "handwriting_ocr", "notes_app_export"
    ] = Field(alias="inputMode")
    answer_string: str | None = Field(default=None, alias="answerString", max_length=2_000)
    entries: tuple[AnswerEntry, ...] = Field(default=(), max_length=200)
    source_artifact_version_ids: tuple[UUID, ...] = Field(
        default=(), alias="sourceArtifactVersionIds", max_length=20
    )
    recognition_revision_ids: tuple[UUID, ...] = Field(
        default=(), alias="recognitionRevisionIds", max_length=20
    )

    @model_validator(mode="after")
    def validate_input(self) -> "CreateAnswerCandidateCommand":
        if self.input_mode == "answer_string":
            if self.answer_string is None or self.entries:
                raise ValueError("answer_string mode requires only answerString")
            normalized = re.sub(r"[\s,，;；|/]+", "", self.answer_string).upper()
            if not normalized or re.fullmatch(r"[A-H]+", normalized) is None:
                raise ValueError("answerString must contain objective option letters")
            self.answer_string = normalized
            if self.source_artifact_version_ids or self.recognition_revision_ids:
                raise ValueError("typed answer strings cannot claim artifact lineage")
        else:
            if self.answer_string is not None or not self.entries:
                raise ValueError("artifact modes require structured entries")
            if not self.source_artifact_version_ids or not self.recognition_revision_ids:
                raise ValueError("artifact modes require artifact and recognition lineage")
        codes = [item.binding_code for item in self.entries]
        if len(codes) != len(set(codes)):
            raise ValueError("answer entries must contain unique binding codes")
        return self


class CorrectAnswerCandidateCommand(AnswerModel):
    expected_version_no: int = Field(alias="expectedVersionNo", ge=1)
    entries: tuple[AnswerEntry, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_codes(self) -> "CorrectAnswerCandidateCommand":
        codes = [item.binding_code for item in self.entries]
        if len(codes) != len(set(codes)):
            raise ValueError("corrected answers must contain unique binding codes")
        return self


class ConfirmAnswerCandidateCommand(AnswerModel):
    expected_version_no: int = Field(alias="expectedVersionNo", ge=1)
    confirmation: Literal["user_verified"]


class AnswerFactReceipt(AnswerModel):
    fact_batch_id: UUID = Field(alias="factBatchId")
    fact_refs: tuple[str, ...] = Field(alias="factRefs", min_length=1)
    ingestion: AnswerIngestionVersion


class AnswerCandidateItem(AnswerModel):
    item_id: str = Field(alias="itemId")
    order_no: int = Field(alias="orderNo", ge=1)
    answer_binding_code: str = Field(alias="answerBindingCode")
    answer: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    requires_correction: bool = Field(alias="requiresCorrection")


class AnswerIngestionWorkspace(AnswerModel):
    ingestion: AnswerIngestionVersion
    items: tuple[AnswerCandidateItem, ...]
    packet_title: str | None = Field(alias="packetTitle")
    packet_item_count: int | None = Field(alias="packetItemCount")
    partial_reasons: tuple[str, ...] = Field(alias="partialReasons")
    expires_at: datetime | None = Field(alias="expiresAt")
    diagnostic_eligibility: Literal[
        "blocked_unconfirmed", "eligible_committed"
    ] = Field(alias="diagnosticEligibility")
    generated_at: datetime = Field(alias="generatedAt")


class PacketAnswerTarget(AnswerModel):
    packet_version_id: UUID = Field(alias="packetVersionId")
    packet_title: str = Field(alias="packetTitle")
    track: LearningTrack
    item_count: int = Field(alias="itemCount", ge=1)
    version_label: str = Field(alias="versionLabel")
    expires_at: datetime | None = Field(alias="expiresAt")


class AnswerTargetSnapshot(AnswerModel):
    packets: tuple[PacketAnswerTarget, ...]
    generated_at: datetime = Field(alias="generatedAt")
