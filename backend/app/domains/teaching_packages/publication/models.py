from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PublicationReasonCode(StrEnum):
    SOURCE_SCHEMA_INVALID = "SOURCE_SCHEMA_INVALID"
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"
    SOURCE_OCCURRENCE_UNRESOLVED = "SOURCE_OCCURRENCE_UNRESOLVED"
    QUESTION_NOT_ACTIVE = "QUESTION_NOT_ACTIVE"
    CONTENT_HEAD_MISSING = "CONTENT_HEAD_MISSING"
    SOURCE_ASSET_HASH_MISMATCH = "SOURCE_ASSET_HASH_MISMATCH"
    SOURCE_ASSET_AMBIGUOUS = "SOURCE_ASSET_AMBIGUOUS"
    SOURCE_AUDIENCE_UNRESOLVED = "SOURCE_AUDIENCE_UNRESOLVED"
    PUBLICATION_STALE = "PUBLICATION_STALE"


class AnalysisPublicationReasonCode(StrEnum):
    SOURCE_SCHEMA_INVALID = "SOURCE_SCHEMA_INVALID"
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"
    SOURCE_OCCURRENCE_UNRESOLVED = "SOURCE_OCCURRENCE_UNRESOLVED"
    QUESTION_NOT_ACTIVE = "QUESTION_NOT_ACTIVE"
    CONTENT_HEAD_MISSING = "CONTENT_HEAD_MISSING"
    ANALYSIS_SOURCE_MISMATCH = "ANALYSIS_SOURCE_MISMATCH"
    ANALYSIS_ASSET_UNRESOLVED = "ANALYSIS_ASSET_UNRESOLVED"
    PUBLICATION_STALE = "PUBLICATION_STALE"


@dataclass(frozen=True, slots=True)
class CandidateProjection:
    evidence_ref: str
    source_asset_sha256: str
    source_payload_sha256: str
    chart_type: str
    payload: dict[str, Any]
    authority: str


@dataclass(frozen=True, slots=True)
class RuntimeQuestion:
    question_id: str
    status: str
    content_version_id: str | None
    placeholder: bool = False


@dataclass(frozen=True, slots=True)
class AnalysisRuntimeQuestion:
    question_id: str
    status: str
    content_version_id: str | None
    analysis_source: str
    placeholder: bool = False
    final_analysis_asset_refs: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AnalysisPublicationBinding:
    question_id: str
    content_version_id: str
    analysis_source: str
    source_occurrence_question_key: str
    source_image_ref: str
    runtime_object_key: str = ""

    def as_contract(self) -> dict[str, str]:
        return {
            "questionId": self.question_id,
            "contentVersionId": self.content_version_id,
            "analysisSource": self.analysis_source,
            "sourceOccurrenceQuestionKey": self.source_occurrence_question_key,
            "sourceImageRef": self.source_image_ref,
            "runtimeObjectKey": self.runtime_object_key,
        }


@dataclass(frozen=True, slots=True)
class AnalysisPublicationPlanItem:
    batch_id: str
    evidence_ref: str
    source_asset_sha256: str
    source_payload_sha256: str
    verification_policy_version: str
    payload: dict[str, Any]
    analysis_bindings: tuple[AnalysisPublicationBinding, ...]
    quarantine_reasons: tuple[AnalysisPublicationReasonCode, ...]
    unresolved_occurrence_keys: tuple[str, ...] = ()
    rejected_question_ids: tuple[str, ...] = ()
    authority: str = ""

    @property
    def publishable(self) -> bool:
        return bool(self.analysis_bindings) and not self.quarantine_reasons

    def as_contract(self) -> dict[str, Any]:
        return {
            "batchId": self.batch_id,
            "evidenceRef": self.evidence_ref,
            "sourceAssetSha256": self.source_asset_sha256,
            "sourcePayloadSha256": self.source_payload_sha256,
            "verificationPolicyVersion": self.verification_policy_version,
            "payload": self.payload,
            "analysisBindings": [
                binding.as_contract() for binding in self.analysis_bindings
            ],
            "quarantineReasons": [reason.value for reason in self.quarantine_reasons],
        }


@dataclass(frozen=True, slots=True)
class AnalysisPublicationPlan:
    batch_id: str
    policy_version: str
    items: tuple[AnalysisPublicationPlanItem, ...]

    @property
    def publishable_items(self) -> tuple[AnalysisPublicationPlanItem, ...]:
        return tuple(item for item in self.items if item.publishable)

    @property
    def quarantined_items(self) -> tuple[AnalysisPublicationPlanItem, ...]:
        return tuple(item for item in self.items if not item.publishable)

    @property
    def binding_count(self) -> int:
        return sum(len(item.analysis_bindings) for item in self.publishable_items)

    def as_contract(self) -> dict[str, Any]:
        return {
            "batchId": self.batch_id,
            "verificationPolicyVersion": self.policy_version,
            "items": [item.as_contract() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class RuntimeAsset:
    asset_id: str
    question_id: str
    object_key: str
    content_sha256: str | None
    part_kind: str = ""


@dataclass(frozen=True, slots=True)
class PublicationBinding:
    question_id: str
    content_version_id: str
    runtime_asset_id: str

    def as_contract(self) -> dict[str, str]:
        return {
            "questionId": self.question_id,
            "contentVersionId": self.content_version_id,
            "runtimeAssetId": self.runtime_asset_id,
        }


@dataclass(frozen=True, slots=True)
class PublicationPlanItem:
    batch_id: str
    evidence_ref: str
    source_asset_sha256: str
    source_payload_sha256: str
    verification_policy_version: str
    payload: dict[str, Any]
    bindings: tuple[PublicationBinding, ...]
    quarantine_reasons: tuple[PublicationReasonCode, ...]
    unresolved_occurrence_keys: tuple[str, ...] = ()
    rejected_question_ids: tuple[str, ...] = ()
    authority: str = ""

    @property
    def publishable(self) -> bool:
        return bool(self.bindings) and not self.quarantine_reasons

    def as_contract(self) -> dict[str, Any]:
        return {
            "batchId": self.batch_id,
            "evidenceRef": self.evidence_ref,
            "sourceAssetSha256": self.source_asset_sha256,
            "sourcePayloadSha256": self.source_payload_sha256,
            "verificationPolicyVersion": self.verification_policy_version,
            "payload": self.payload,
            "bindings": [binding.as_contract() for binding in self.bindings],
            "quarantineReasons": [reason.value for reason in self.quarantine_reasons],
        }


@dataclass(frozen=True, slots=True)
class PublicationPlan:
    batch_id: str
    policy_version: str
    items: tuple[PublicationPlanItem, ...]

    @property
    def publishable_items(self) -> tuple[PublicationPlanItem, ...]:
        return tuple(item for item in self.items if item.publishable)

    @property
    def quarantined_items(self) -> tuple[PublicationPlanItem, ...]:
        return tuple(item for item in self.items if not item.publishable)

    @property
    def binding_count(self) -> int:
        return sum(len(item.bindings) for item in self.publishable_items)

    def as_contract(self) -> dict[str, Any]:
        return {
            "batchId": self.batch_id,
            "verificationPolicyVersion": self.policy_version,
            "items": [item.as_contract() for item in self.items],
        }
