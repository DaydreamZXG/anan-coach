from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.xingce_learning.cause_agent.contracts import CandidateReady
from app.domains.ai.model_cost import ModelTokenUsage
from app.domains.xingce_learning.cause_agent.provider import CauseAgentProviderResult
from app.domains.xingce_learning.cause_agent.repository import CauseAgentRepository
from app.domains.xingce_learning.cause_agent.tools import CauseAgentTools
from tests.factories import seed_route_frozen_release
from tests.task_editing_helpers import claim_task_edit_headers
from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)
from tests.test_xingce_cause_agent_workflow import (
    SequenceProvider,
    _reflect,
    _run_one,
)
from tests.test_xingce_learning_routes import _xingce_task
from tests.test_xingce_planning_closure import _submitted_attempt


def test_action_trace_tool_reports_only_recorded_behavior(
    db_session_factory: sessionmaker[Session],
) -> None:
    _client_obj, _auth, submitted, question_ids = _submitted_attempt_with_trace(
        db_session_factory,
        token="cause-action-trace-token",
    )
    session_id = str(submitted["sessionId"])
    first, second, third = question_ids
    with db_session_factory() as session:
        trace = _tools(session, session_id).load_action_trace(
            _scope(session, session_id)
        )

    assert trace["contractVersion"] == "xingce-action-trace@v2"
    items = {item["questionId"]: item for item in trace["items"]}
    assert set(items) == set(question_ids)

    traced = items[first]
    # 事件压缩为 类型+atMs+optionId：不携带 detail/sessionAtMs。
    assert traced["events"][0] == {"type": "question_entered", "atMs": 0}
    assert {"type": "option_eliminated", "atMs": 5_000, "optionId": "A"} in (
        traced["events"]
    )
    assert {"type": "option_selected", "atMs": 12_000, "optionId": "D"} in (
        traced["events"]
    )
    assert all(
        set(event) <= {"type", "atMs", "optionId"} for event in traced["events"]
    )
    assert traced["annotationSummary"] == {
        "strokeCount": 2,
        "erasedStrokeCount": 1,
        "firstStrokeAtMs": 3_000,
        "lastStrokeAtMs": 8_000,
    }
    assert traced["actionEventsTruncated"] is True

    # 没有记录的题保持显式空，绝不推断出任何过程。
    for untraced_id in (second, third):
        untraced = items[untraced_id]
        assert untraced["events"] == []
        assert untraced["annotationSummary"] == {
            "strokeCount": 0,
            "erasedStrokeCount": 0,
            "firstStrokeAtMs": None,
            "lastStrokeAtMs": None,
        }
        assert untraced["actionEventsTruncated"] is False

    assert trace["evidenceRefs"] == [f"action-trace:{first}"]


def test_action_trace_collects_anchored_segments_in_stroke_order_and_dedupes(
    db_session_factory: sessionmaker[Session],
) -> None:
    _client_obj, _auth, submitted, question_ids = _submitted_attempt_with_trace(
        db_session_factory,
        token="cause-anchor-trace-token",
        first_question_annotations=[
            {
                "strokeId": "stroke-1",
                "tool": "pen",
                "width": 0.01,
                "points": [{"x": 0.1, "y": 0.2}, {"x": 0.3, "y": 0.4}],
                "atMs": 3_000,
                "anchor": {
                    "blockId": "block-stem-01",
                    "segmentIndex": 1,
                    "segmentText": "下列说法不正确的是",
                },
            },
            {
                "strokeId": "stroke-2",
                "tool": "highlighter",
                "width": 0.05,
                "points": [{"x": 0.5, "y": 0.5}, {"x": 0.6, "y": 0.55}],
                "atMs": 8_000,
                "anchor": {
                    "blockId": "block-stem-01",
                    "segmentIndex": 1,
                    "segmentText": "下列说法不正确的是",
                },
            },
            {
                "strokeId": "stroke-3",
                "tool": "pen",
                "width": 0.01,
                "points": [{"x": 0.4, "y": 0.3}, {"x": 0.45, "y": 0.35}],
                "atMs": 9_000,
                "anchor": {
                    "blockId": "block-stem-01",
                    "segmentIndex": 2,
                    "segmentText": "与题意最相符",
                },
            },
            # 无 anchor 的旧笔迹不产生锚定段。
            {
                "strokeId": "stroke-4",
                "tool": "pen",
                "width": 0.01,
                "points": [{"x": 0.2, "y": 0.2}, {"x": 0.25, "y": 0.25}],
                "atMs": 10_000,
            },
        ],
    )
    session_id = str(submitted["sessionId"])
    first = question_ids[0]
    with db_session_factory() as session:
        trace = _tools(session, session_id).load_action_trace(_scope(session, session_id))

    assert trace["contractVersion"] == "xingce-action-trace@v2"
    traced = next(item for item in trace["items"] if item["questionId"] == first)
    # 旧式统计字段保持原样，锚定段按首次标记时间升序、同段去重计数。
    assert traced["annotationSummary"] == {
        "strokeCount": 4,
        "erasedStrokeCount": 0,
        "firstStrokeAtMs": 3_000,
        "lastStrokeAtMs": 10_000,
        "anchoredSegments": [
            {
                "blockId": "block-stem-01",
                "segmentIndex": 1,
                "segmentText": "下列说法不正确的是",
                "strokeCount": 2,
            },
            {
                "blockId": "block-stem-01",
                "segmentIndex": 2,
                "segmentText": "与题意最相符",
                "strokeCount": 1,
            },
        ],
        "anchoredSummary": (
            "用户依次标记了「下列说法不正确的是」、「与题意最相符」"
        ),
    }
    # 无 anchor 的题保持旧结构，绝不新增锚定字段。
    untraced = next(
        item for item in trace["items"] if item["questionId"] != first
    )
    assert set(untraced["annotationSummary"]) == {
        "strokeCount",
        "erasedStrokeCount",
        "firstStrokeAtMs",
        "lastStrokeAtMs",
    }
    assert trace["evidenceRefs"] == [f"action-trace:{first}"]


def test_ink_formula_tool_returns_current_confirmed_and_candidate_heads(
    db_session_factory: sessionmaker[Session],
) -> None:
    _client_obj, _auth, submitted, question_ids = _submitted_attempt_with_trace(
        db_session_factory,
        token="cause-ink-formula-token",
    )
    session_id = str(submitted["sessionId"])
    first, second, _third = question_ids
    _seed_ink_formulas(
        db_session_factory,
        session_id=session_id,
        confirmed_question_id=first,
        rejected_question_id=second,
    )
    with db_session_factory() as session:
        formulas = _tools(session, session_id).load_ink_formulas(
            _scope(session, session_id)
        )

    assert formulas["contractVersion"] == "xingce-ink-formula@v1"
    by_state = {item["state"]: item for item in formulas["items"]}
    assert set(by_state) == {"confirmed", "candidate"}
    assert by_state["confirmed"]["questionId"] == first
    assert by_state["confirmed"]["latex"] == "x^{2}+1"
    assert by_state["confirmed"]["source"] == "user_confirmed"
    assert by_state["confirmed"]["confirmedAt"] is not None
    assert by_state["candidate"]["questionId"] == first
    assert by_state["candidate"]["latex"] == "\\frac{3}{4}"
    assert by_state["candidate"]["confirmedAt"] is None
    # 被用户拒绝的公式链头不进入错因证据。
    assert all(item["questionId"] != second for item in formulas["items"])
    assert formulas["evidenceRefs"] == [f"ink-formula:{first}"]


def test_sessions_without_trace_or_formulas_return_explicit_emptiness(
    db_session_factory: sessionmaker[Session],
) -> None:
    _client_obj, _auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-empty-trace-token",
        choices=["D", "B", "C"],
    )
    session_id = str(submitted["sessionId"])
    with db_session_factory() as session:
        tools = _tools(session, session_id)
        scope = _scope(session, session_id)
        trace = tools.load_action_trace(scope)
        formulas = tools.load_ink_formulas(scope)

    # 老式作答（无事件、无批注）：逐题显式空，不发 action-trace 引用。
    assert all(item["events"] == [] for item in trace["items"])
    assert all(
        item["annotationSummary"]["strokeCount"] == 0 for item in trace["items"]
    )
    assert trace["evidenceRefs"] == [f"task-session:{session_id}:no-action-trace"]
    assert formulas["items"] == []
    assert formulas["evidenceRefs"] == [
        f"task-session:{session_id}:no-ink-formula"
    ]


def test_candidate_citing_action_trace_and_ink_formula_refs_passes_grounding(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, submitted, question_ids = _submitted_attempt_with_trace(
        db_session_factory,
        token="cause-trace-grounding-token",
    )
    session_id = str(submitted["sessionId"])
    first = question_ids[0]
    _seed_ink_formulas(
        db_session_factory,
        session_id=session_id,
        confirmed_question_id=first,
        rejected_question_id=question_ids[1],
    )
    _reflect(
        client,
        auth,
        submitted["review"],
        key="cause-trace-grounding-reflection",
    )
    cited_refs = [f"action-trace:{first}", f"ink-formula:{first}"]
    _run_one(db_session_factory, _TraceCitingProvider(cited_refs))

    current = client.get(
        f"/api/v2/xingce/task-sessions/{session_id}",
        headers=auth,
    )
    assert current.status_code == 200, current.text
    candidate = current.json()["data"]["review"]["aiCandidate"]
    assert candidate["state"] == "candidate_ready"
    assert candidate["evidenceRefs"] == cited_refs

    with db_session_factory() as session:
        job_result = session.execute(
            text("select result from platform.background_jobs")
        ).scalar_one()
        evidence = job_result["evidence"]
        assert evidence["actionTrace"]["contractVersion"] == "xingce-action-trace@v2"
        assert f"action-trace:{first}" in evidence["actionTrace"]["evidenceRefs"]
        assert evidence["inkFormulas"]["contractVersion"] == "xingce-ink-formula@v1"
        assert any(
            item["latex"] == "x^{2}+1"
            for item in evidence["inkFormulas"]["items"]
        )


class _TraceCitingProvider(SequenceProvider):
    def __init__(self, refs: list[str]) -> None:
        super().__init__(["candidate_ready"])
        self._refs = refs

    def generate(
        self,
        *,
        evidence: Any,
        followup_answer: str | None,
        idempotency_key: str,
    ) -> CauseAgentProviderResult:
        del idempotency_key
        self.followup_answers.append(followup_answer)
        assert set(self._refs) <= evidence.allowed_evidence_refs
        output = CandidateReady(
            causeCode="stem_misread",
            summary="轨迹显示第一题 12 秒内即改选并高亮题干后仍选错",
            improvement="先完成题干标注再作答，确认限定词后提交",
            evidenceRefs=self._refs,
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


_FROZEN_REFLECTION_ID = "00000000-0000-0000-0000-00000000cafe"


def _session_user_id(session: Session, session_id: str) -> str:
    return str(
        session.execute(
            text(
                "select user_id::text from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": session_id},
        ).scalar_one()
    )


def _tools(session: Session, session_id: str) -> CauseAgentTools:
    return CauseAgentTools(
        session=session,
        repository=CauseAgentRepository(),
        user_id=_session_user_id(session, session_id),
        session_id=session_id,
        reflection_version_id=_FROZEN_REFLECTION_ID,
    )


def _scope(session: Session, session_id: str) -> dict[str, str]:
    return {
        "userId": _session_user_id(session, session_id),
        "sessionId": session_id,
        "reflectionVersionId": _FROZEN_REFLECTION_ID,
    }


def _submitted_attempt_with_trace(
    factory: sessionmaker[Session],
    *,
    token: str,
    first_question_annotations: list[dict[str, Any]] | None = None,
) -> tuple[TestClient, dict[str, str], dict[str, Any], list[str]]:
    """与 _submitted_attempt 同构，但第一题带真实动作事件与批注笔画。

    first_question_annotations 可覆盖第一题的批注笔画（含 anchor）；
    缺省时使用无 anchor 的旧式两笔。
    """

    _seed_learner_and_published_essay_content(
        factory,
        access_token=token,
        include_objective_questions=False,
    )
    seed_route_frozen_release(factory, count=6)
    client = _client(factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _xingce_task(client, auth)
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
        ),
    }
    questions = started["bundle"]["questions"]
    question_ids = [question["questionId"] for question in questions]
    answers: list[dict[str, Any]] = []
    for index, (question_id, choice) in enumerate(
        zip(question_ids, ["D", "B", "C"], strict=True)
    ):
        answer: dict[str, Any] = {
            "questionId": question_id,
            "selectedOptionIds": [choice],
            "eliminatedOptionIds": [],
            "confidence": "high",
            "marked": False,
            "skipped": False,
            "elapsedSeconds": 40 + index,
            "draftText": "",
            "materialPosition": None,
        }
        if index == 0:
            answer["eliminatedOptionIds"] = ["A"]
            answer["actionEvents"] = [
                {"atMs": 0, "type": "question_entered"},
                {
                    "atMs": 5_000,
                    "sessionAtMs": 5_000,
                    "type": "option_eliminated",
                    "optionId": "A",
                },
                {"atMs": 12_000, "type": "option_selected", "optionId": "D"},
                {"atMs": 20_000, "type": "confidence_set", "detail": "high"},
                {"atMs": 22_000, "type": "question_left"},
            ]
            answer["actionEventsTruncated"] = True
            answer["annotations"] = (
                first_question_annotations
                if first_question_annotations is not None
                else [
                    {
                        "strokeId": "stroke-1",
                        "tool": "pen",
                        "width": 0.01,
                        "points": [
                            {"x": 0.1, "y": 0.2, "t": 0},
                            {"x": 0.3, "y": 0.4, "t": 40},
                        ],
                        "atMs": 3_000,
                    },
                    {
                        "strokeId": "stroke-2",
                        "tool": "highlighter",
                        "width": 0.05,
                        "points": [{"x": 0.5, "y": 0.5}, {"x": 0.6, "y": 0.55}],
                        "atMs": 8_000,
                        "erasedAtMs": 15_000,
                    },
                ]
            )
        answers.append(answer)
    checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": f"checkpoint-{token}"},
        json={
            "expectedRevision": started["revision"],
            "currentQuestionId": question_ids[-1],
            "answers": answers,
            "totalElapsedSeconds": 132,
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    checkpointed = checkpoint.json()["data"]
    submit = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": f"submit-{token}"},
        json={
            "expectedRevision": checkpointed["revision"],
            "totalElapsedSeconds": 135,
        },
    )
    assert submit.status_code == 200, submit.text
    return client, auth, submit.json()["data"], question_ids


def _seed_ink_formulas(
    factory: sessionmaker[Session],
    *,
    session_id: str,
    confirmed_question_id: str,
    rejected_question_id: str,
) -> None:
    """种入公式版本链：q1 一条已确认 + 一条候选，q2 一条已被拒绝。"""

    created_at = datetime(2026, 7, 26, 9, 0, tzinfo=UTC)
    with factory.begin() as session:
        user_id = session.execute(
            text(
                "select user_id::text from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": session_id},
        ).scalar_one()
        chains = (
            (confirmed_question_id, "x^{2}", "x^{2}+1", "confirmed"),
            (confirmed_question_id, "\\frac{3}{4}", None, None),
            (rejected_question_id, "y=2x", "y=2x", "rejected"),
        )
        for index, (question_id, candidate_latex, head_latex, head_state) in (
            enumerate(chains)
        ):
            request_id = str(uuid4())
            formula_id = str(uuid4())
            candidate_version_id = str(uuid4())
            session.execute(
                text(
                    """
                    insert into learning.math_ink_commands (
                      request_id, owner_user_id, session_id, question_id,
                      idempotency_key, input_fingerprint, ink_payload,
                      provider_release, provider_endpoint_fingerprint, state,
                      output_fingerprint, failure_code, latency_ms,
                      created_at, completed_at
                    ) values (
                      cast(:request_id as uuid), cast(:owner as uuid),
                      cast(:session_id as uuid), :question_id, :key, :input,
                      cast(:ink as jsonb), 'myscript-test-release',
                      :endpoint, 'candidate', :output, null, 12,
                      :created_at, :created_at
                    )
                    """
                ),
                {
                    "request_id": request_id,
                    "owner": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "key": f"ink-evidence-{session_id}-{index}",
                    "input": sha256(f"ink-{index}".encode()).hexdigest(),
                    "ink": json.dumps({"strokes": []}),
                    "endpoint": sha256(b"myscript-endpoint").hexdigest(),
                    "output": sha256(candidate_latex.encode()).hexdigest(),
                    "created_at": created_at,
                },
            )
            session.execute(
                text(
                    """
                    insert into learning.math_formula_versions (
                      formula_version_id, formula_id, request_id, owner_user_id,
                      session_id, question_id, version_no, state, latex, source,
                      created_at
                    ) values (
                      cast(:version_id as uuid), cast(:formula_id as uuid),
                      cast(:request_id as uuid), cast(:owner as uuid),
                      cast(:session_id as uuid), :question_id, 1, 'candidate',
                      :latex, 'myscript_candidate', :created_at
                    )
                    """
                ),
                {
                    "version_id": candidate_version_id,
                    "formula_id": formula_id,
                    "request_id": request_id,
                    "owner": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "latex": candidate_latex,
                    "created_at": created_at,
                },
            )
            if head_state is None:
                continue
            session.execute(
                text(
                    """
                    insert into learning.math_formula_versions (
                      formula_version_id, formula_id, request_id, owner_user_id,
                      session_id, question_id, version_no, state, latex, source,
                      supersedes_formula_version_id, created_at, confirmed_at
                    ) values (
                      cast(:version_id as uuid), cast(:formula_id as uuid),
                      cast(:request_id as uuid), cast(:owner as uuid),
                      cast(:session_id as uuid), :question_id, 2, :state,
                      :latex, 'user_confirmed', cast(:supersedes as uuid),
                      :created_at, :confirmed_at
                    )
                    """
                ),
                {
                    "version_id": str(uuid4()),
                    "formula_id": formula_id,
                    "request_id": request_id,
                    "owner": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "state": head_state,
                    "latex": head_latex,
                    "supersedes": candidate_version_id,
                    "created_at": created_at,
                    "confirmed_at": (
                        created_at if head_state == "confirmed" else None
                    ),
                },
            )
