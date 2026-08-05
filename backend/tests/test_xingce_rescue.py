"""错题即时救场：错因 -> 方法卡 -> 同类题 的桥接。

产品决策：
- 只在「方法不当(method_selection)」与「知识缺口(knowledge_gap)」两类错因救场；
  粗心/时间压力/蒙/放弃不救。
- 方法卡通过知识树节点 method_card_ids 挂接取回。
- 同类题按同一考点从题库选取（排除本组已做过的题）。

本测试走完整闭环：seed 含方法卡的发布 -> 作答 -> 复盘 -> 错因确认 -> 拉救场。
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.xingce_learning.cause_agent.contracts import (
    CandidateReady,
    CauseAgentEvidence,
    QuestionCauseCandidate,
)
from app.domains.xingce_learning.cause_agent.provider import CauseAgentProviderResult
from tests.factories.frozen_release import (
    FrozenKnowledgeNodeSeed,
    FrozenMethodCardSeed,
    FrozenQuestionSeed,
    seed_canonical_frozen_release,
)
from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)
from tests.test_xingce_cause_agent_workflow import (
    SequenceProvider,
    _reflect,
    _run_one,
)
from tests.task_editing_helpers import claim_task_edit_headers

_NODE_ID = "rescue-node-01"
_CARD_ID = "mc_rescue_verbal_basic_01"
_OPTIONS = ("A", "B", "C", "D")


def _seed_rescue_release(factory: sessionmaker[Session]) -> None:
    """Seed 唯一的 fresh active 发布：节点挂方法卡，题库够同类题选取。"""
    with factory.begin() as session:
        seed_canonical_frozen_release(
            session,
            release_key="rescue",
            nodes=(
                FrozenKnowledgeNodeSeed(
                    node_id=_NODE_ID,
                    module_name="言语理解与表达",
                    tag_value="言语理解基础",
                    method_card_ids=(_CARD_ID,),
                ),
            ),
            method_cards=(
                FrozenMethodCardSeed(
                    id=_CARD_ID,
                    module_name="言语理解与表达",
                    title="言语理解基础：先圈限定词再比较",
                    recognition="题干出现限定词或问法陷阱时使用",
                    method_steps=("先圈出问法和限定词", "再逐项排除比较"),
                    fix_action="做 3 道同类题巩固",
                    time_budget_sec=60,
                    pitfall="别把猜测当方法",
                ),
            ),
            questions=tuple(
                FrozenQuestionSeed(
                    question_id=f"rescue-q-{index:02d}",
                    stem=f"言语理解救场题 {index}",
                    answer=_OPTIONS[(index - 1) % 4],
                    node_id=_NODE_ID,
                    module_name="言语理解",
                    occurrence_count=80 - index,
                    explanation=f"解析 {index}",
                )
                for index in range(1, 19)
            ),
        )


def _seed_rescue_learner(
    factory: sessionmaker[Session],
    *,
    token: str,
) -> str:
    """Seed 学习者并把每日时长压到 20 分钟，得到 10 题的小组。

    组大小 = min(30, 题库供给, max(3, 20//2)) = 10，证据引用数（每题的
    assessment + content-version 两条）约 20，低于 governance 的 50 上限；
    同时保留题库里的同类题余量用于救场选取。
    """
    user_id = _seed_learner_and_published_essay_content(
        factory,
        access_token=token,
        include_objective_questions=False,
    )
    with factory.begin() as session:
        session.execute(
            text(
                """
                update learning.weekly_study_preference_versions
                set weekday_minutes = 20, weekend_minutes = 20,
                    long_block_minutes = 20, fragment_minutes = 10
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        )
    return user_id


def _run_attempt(
    factory: sessionmaker[Session],
    client: TestClient,
    auth: dict[str, str],
    *,
    token: str,
    choices: list[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Start -> checkpoint -> submit，返回 (submitted 投影, 带编辑租约的 auth)。"""
    task = next(
        task
        for task in client.get("/api/v2/plans/today", headers=auth).json()["data"]["tasks"]
        if task["track"] == "xingce"
    )
    start = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": f"start-{token}"},
        json={"planTaskId": task["id"]},
    )
    assert start.status_code == 200, start.text
    started = start.json()["data"]
    auth = {
        **auth,
        **claim_task_edit_headers(
            client,
            auth,
            resource_type="xingce_task_session",
            resource_id=started["sessionId"],
            revision=started["revision"],
            device_suffix=token,
        ),
    }
    questions = started["bundle"]["questions"]
    with factory() as session:
        scoring_bundle = session.execute(
            text(
                "select scoring_bundle from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": started["sessionId"]},
        ).scalar_one()
    correct_options_by_question = {
        str(item["questionId"]): [str(option) for option in item["correctOptionIds"]]
        for item in scoring_bundle["questions"]
    }
    resolved_choices: list[str] = []
    for index, question in enumerate(questions):
        correct = correct_options_by_question[question["questionId"]]
        if index < len(choices):
            resolved_choices.append(
                next(option for option in ("A", "B", "C", "D") if option not in correct)
            )
        else:
            resolved_choices.append(correct[0])
    elapsed = [40 + index for index in range(len(questions))]
    checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": f"checkpoint-{token}"},
        json={
            "expectedRevision": started["revision"],
            "currentQuestionId": questions[-1]["questionId"],
            "answers": [
                {
                    "questionId": question["questionId"],
                    "selectedOptionIds": [choice],
                    "eliminatedOptionIds": [],
                    "confidence": "high",
                    "marked": False,
                    "skipped": False,
                    "elapsedSeconds": elapsed[index],
                    "draftText": "",
                    "materialPosition": None,
                }
                for index, (question, choice) in enumerate(
                    zip(questions, resolved_choices, strict=True)
                )
            ],
            "totalElapsedSeconds": sum(elapsed),
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    checkpointed = checkpoint.json()["data"]
    submit = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": f"submit-{token}"},
        json={
            "expectedRevision": checkpointed["revision"],
            "totalElapsedSeconds": sum(elapsed) + 3,
        },
    )
    assert submit.status_code == 200, submit.text
    return submit.json()["data"], auth


class _MethodSelectionProvider(SequenceProvider):
    """逐题错因：仅对错题给出 method_selection 候选；不做正确题的逐题判断。"""

    def __init__(self, rescusable_question_ids: list[str]) -> None:
        super().__init__(["candidate_ready"])
        self._rescusable = set(rescusable_question_ids)

    def generate(
        self,
        *,
        evidence: CauseAgentEvidence,
        followup_answer: str | None,
        idempotency_key: str,
    ) -> CauseAgentProviderResult:
        del idempotency_key
        self.followup_answers.append(followup_answer)
        items = {str(item["questionId"]): item for item in evidence.attempt["items"]}
        question_candidates: list[QuestionCauseCandidate] = []
        # 只对错题做逐题候选；正确题的 evidenceRefs 不允许出现在逐题候选里。
        for question_id in self._rescusable:
            if question_id not in items:
                continue
            ref = f"assessment:{items[question_id]['assessmentId']}"
            question_candidates.append(
                QuestionCauseCandidate(
                    questionId=question_id,
                    status="candidate",
                    causeCode="method_selection",
                    confidence="medium",
                    evidenceRefs=[ref],
                    rationale="该题存在方法选择或步骤组织不当的过程证据。",
                )
            )
        output = CandidateReady(
            causeCode="method_selection",
            summary="方法选择或步骤组织不当",
            improvement="先看方法卡再练同类题。",
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


def test_rescue_returns_method_card_and_similar_question_for_method_selection(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_rescue_release(db_session_factory)
    token = "rescue-method-selection-token"
    _seed_rescue_learner(db_session_factory, token=token)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    submitted, auth = _run_attempt(db_session_factory, client, auth, token=token, choices=["D", "B", "C"])
    review = submitted["review"]
    question_ids = [
        str(question["questionId"]) for question in submitted["bundle"]["questions"]
    ]
    _reflect(client, auth, review, key="rescue-method-reflection")
    _run_one(db_session_factory, _MethodSelectionProvider(question_ids[:2]))

    view = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    candidate = view["review"]["aiCandidate"]
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "rescue-method-decision"},
        json={
            "expectedRevision": view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text

    rescue = client.get(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/rescue",
        headers=auth,
    )
    assert rescue.status_code == 200, rescue.text
    data = rescue.json()["data"]
    assert data["reviewId"] == review["reviewId"]
    assert data["items"], "method_selection 错因应触发救场"

    item = data["items"][0]
    assert item["causeCode"] == "method_selection"
    assert "方法选择" in item["explanation"]
    assert item["methodCard"] is not None
    assert item["methodCard"]["cardId"] == _CARD_ID
    assert item["methodCard"]["title"] == "言语理解基础：先圈限定词再比较"
    assert "先圈出问法和限定词" in item["methodCard"]["methodSteps"]
    assert item["similarQuestion"] is not None
    assert item["similarQuestion"]["questionId"].startswith("rescue-q-")
    # 同类题必须是另一道题，不能是本组已做过的题。
    assert item["similarQuestion"]["questionId"] not in question_ids
    assert item["similarQuestion"]["correctOptionIds"]
    assert item["node"]["nodeId"] == _NODE_ID
    assert item["node"]["displayPath"].endswith("言语理解基础")


def test_rescue_applies_session_cause_to_all_wrong_questions_without_per_question(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_rescue_release(db_session_factory)
    token = "rescue-session-cause-token"
    _seed_rescue_learner(db_session_factory, token=token)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    submitted, auth = _run_attempt(db_session_factory, client, auth, token=token, choices=["D", "B", "C"])
    review = submitted["review"]
    _reflect(client, auth, review, key="rescue-session-reflection")

    # 老式候选：无逐题字段，会话级错因是 method_selection。
    _run_one(db_session_factory, _SessionMethodSelectionProvider())

    view = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    candidate = view["review"]["aiCandidate"]
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "rescue-session-decision"},
        json={
            "expectedRevision": view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text

    rescue = client.get(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/rescue",
        headers=auth,
    ).json()["data"]
    assert rescue["items"]
    assert all(
        item["causeCode"] == "method_selection" for item in rescue["items"]
    )


class _SessionMethodSelectionProvider(SequenceProvider):
    """无逐题候选的会话级 method_selection 错因。"""

    def __init__(self) -> None:
        super().__init__(["candidate_ready"])

    def generate(
        self,
        *,
        evidence: CauseAgentEvidence,
        followup_answer: str | None,
        idempotency_key: str,
    ) -> CauseAgentProviderResult:
        del idempotency_key
        self.followup_answers.append(followup_answer)
        output = CandidateReady(
            causeCode="method_selection",
            summary="方法选择或步骤组织不当",
            improvement="先看方法卡再练同类题。",
            evidenceRefs=sorted(evidence.allowed_evidence_refs)[:3],
        )
        payload = output.model_dump(by_alias=True, mode="json")
        return CauseAgentProviderResult(
            output=output,
            usage=ModelTokenUsage(
                input_tokens=180,
                cached_input_tokens=20,
                output_tokens=45,
            ),
            output_fingerprint=sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
            latency_ms=25,
        )


def test_rescue_skips_non_rescuable_causes(
    db_session_factory: sessionmaker[Session],
) -> None:
    """time_pressure（时间压力）等错因不触发救场。"""
    _seed_rescue_release(db_session_factory)
    token = "rescue-time-pressure-token"
    _seed_rescue_learner(db_session_factory, token=token)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    submitted, auth = _run_attempt(db_session_factory, client, auth, token=token, choices=["D", "B", "C"])
    review = submitted["review"]
    _reflect(client, auth, review, key="rescue-time-reflection")
    _run_one(db_session_factory, _TimePressureProvider())

    view = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    candidate = view["review"]["aiCandidate"]
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "rescue-time-decision"},
        json={
            "expectedRevision": view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text

    rescue = client.get(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/rescue",
        headers=auth,
    ).json()["data"]
    assert rescue["items"] == []


class _TimePressureProvider(SequenceProvider):
    def __init__(self) -> None:
        super().__init__(["candidate_ready"])

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
            if str(item["resultCode"]) in {"incorrect", "timeout"}
        }
        question_candidates = [
            QuestionCauseCandidate(
                questionId=question_id,
                status="candidate",
                causeCode="time_pressure",
                confidence="high",
                evidenceRefs=[f"assessment:{items[question_id]['assessmentId']}"],
                rationale="该题超出建议用时，判断为时间压力。",
            )
            for question_id in items
        ]
        output = CandidateReady(
            causeCode="time_pressure",
            summary="时间分配导致超时或仓促作答",
            improvement="下一组先做会做的题。",
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
