from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CAUSE_CANDIDATE_CONTRACT_VERSION = "cause-candidate@v2"
CAUSE_DECISION_CONTRACT_VERSION = "cause-decision@v2"
ENTITLEMENT_POLICY_VERSION = "metered-model-round@v1"


class CauseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class QuestionCauseCandidate(CauseModel):
    """契约增量（cause-candidate@v2 可选字段）：单题级候选错因。

    每道错题一条；证据不足的题必须显式 status=evidence_insufficient，
    不得硬给 causeCode。用户决策粒度仍是会话级，本记录只作候选展示
    与错误模式聚合输入。旧 payload 没有该字段时缺省为空列表。
    """

    question_id: str = Field(alias="questionId", min_length=1, max_length=240)
    status: Literal["candidate", "evidence_insufficient"]
    cause_code: str | None = Field(default=None, alias="causeCode", max_length=80)
    confidence: Literal["low", "medium", "high"] | None = None
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=20)
    rationale: str = Field(min_length=4, max_length=600)

    @model_validator(mode="after")
    def validate_status_shape(self) -> "QuestionCauseCandidate":
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("question candidate evidence refs must be unique")
        if self.status == "candidate":
            if not self.cause_code or self.confidence is None:
                raise ValueError(
                    "a per-question candidate requires causeCode and confidence"
                )
        elif self.cause_code is not None or self.confidence is not None:
            raise ValueError(
                "an evidence-insufficient question cannot carry causeCode or confidence"
            )
        return self


class CandidateReady(CauseModel):
    kind: Literal["candidate_ready"] = "candidate_ready"
    cause_code: str = Field(alias="causeCode", min_length=2, max_length=80)
    summary: str = Field(min_length=4, max_length=600)
    improvement: str = Field(min_length=4, max_length=800)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=6)
    # 契约增量：逐题候选；旧输出没有该字段时缺省为空，不推断补全。
    question_candidates: list[QuestionCauseCandidate] = Field(
        default_factory=list,
        alias="questionCandidates",
        max_length=100,
    )

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
    # 契约增量：整体证据不足时仍可对个别题给出显式逐题判断；缺省为空。
    question_candidates: list[QuestionCauseCandidate] = Field(
        default_factory=list,
        alias="questionCandidates",
        max_length=100,
    )


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
    # 契约增量：v1 冻结证据没有下面两段；回放旧会话时缺省为空，
    # 不伪造轨迹或公式，evidenceRefs 子集校验自然不放行对应命名空间。
    action_trace: dict = Field(default_factory=dict, alias="actionTrace")
    ink_formulas: dict = Field(default_factory=dict, alias="inkFormulas")

    @property
    def allowed_evidence_refs(self) -> set[str]:
        refs: set[str] = set()
        for section in (
            self.attempt,
            self.reflection,
            self.explanations,
            self.taxonomy,
            self.action_trace,
            self.ink_formulas,
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

    @property
    def attempt_result_codes(self) -> dict[str, str]:
        """冻结作答证据里逐题的 resultCode；v1 回放证据没有 items 时为空。"""

        return {
            str(item["questionId"]): str(item.get("resultCode", ""))
            for item in self.attempt.get("items", [])
            if isinstance(item, dict) and item.get("questionId")
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
    _validate_question_candidates(output=output, evidence=evidence)


def _validate_question_candidates(
    *,
    output: CandidateReady | FollowupRequired | EvidenceInsufficient,
    evidence: CauseAgentEvidence,
) -> None:
    question_candidates = getattr(output, "question_candidates", [])
    if not question_candidates:
        return
    question_ids = [item.question_id for item in question_candidates]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("cause agent produced duplicate per-question candidates")
    result_codes = evidence.attempt_result_codes
    for item in question_candidates:
        if not set(item.evidence_refs).issubset(evidence.allowed_evidence_refs):
            raise ValueError(
                "cause agent question candidate cited evidence outside the frozen tool results"
            )
        result_code = result_codes.get(item.question_id)
        if result_code is None:
            # 冻结证据里没有这道题（含 v1 回放证据缺 items）：拒绝而不是放行。
            raise ValueError(
                "cause agent question candidate targets a question outside the frozen attempt"
            )
        if result_code == "correct":
            raise ValueError(
                "cause agent question candidate cannot assign a cause to a correct answer"
            )
        if (
            item.status == "candidate"
            and item.cause_code not in evidence.allowed_cause_codes
        ):
            raise ValueError("cause agent output used an unpublished cause code")
