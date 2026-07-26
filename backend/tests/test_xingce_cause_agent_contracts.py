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
    QuestionCauseCandidate,
    validate_grounded_output,
)
from app.domains.xingce_learning.real_attempt.schemas import CauseDecisionCommand


def test_cause_agent_definition_freezes_tools_and_budgets() -> None:
    assert CAUSE_AGENT_DEFINITION.contract_version == "cause-agent-definition@v1"
    assert CAUSE_AGENT_DEFINITION.prompt_version == "xingce-cause-prompt@v3"
    assert CAUSE_AGENT_DEFINITION.definition_release_id == (
        "xingce-cause-agent-2026-07-26.2"
    )
    assert [tool.name for tool in CAUSE_AGENT_DEFINITION.tools] == [
        "load_attempt_evidence",
        "load_user_reflection",
        "load_published_question_explanation",
        "load_cause_taxonomy",
        "load_action_trace",
        "load_ink_formulas",
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


def test_new_evidence_namespaces_are_grounded_only_when_tools_returned_them() -> None:
    evidence = _evidence()
    grounded = CandidateReady(
        causeCode="stem_misread",
        summary="轨迹显示 12 秒内改选并留下批注",
        improvement="先完成题干标注再作答",
        evidenceRefs=["action-trace:q-1", "ink-formula:q-1"],
    )
    validate_grounded_output(output=grounded, evidence=evidence, followup_round=0)

    outside = grounded.model_copy(
        update={"evidence_refs": ["action-trace:q-never-traced"]}
    )
    with pytest.raises(ValueError, match="outside the frozen tool results"):
        validate_grounded_output(output=outside, evidence=evidence, followup_round=0)


def test_v1_frozen_evidence_replays_without_new_sections() -> None:
    # 旧 v1 会话冻结的 evidence 没有 actionTrace/inkFormulas 两段：
    # 回放校验必须原样通过，且不得凭空放行新命名空间。
    replayed = CauseAgentEvidence.model_validate(
        {
            "attempt": {"evidenceRefs": ["assessment:1"]},
            "reflection": {"evidenceRefs": ["reflection-version:1"]},
            "explanations": {"evidenceRefs": ["content-version:1"]},
            "taxonomy": {
                "evidenceRefs": ["cause-taxonomy:v1"],
                "causes": [{"code": "stem_misread"}],
            },
        }
    )
    assert replayed.action_trace == {}
    assert replayed.ink_formulas == {}
    assert replayed.allowed_evidence_refs == {
        "assessment:1",
        "reflection-version:1",
        "content-version:1",
        "cause-taxonomy:v1",
    }
    old_output = CandidateReady(
        causeCode="stem_misread",
        summary="漏看了题干限定词",
        improvement="先圈出限定词再比较选项",
        evidenceRefs=["assessment:1"],
    )
    validate_grounded_output(output=old_output, evidence=replayed, followup_round=0)
    with pytest.raises(ValueError, match="outside the frozen tool results"):
        validate_grounded_output(
            output=old_output.model_copy(
                update={"evidence_refs": ["action-trace:q-1"]}
            ),
            evidence=replayed,
            followup_round=0,
        )


def test_question_candidates_are_grounded_per_question_or_explicitly_insufficient() -> None:
    evidence = _evidence()
    grounded = _candidate_with_questions(
        [
            QuestionCauseCandidate(
                questionId="q-1",
                status="candidate",
                causeCode="stem_misread",
                confidence="medium",
                evidenceRefs=["assessment:1", "action-trace:q-1"],
                rationale="轨迹显示 12 秒内改选，且复盘原话提到漏看限定词。",
            ),
            QuestionCauseCandidate(
                questionId="q-2",
                status="evidence_insufficient",
                evidenceRefs=["assessment:2"],
                rationale="该题没有轨迹、公式或复盘线索，证据不足不硬给错因。",
            ),
        ]
    )
    validate_grounded_output(output=grounded, evidence=evidence, followup_round=0)

    # 逐题候选不得引用冻结工具结果之外的证据。
    outside = _candidate_with_questions(
        [
            QuestionCauseCandidate(
                questionId="q-1",
                status="candidate",
                causeCode="stem_misread",
                confidence="high",
                evidenceRefs=["invented:q-1"],
                rationale="引用了不存在的证据命名空间。",
            )
        ]
    )
    with pytest.raises(ValueError, match="outside the frozen tool results"):
        validate_grounded_output(output=outside, evidence=evidence, followup_round=0)

    # 逐题候选只能使用已发布的错因分类。
    invented_code = _candidate_with_questions(
        [
            QuestionCauseCandidate(
                questionId="q-1",
                status="candidate",
                causeCode="invented_code",
                confidence="high",
                evidenceRefs=["assessment:1"],
                rationale="使用了未发布的错因编码。",
            )
        ]
    )
    with pytest.raises(ValueError, match="unpublished cause code"):
        validate_grounded_output(
            output=invented_code,
            evidence=evidence,
            followup_round=0,
        )


def test_question_candidates_cannot_target_correct_or_unknown_questions() -> None:
    evidence = _evidence()
    correct_target = _candidate_with_questions(
        [
            QuestionCauseCandidate(
                questionId="q-correct",
                status="candidate",
                causeCode="stem_misread",
                confidence="high",
                evidenceRefs=["assessment:1"],
                rationale="这题客观答对，不允许被编造错因。",
            )
        ]
    )
    with pytest.raises(ValueError, match="correct answer"):
        validate_grounded_output(
            output=correct_target,
            evidence=evidence,
            followup_round=0,
        )

    unknown_target = _candidate_with_questions(
        [
            QuestionCauseCandidate(
                questionId="q-not-in-attempt",
                status="candidate",
                causeCode="stem_misread",
                confidence="high",
                evidenceRefs=["assessment:1"],
                rationale="这题不在冻结作答证据里。",
            )
        ]
    )
    with pytest.raises(ValueError, match="outside the frozen attempt"):
        validate_grounded_output(
            output=unknown_target,
            evidence=evidence,
            followup_round=0,
        )

    duplicated = _candidate_with_questions(
        [
            QuestionCauseCandidate(
                questionId="q-1",
                status="candidate",
                causeCode="stem_misread",
                confidence="high",
                evidenceRefs=["assessment:1"],
                rationale="同一道题只允许一条逐题候选。",
            ),
            QuestionCauseCandidate(
                questionId="q-1",
                status="evidence_insufficient",
                evidenceRefs=["assessment:1"],
                rationale="同一道题的第二条候选必须被拒绝。",
            ),
        ]
    )
    with pytest.raises(ValueError, match="duplicate per-question"):
        validate_grounded_output(output=duplicated, evidence=evidence, followup_round=0)


def test_question_candidate_shape_rejects_hard_assigned_or_incomplete_entries() -> None:
    # 证据不足的题必须显式 evidence_insufficient，不得携带 causeCode。
    with pytest.raises(ValidationError, match="cannot carry causeCode"):
        QuestionCauseCandidate(
            questionId="q-2",
            status="evidence_insufficient",
            causeCode="stem_misread",
            evidenceRefs=["assessment:2"],
            rationale="证据不足时硬给错因必须失败。",
        )
    # 给出候选就必须同时给 causeCode 和 confidence。
    with pytest.raises(ValidationError, match="requires causeCode and confidence"):
        QuestionCauseCandidate(
            questionId="q-1",
            status="candidate",
            evidenceRefs=["assessment:1"],
            rationale="缺少错因编码与信心的候选必须失败。",
        )
    # v1 冻结证据没有逐题 items：带逐题候选的新输出不得凭空放行。
    replayed = CauseAgentEvidence.model_validate(
        {
            "attempt": {"evidenceRefs": ["assessment:1"]},
            "reflection": {"evidenceRefs": ["reflection-version:1"]},
            "explanations": {"evidenceRefs": ["content-version:1"]},
            "taxonomy": {
                "evidenceRefs": ["cause-taxonomy:v1"],
                "causes": [{"code": "stem_misread"}],
            },
        }
    )
    assert replayed.attempt_result_codes == {}
    with pytest.raises(ValueError, match="outside the frozen attempt"):
        validate_grounded_output(
            output=_candidate_with_questions(
                [
                    QuestionCauseCandidate(
                        questionId="q-1",
                        status="candidate",
                        causeCode="stem_misread",
                        confidence="high",
                        evidenceRefs=["assessment:1"],
                        rationale="回放证据没有逐题事实时不放行逐题候选。",
                    )
                ]
            ),
            evidence=replayed,
            followup_round=0,
        )


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
        attempt={
            "evidenceRefs": ["assessment:1", "assessment:2"],
            "items": [
                {"questionId": "q-1", "resultCode": "incorrect"},
                {"questionId": "q-2", "resultCode": "guessed_correct"},
                {"questionId": "q-correct", "resultCode": "correct"},
            ],
        },
        reflection={"evidenceRefs": ["reflection-version:1"]},
        explanations={"evidenceRefs": ["content-version:1"]},
        taxonomy={
            "evidenceRefs": ["cause-taxonomy:v1"],
            "causes": [{"code": "stem_misread"}],
        },
        action_trace={"evidenceRefs": ["action-trace:q-1"]},
        ink_formulas={"evidenceRefs": ["ink-formula:q-1"]},
    )


def _candidate_with_questions(
    question_candidates: list[QuestionCauseCandidate],
) -> CandidateReady:
    return CandidateReady(
        causeCode="stem_misread",
        summary="漏看了题干中的关键限定词",
        improvement="作答前先圈出问法和限定词再比较选项",
        evidenceRefs=["assessment:1", "reflection-version:1"],
        questionCandidates=question_candidates,
    )
