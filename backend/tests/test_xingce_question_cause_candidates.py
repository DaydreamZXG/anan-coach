"""错因候选下探到单题：逐题候选生成、投影与错误模式聚合优先级。

约束（本轮范围）：
- cause agent 输出契约新增逐题候选，证据不足的题显式 evidence_insufficient；
- 用户决策粒度保持会话级（confirm/reject/replace 不变），逐题候选纯展示；
- learning_brain 错误模式聚合优先消费逐题 causeCode，没有逐题候选时
  回退既有 session 摊派，反证计数逻辑不动。
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.learning_brain.repository import LearningBrainRepository
from app.domains.learning_brain.service import _error_pattern
from app.domains.xingce_learning.cause_agent.contracts import (
    CandidateReady,
    CauseAgentEvidence,
    QuestionCauseCandidate,
)
from app.domains.xingce_learning.cause_agent.provider import CauseAgentProviderResult
from tests.test_xingce_cause_agent_workflow import SequenceProvider, _reflect, _run_one
from tests.test_xingce_planning_closure import _submitted_attempt


def test_question_candidates_project_per_question_and_drive_module_attribution(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="question-cause-preferred-token",
        choices=["D", "C", "B"],
    )
    question_ids = [
        str(question["questionId"]) for question in submitted["bundle"]["questions"]
    ]
    first, second, third = question_ids
    review = submitted["review"]
    _reflect(client, auth, review, key="question-cause-preferred-reflection")
    _run_one(
        db_session_factory,
        _PerQuestionProvider(
            {
                first: "stem_misread",
                second: "calculation_error",
                third: None,
            }
        ),
    )

    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    )
    assert current.status_code == 200, current.text
    candidate_view = current.json()["data"]
    candidate = candidate_view["review"]["aiCandidate"]
    assert candidate["state"] == "candidate_ready"
    by_question = {
        item["questionId"]: item for item in candidate["questionCandidates"]
    }
    assert set(by_question) == set(question_ids)
    assert by_question[first]["status"] == "candidate"
    assert by_question[first]["causeCode"] == "stem_misread"
    assert by_question[first]["confidence"] == "medium"
    assert by_question[first]["rationale"]
    assert by_question[first]["evidenceRefs"]
    assert by_question[second]["causeCode"] == "calculation_error"
    # 证据不足的题显式标注，不硬给错因编码。
    assert by_question[third]["status"] == "evidence_insufficient"
    assert by_question[third]["causeCode"] is None
    assert by_question[third]["confidence"] is None

    # 用户决策仍是会话级 confirm：不存在逐题决策命令。
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "question-cause-preferred-decision"},
        json={
            "expectedRevision": candidate_view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text

    # 第二题换到另一个真实模块，验证逐题归模块而不是 session 摊派。
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update product.questions set module_name = '资料分析' "
                "where question_id = :question_id"
            ),
            {"question_id": second},
        )
    user_id = _session_owner(db_session_factory, session_id=submitted["sessionId"])
    rows = _xingce_patterns(db_session_factory, user_id=user_id)
    assert {
        (row["module_key"], row["cause_code"], row["occurrence_count"])
        for row in rows
    } == {
        ("言语理解", "stem_misread", 1),
        ("资料分析", "calculation_error", 1),
    }
    # 证据不足的第三题不产生任何错误模式行（session 摊派会把它算进言语理解）。
    assert all(int(row["counterevidence_count"]) == 0 for row in rows)
    patterns = [_error_pattern(row) for row in rows]
    assert {item.display_name for item in patterns} == {
        "题干条件或问法审读错误",
        "方法成立但计算、单位或抄写错误",
    }
    assert all(item.status == "confirmed" for item in patterns)


def test_missing_question_candidates_fall_back_to_session_apportionment(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="question-cause-fallback-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="question-cause-fallback-reflection")
    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    )
    assert current.status_code == 200, current.text
    candidate_view = current.json()["data"]
    candidate = candidate_view["review"]["aiCandidate"]
    # 老式（无逐题候选）输出的投影保持显式空，不推断补全。
    assert candidate["questionCandidates"] == []
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "question-cause-fallback-decision"},
        json={
            "expectedRevision": candidate_view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text

    user_id = _session_owner(db_session_factory, session_id=submitted["sessionId"])
    rows = _xingce_patterns(db_session_factory, user_id=user_id)
    # 回退路径：既有 session 摊派语义原样保留（display_name 用会话级 cause_text）。
    assert len(rows) == 1
    assert rows[0]["module_key"] == "言语理解"
    assert rows[0]["cause_code"] == "stem_misread"
    assert rows[0]["occurrence_count"] == 1
    assert rows[0]["display_name"] == "漏看了题干中的关键限定词"


class _PerQuestionProvider(SequenceProvider):
    """按题给出候选/证据不足判断的桩 provider；引用真实冻结证据。"""

    def __init__(self, causes_by_question: dict[str, str | None]) -> None:
        super().__init__(["candidate_ready"])
        self._causes_by_question = causes_by_question

    def generate(
        self,
        *,
        evidence: CauseAgentEvidence,
        followup_answer: str | None,
        idempotency_key: str,
    ) -> CauseAgentProviderResult:
        del idempotency_key
        self.followup_answers.append(followup_answer)
        items = {
            str(item["questionId"]): item
            for item in evidence.attempt["items"]
        }
        question_candidates: list[QuestionCauseCandidate] = []
        for question_id, cause_code in self._causes_by_question.items():
            ref = f"assessment:{items[question_id]['assessmentId']}"
            if cause_code is None:
                question_candidates.append(
                    QuestionCauseCandidate(
                        questionId=question_id,
                        status="evidence_insufficient",
                        evidenceRefs=[ref],
                        rationale="该题没有足够过程证据，不硬给错因。",
                    )
                )
            else:
                question_candidates.append(
                    QuestionCauseCandidate(
                        questionId=question_id,
                        status="candidate",
                        causeCode=cause_code,
                        confidence="medium",
                        evidenceRefs=[ref],
                        rationale="依据该题作答证据与复盘原话的逐题判断。",
                    )
                )
        output = CandidateReady(
            causeCode="stem_misread",
            summary="漏看了题干中的关键限定词",
            improvement="作答前先圈出问法和限定词再比较选项",
            evidenceRefs=sorted(evidence.allowed_evidence_refs)[:3],
            questionCandidates=question_candidates,
        )
        payload = output.model_dump(by_alias=True, mode="json")
        return CauseAgentProviderResult(
            output=output,
            usage=ModelTokenUsage(
                input_tokens=200,
                cached_input_tokens=20,
                output_tokens=60,
            ),
            output_fingerprint=sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
            latency_ms=25,
        )


def _session_owner(factory: sessionmaker[Session], *, session_id: str) -> str:
    with factory() as session:
        return str(
            session.execute(
                text(
                    "select user_id from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": session_id},
            ).scalar_one()
        )


def _xingce_patterns(
    factory: sessionmaker[Session],
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    with factory() as session:
        rows = LearningBrainRepository(session).error_patterns(user_id=user_id)
        return [dict(row) for row in rows if str(row["track"]) == "xingce"]
