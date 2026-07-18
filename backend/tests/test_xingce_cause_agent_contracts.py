from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domains.ai.agent_runtime.contracts import CAUSE_AGENT_DEFINITION
from app.domains.xingce_learning.cause_agent.contracts import (
    CandidateReady,
    CauseAgentEvidence,
    ENTITLEMENT_POLICY_VERSION,
    FollowupRequired,
    validate_grounded_output,
)
from app.domains.xingce_learning.real_attempt.schemas import CauseDecisionCommand


def test_cause_agent_definition_freezes_tools_and_budgets() -> None:
    assert CAUSE_AGENT_DEFINITION.contract_version == "cause-agent-definition@v1"
    assert [tool.name for tool in CAUSE_AGENT_DEFINITION.tools] == [
        "load_attempt_evidence",
        "load_user_reflection",
        "load_published_question_explanation",
        "load_cause_taxonomy",
    ]
    assert CAUSE_AGENT_DEFINITION.budget.model_dump(by_alias=True) == {
        "maxModelCalls": 2,
        "maxToolCalls": 6,
        "maxFollowupRounds": 1,
        "maxConsecutiveNoProgress": 2,
    }
    assert len(CAUSE_AGENT_DEFINITION.fingerprint) == 64
    assert ENTITLEMENT_POLICY_VERSION == "metered-model-round@v1"


def test_grounded_candidate_must_use_published_taxonomy_and_evidence() -> None:
    evidence = _evidence()
    valid = CandidateReady(
        causeCode="stem_misread",
        summary="漏看了题干限定词",
        improvement="先圈出限定词再比较选项",
        evidenceRefs=["assessment:1", "reflection-version:1"],
    )
    validate_grounded_output(output=valid, evidence=evidence, followup_round=0)

    invented = valid.model_copy(update={"cause_code": "invented"})
    with pytest.raises(ValueError, match="unpublished cause code"):
        validate_grounded_output(output=invented, evidence=evidence, followup_round=0)


def test_second_followup_and_unbound_decision_are_rejected() -> None:
    with pytest.raises(ValueError, match="second followup"):
        validate_grounded_output(
            output=FollowupRequired(
                question="你当时是不会方法，还是来不及计算？",
                rationale="现有文字无法区分知识和时间原因",
                evidenceRefs=["reflection-version:1"],
            ),
            evidence=_evidence(),
            followup_round=1,
        )
    with pytest.raises(ValidationError, match="candidateVersionId"):
        CauseDecisionCommand(expectedRevision=3, decision="confirm")
    command = CauseDecisionCommand(
        expectedRevision=3,
        candidateVersionId=uuid4(),
        decision="reject",
    )
    assert command.decision == "reject"


def _evidence() -> CauseAgentEvidence:
    return CauseAgentEvidence(
        attempt={"evidenceRefs": ["assessment:1"]},
        reflection={"evidenceRefs": ["reflection-version:1"]},
        explanations={"evidenceRefs": ["content-version:1"]},
        taxonomy={
            "evidenceRefs": ["cause-taxonomy:v1"],
            "causes": [{"code": "stem_misread"}],
        },
    )
