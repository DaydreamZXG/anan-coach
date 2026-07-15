from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.external_learning.models import (
    ContentComponent,
    LearningTrack,
    OfflinePacketVersion,
)


class PacketModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PacketContentInput(PacketModel):
    question_id: str = Field(alias="questionId", min_length=1, max_length=160)
    content_version_id: str = Field(
        alias="contentVersionId", pattern=r"^[0-9a-f]{64}$"
    )
    content_license_id: UUID = Field(alias="contentLicenseId")
    expected_authorization_epoch: int = Field(
        alias="expectedAuthorizationEpoch", ge=1
    )
    track: LearningTrack
    components: tuple[ContentComponent, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_components(self) -> "PacketContentInput":
        if len(self.components) != len(set(self.components)):
            raise ValueError("packet item components must be unique")
        return self


class CreatePacketCommand(PacketModel):
    render_format: Literal["pdf", "note_app_file"] = Field(alias="renderFormat")
    items: tuple[PacketContentInput, ...] = Field(min_length=1, max_length=200)
    expires_in_days: int = Field(default=14, alias="expiresInDays", ge=1, le=30)

    @model_validator(mode="after")
    def unique_content(self) -> "CreatePacketCommand":
        identities = [(item.question_id, item.content_version_id) for item in self.items]
        if len(identities) != len(set(identities)):
            raise ValueError("packet items must contain unique content versions")
        return self


class PacketList(PacketModel):
    items: tuple[OfflinePacketVersion, ...]


class RetryPacketCommand(PacketModel):
    failed_packet_version_id: UUID = Field(alias="failedPacketVersionId")
    expected_version_no: int = Field(alias="expectedVersionNo", ge=1)


class RenewPacketCommand(PacketModel):
    expired_packet_version_id: UUID = Field(alias="expiredPacketVersionId")
    expected_version_no: int = Field(alias="expectedVersionNo", ge=1)
    expires_in_days: int = Field(default=14, alias="expiresInDays", ge=1, le=30)


class PacketAccessProjection(PacketModel):
    packet_version_id: UUID = Field(alias="packetVersionId")
    download_url: str = Field(alias="downloadUrl", pattern=r"^https://")
    file_type: Literal["pdf", "note_app_file"] = Field(alias="fileType")
    expires_at: datetime = Field(alias="expiresAt")


class PacketContentCandidate(PacketModel):
    question_id: str = Field(alias="questionId")
    content_version_id: str = Field(alias="contentVersionId", pattern=r"^[0-9a-f]{64}$")
    content_license_id: UUID = Field(alias="contentLicenseId")
    expected_authorization_epoch: int = Field(alias="expectedAuthorizationEpoch", ge=1)
    components: tuple[ContentComponent, ...] = Field(min_length=1)
    title: str
    track: LearningTrack
    source_label: str = Field(alias="sourceLabel")
    version_label: str = Field(alias="versionLabel")
    selected_by_default: bool = Field(alias="selectedByDefault")


class PacketListSnapshot(PacketModel):
    packets: tuple[OfflinePacketVersion, ...]
    eligible_content: tuple[PacketContentCandidate, ...] = Field(alias="eligibleContent")
    partial_reasons: tuple[str, ...] = Field(alias="partialReasons")
    generated_at: datetime = Field(alias="generatedAt")


class PacketRenderContent(PacketModel):
    question_id: str = Field(alias="questionId")
    content_version_id: str = Field(alias="contentVersionId")
    order_no: int = Field(alias="orderNo", ge=1)
    answer_binding_code: str = Field(alias="answerBindingCode")
    track: LearningTrack
    title: str
    stem: str
    material_text: str | None = Field(default=None, alias="materialText")
    options: tuple[dict[str, str], ...] = ()


class PacketRenderRequest(PacketModel):
    contract_version: Literal["private-packet-render@v1"] = Field(
        default="private-packet-render@v1", alias="contractVersion"
    )
    packet_version_id: UUID = Field(alias="packetVersionId")
    owner_subject_ref: str = Field(alias="ownerSubjectRef", min_length=64, max_length=64)
    render_format: Literal["pdf", "note_app_file"] = Field(alias="renderFormat")
    manifest_sha256: str = Field(alias="manifestSha256", pattern=r"^[0-9a-f]{64}$")
    expires_at: str = Field(alias="expiresAt")
    contents: tuple[PacketRenderContent, ...] = Field(min_length=1, max_length=200)


class PacketRenderResult(PacketModel):
    contract_version: Literal["private-packet-render-result@v1"] = Field(
        default="private-packet-render-result@v1", alias="contractVersion"
    )
    artifact_ref: str = Field(alias="artifactRef", min_length=1, max_length=500)
    artifact_sha256: str = Field(
        alias="artifactSha256", pattern=r"^[0-9a-f]{64}$"
    )
    provider_request_id: str = Field(alias="providerRequestId", min_length=1, max_length=200)
