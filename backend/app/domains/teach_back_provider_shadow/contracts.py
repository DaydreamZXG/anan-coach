from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.teach_back_shadow.contracts import (
    GroundingShadowCandidate,
    ShadowReasonCode,
    StudentReproductionOutput,
)


class ProviderShadowModel(BaseModel):
    """Closed immutable contracts for synthetic provider-shadow execution."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        str_strip_whitespace=True,
    )


class ProviderCallAuditCandidate(ProviderShadowModel):
    """Content-free fields that can later be persisted by ModelCallLedgerRepository."""

    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=300)
    operation: Literal["teach_back_student_reproduction", "teach_back_grounding_verification"]
    provider: str = Field(min_length=1, max_length=240)
    model: str = Field(min_length=1, max_length=240)
    prompt_version: str = Field(alias="promptVersion", min_length=1, max_length=240)
    schema_version: str = Field(alias="schemaVersion", min_length=1, max_length=240)
    input_fingerprint: str = Field(
        alias="inputFingerprint", pattern=r"^[0-9a-f]{64}$"
    )
    output_fingerprint: str = Field(
        alias="outputFingerprint", pattern=r"^[0-9a-f]{64}$"
    )
    input_tokens: int = Field(alias="inputTokens", ge=0)
    cached_input_tokens: int = Field(alias="cachedInputTokens", ge=0)
    output_tokens: int = Field(alias="outputTokens", ge=0)
    total_cost_cny: str = Field(alias="totalCostCny", pattern=r"^\d+\.\d{8}$")
    latency_ms: int = Field(alias="latencyMs", ge=0)
    request_id_source: str = Field(alias="requestIdSource", min_length=1, max_length=120)
    request_id_fingerprint: str = Field(
        alias="requestIdFingerprint", pattern=r"^[0-9a-f]{64}$"
    )


class TeachBackProviderShadowResult(ProviderShadowModel):
    """Complete displayable shadow candidate or a closed grounding rejection."""

    state: Literal["displayable", "undisplayable"]
    reason_codes: tuple[ShadowReasonCode, ...] = Field(alias="reasonCodes")
    student_outputs: tuple[StudentReproductionOutput, ...] = Field(
        alias="studentOutputs", max_length=1
    )
    grounding_candidates: tuple[GroundingShadowCandidate, ...] = Field(
        alias="groundingCandidates", max_length=1
    )
    call_audits: tuple[ProviderCallAuditCandidate, ...] = Field(
        alias="callAudits", min_length=2, max_length=2
    )

    @model_validator(mode="after")
    def validate_complete_result(self) -> Self:
        operations = tuple(item.operation for item in self.call_audits)
        if operations != (
            "teach_back_student_reproduction",
            "teach_back_grounding_verification",
        ):
            raise ValueError("shadow result requires student then verifier call audits")
        if self.call_audits[0].idempotency_key == self.call_audits[1].idempotency_key:
            raise ValueError("student and verifier calls require distinct idempotency keys")
        if self.state == "displayable":
            if self.reason_codes or len(self.student_outputs) != 1 or len(
                self.grounding_candidates
            ) != 1:
                raise ValueError("displayable result requires both complete candidates")
        elif (
            self.reason_codes
            != (ShadowReasonCode.GROUNDING_REFERENCE_OUT_OF_SCOPE,)
            or self.student_outputs
            or self.grounding_candidates
        ):
            raise ValueError("undisplayable result exposes no partial candidates")
        return self
