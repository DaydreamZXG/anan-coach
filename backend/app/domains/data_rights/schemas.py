from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DataRightsModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class DataCategoryView(DataRightsModel):
    category: str
    title: str
    item_count: int = Field(alias="itemCount", ge=0)
    retention: str


class PrivacyOverview(DataRightsModel):
    policy_version: str = Field(alias="policyVersion")
    policy_title: str = Field(alias="policyTitle")
    raw_material_retention: str = Field(alias="rawMaterialRetention")
    categories: list[DataCategoryView]
    account_deletion_effect: list[str] = Field(alias="accountDeletionEffect")
    server_time: datetime = Field(alias="serverTime")


ExportState = Literal["queued", "running", "completed", "failed", "expired"]


class DataExportCreateRequest(DataRightsModel):
    command_key: str = Field(alias="commandKey", min_length=8, max_length=160)


class DataExportView(DataRightsModel):
    export_request_id: UUID = Field(alias="exportRequestId")
    state: ExportState
    requested_at: datetime = Field(alias="requestedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    byte_size: int | None = Field(default=None, alias="byteSize", ge=0)
    payload_sha256: str | None = Field(default=None, alias="payloadSha256")
    failure_code: str | None = Field(default=None, alias="failureCode")


class DataExportPayload(DataRightsModel):
    export_request_id: UUID = Field(alias="exportRequestId")
    policy_version: str = Field(alias="policyVersion")
    generated_at: datetime = Field(alias="generatedAt")
    payload_sha256: str = Field(alias="payloadSha256")
    payload: dict[str, object]


class AccountDeletionCreateRequest(DataRightsModel):
    command_key: str = Field(alias="commandKey", min_length=8, max_length=160)
    confirmation_phrase: str = Field(alias="confirmationPhrase", min_length=8, max_length=40)


DeletionState = Literal["queued", "running", "completed", "failed"]


class AccountDeletionView(DataRightsModel):
    deletion_request_id: UUID = Field(alias="deletionRequestId")
    state: DeletionState
    requested_at: datetime = Field(alias="requestedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    deletion_manifest: dict[str, object] | None = Field(
        default=None, alias="deletionManifest"
    )
    failure_code: str | None = Field(default=None, alias="failureCode")
