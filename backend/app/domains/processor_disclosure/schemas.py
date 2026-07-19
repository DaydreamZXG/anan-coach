from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProcessorDisclosure(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    processor_release_id: str = Field(alias="processorReleaseId")
    provider_release: str = Field(alias="providerRelease")
    display_name: str = Field(alias="displayName")
    operator_name: str | None = Field(alias="operatorName")
    real_user_status: Literal["approved", "blocked", "synthetic_only", "retired"] = Field(
        alias="realUserStatus"
    )
    transport_security: str = Field(alias="transportSecurity")
    purposes: list[str]
    data_categories: list[str] = Field(alias="dataCategories")
    data_region: str = Field(alias="dataRegion")
    model_training_use: str = Field(alias="modelTrainingUse")
    retention_policy: str = Field(alias="retentionPolicy")
    retention_seconds: int | None = Field(alias="retentionSeconds")
    deletion_support: str = Field(alias="deletionSupport")
    evidence: dict[str, object]
    verified_at: datetime = Field(alias="verifiedAt")
    valid_until: datetime | None = Field(alias="validUntil")


class ProcessorDisclosureList(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    contract_version: Literal["external-processor-disclosure@v1"] = Field(
        default="external-processor-disclosure@v1", alias="contractVersion"
    )
    processors: list[ProcessorDisclosure]
    generated_at: datetime = Field(alias="generatedAt")
