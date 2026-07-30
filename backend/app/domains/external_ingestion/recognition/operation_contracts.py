from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from app.contracts.external_learning.models import RecognitionRevision


OperationStatus = Literal["running", "succeeded", "failed", "outcome_unknown"]
UsageState = Literal["planned", "actual", "unknown", "not_dispatched"]


@dataclass(frozen=True)
class RecognitionOperationSpec:
    operation_id: UUID
    operation_key: str
    owner_user_id: UUID
    artifact_version_id: UUID
    request_hash: str
    scenario_key: str
    operation: str
    provider: str
    provider_release: str
    model: str
    prompt_version: str
    schema_version: str
    price_version: str
    billing_unit: str
    unit_count: int
    unit_price_cny: Decimal
    input_fingerprint: str
    started_at: datetime


@dataclass(frozen=True)
class RecognitionOperationState:
    operation_id: UUID
    operation_key: str
    status: OperationStatus
    usage_state: UsageState
    created: bool


@dataclass(frozen=True)
class RecognitionReconciliationResult:
    operations: int
    refunded_reservations: int


class RecognitionOperationPort(Protocol):
    def begin(self, *, spec: RecognitionOperationSpec) -> RecognitionOperationState: ...

    def claim_provider_dispatch(
        self,
        *,
        operation_id: UUID,
        claimed_at: datetime,
    ) -> RecognitionOperationState: ...

    def succeed(
        self,
        *,
        operation_id: UUID,
        revision: RecognitionRevision,
        output_fingerprint: str,
        latency_ms: int,
        completed_at: datetime,
    ) -> RecognitionOperationState: ...

    def fail(
        self,
        *,
        operation_id: UUID,
        error_code: str,
        usage_state: Literal["actual", "unknown", "not_dispatched"],
        latency_ms: int,
        completed_at: datetime,
    ) -> RecognitionOperationState: ...
