from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CAUSE_CANDIDATE_CONTRACT_VERSION = "cause-candidate@v2"
CAUSE_DECISION_CONTRACT_VERSION = "cause-decision@v2"
ENTITLEMENT_POLICY_VERSION = "included-with-canonical-practice@v1"


class CauseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class CandidateReady(CauseModel):
    kind: Literal["candidate_ready"] = "candidate_ready"
    cause_code: str = Field(alias="causeCode", min_length=2, max_length=80)
    summary: str = Field(min_length=4, max_length=600)
    improvement: str = Field(min_length=4, max_length=800)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def unique_evidence(self) -> "CandidateReady":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("candidate evidence refs must be unique")
        return self


class FollowupRequired(CauseModel):
    kind: Literal["followup_required"] = "followup_required"
    question: str = Field(min_length=4, max_length=500)
    rationale: str = Field(min_length=4, max_length=600)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=20)


class EvidenceInsufficient(CauseModel):
    kind: Literal["evidence_insufficient"] = "evidence_insufficient"
    reason_code: str = Field(alias="reasonCode", min_length=2, max_length=80)
    explanation: str = Field(min_length=4, max_length=800)
    missing_evidence: list[str] = Field(alias="missingEvidence", min_length=1, max_length=8)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=20)


CauseAgentOutput = Annotated[
    CandidateReady | FollowupRequired | EvidenceInsufficient,
    Field(discriminator="kind"),
]


class CauseAgentProviderEnvelope(CauseModel):
    contract_version: Literal["cause-candidate@v2"] = Field(
        default=CAUSE_CANDIDATE_CONTRACT_VERSION,
        alias="contractVersion",
    )
    output: CauseAgentOutput


class CauseAgentEvidence(CauseModel):
    attempt: dict
    reflection: dict
    explanations: dict
    taxonomy: dict

    @property
    def allowed_evidence_refs(self) -> set[str]:
        refs: set[str] = set()
        for section in (
            self.attempt,
            self.reflection,
            self.explanations,
            self.taxonomy,
        ):
            refs.update(str(item) for item in section.get("evidenceRefs", []))
        return refs

    @property
    def allowed_cause_codes(self) -> set[str]:
        return {
            str(item["code"])
            for item in self.taxonomy.get("causes", [])
            if isinstance(item, dict) and item.get("code")
        }


def validate_grounded_output(
    *,
    output: CandidateReady | FollowupRequired | EvidenceInsufficient,
    evidence: CauseAgentEvidence,
    followup_round: int,
) -> None:
    refs = set(output.evidence_refs)
    if not refs.issubset(evidence.allowed_evidence_refs):
        raise ValueError("cause agent output cited evidence outside the frozen tool results")
    if isinstance(output, CandidateReady) and output.cause_code not in (
        evidence.allowed_cause_codes
    ):
        raise ValueError("cause agent output used an unpublished cause code")
    if isinstance(output, FollowupRequired) and followup_round >= 1:
        raise ValueError("cause agent cannot request a second followup round")
