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
    authorization_mode: Literal["contracted_data_protection", "user_consent"] = Field(
        alias="authorizationMode"
    )
    consent_policy_version: str | None = Field(alias="consentPolicyVersion")
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


class ExternalAIConsentCommand(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    command_key: str = Field(alias="commandKey", min_length=8, max_length=160)
    surface: str = Field(min_length=3, max_length=120)


class ExternalAIConsentView(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    contract_version: Literal["external-ai-consent-view@v1"] = Field(
        default="external-ai-consent-view@v1", alias="contractVersion"
    )
    policy_version: str = Field(alias="policyVersion")
    state: Literal["not_accepted", "accepted", "withdrawn"]
    consent_event_id: str | None = Field(alias="consentEventId")
    accepted_at: datetime | None = Field(alias="acceptedAt")
    withdrawn_at: datetime | None = Field(alias="withdrawnAt")
    title: str
    summary: str
    data_sent: list[str] = Field(alias="dataSent")
    provider_risk: str = Field(alias="providerRisk")
    withdrawal_effect: str = Field(alias="withdrawalEffect")
    processors: list[ProcessorDisclosure]
    generated_at: datetime = Field(alias="generatedAt")
