from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class IssueLinkCommand(StrictModel):
    replace_pending: bool = Field(default=False, alias="replacePending")


class RevokeLinkCommand(StrictModel):
    expected_link_id: UUID = Field(alias="expectedLinkId")


class StartHubBotAuthorizationCommand(StrictModel):
    replace_pending: bool = Field(default=False, alias="replacePending")


class HubBotAuthorizationView(StrictModel):
    state: Literal[
        "unbound",
        "waiting_scan",
        "scanned",
        "connected",
        "reauth_required",
        "revoked",
    ]
    authorization_id: UUID | None = Field(default=None, alias="authorizationId")
    qr_image_data_url: str | None = Field(default=None, alias="qrImageDataUrl")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    status_copy: str = Field(alias="statusCopy")
    usage_copy: str = Field(alias="usageCopy")


class ILinkChannelView(StrictModel):
    link_state: Literal["unbound", "pending", "bound", "revoked"] = Field(alias="linkState")
    link_id: UUID | None = Field(default=None, alias="linkId")
    binding_code: str | None = Field(default=None, alias="bindingCode")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    query_count: int = Field(alias="queryCount", ge=0)
    collection_count: int = Field(alias="collectionCount", ge=0)
    usage_copy: str = Field(alias="usageCopy")
    evidence_copy: str = Field(alias="evidenceCopy")


class ILinkDataExport(StrictModel):
    contract_version: Literal["ilink-channel-export@v1"] = Field(
        default="ilink-channel-export@v1", alias="contractVersion"
    )
    link: dict[str, object] | None
    queries: tuple[dict[str, object], ...]
    collections: tuple[dict[str, object], ...]


class ILinkDataDeletion(StrictModel):
    deleted_links: int = Field(alias="deletedLinks", ge=0)
    deleted_queries: int = Field(alias="deletedQueries", ge=0)
    deleted_collections: int = Field(alias="deletedCollections", ge=0)


class AnswerProjection(StrictModel):
    answer_kind: Literal[
        "authorized_exact",
        "similar_reference",
        "ai_candidate",
        "ambiguous",
        "material_required",
    ] = Field(alias="answerKind")
    answer_text: str = Field(alias="answerText")
    explanation: str
    track: Literal["xingce", "essay"]
    recognized_payload: dict[str, object] = Field(alias="recognizedPayload")
    content_version_id: str | None = Field(default=None, alias="contentVersionId")
    authorization_snapshot_ref: str | None = Field(default=None, alias="authorizationSnapshotRef")
    model_provenance: dict[str, object] | None = Field(default=None, alias="modelProvenance")


class QueryView(StrictModel):
    query_id: UUID = Field(alias="queryId")
    user_id: UUID = Field(alias="userId")
    link_id: UUID = Field(alias="linkId")
    state: Literal["received", "processing", "answered", "failed", "deleted"]
    answer_kind: Literal[
        "none",
        "authorized_exact",
        "similar_reference",
        "ai_candidate",
        "ambiguous",
        "material_required",
    ] = Field(alias="answerKind")
    collection_state: Literal["not_collected", "collected", "queued_for_reattempt", "deleted"] = (
        Field(alias="collectionState")
    )
    reply_text: str = Field(alias="replyText")
    ability_impact: Literal["none_until_independent_attempt"] = Field(
        default="none_until_independent_attempt", alias="abilityImpact"
    )
