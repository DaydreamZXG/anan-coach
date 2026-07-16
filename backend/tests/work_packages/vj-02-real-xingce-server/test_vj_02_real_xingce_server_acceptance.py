from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.auth.repository import token_hash_for_tests
from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)
from tests.test_xingce_cause_agent_workflow import SequenceProvider, _run_one


def test_vj_02_real_xingce_server_as_01(
    db_session_factory: sessionmaker[Session],
) -> None:
    """An owner can resume one safe frozen bundle; idempotency is tenant scoped."""

    first_token = "xingce-as01-first-token"
    second_token = "xingce-as01-second-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=first_token,
    )
    _seed_second_learner(db_session_factory, access_token=second_token)
    _seed_objective_questions(db_session_factory)
    client = _client(db_session_factory)
    first_auth = {"Authorization": f"Bearer {first_token}"}
    second_auth = {"Authorization": f"Bearer {second_token}"}
    first_task = _xingce_task(client, first_auth)
    second_task = _xingce_task(client, second_auth)
    shared_key = "same-key-across-accounts"

    first = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**first_auth, "Idempotency-Key": shared_key},
        json={"planTaskId": first_task["id"]},
    )
    second = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**second_auth, "Idempotency-Key": shared_key},
        json={"planTaskId": second_task["id"]},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_session = first.json()["data"]
    second_session = second.json()["data"]
    assert first_session["sessionId"] != second_session["sessionId"]
    assert first_session["state"] == "answering"
    assert first_session["planTaskId"] == first_task["id"]
    assert first_session["bundle"]["bundleType"] == "shared_material"
    assert len(first_session["bundle"]["questions"]) == 3
    assert (
        first_session["bundle"]["materials"][0]["text"]
        == "共用材料：基层治理需要精准施策。"
    )
    assert not _contains_forbidden_safe_key(first_session["bundle"])

    replay = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**first_auth, "Idempotency-Key": shared_key},
        json={"planTaskId": first_task["id"]},
    )
    resumed = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**first_auth, "Idempotency-Key": "resume-same-work-item"},
        json={"planTaskId": first_task["id"]},
    )
    foreign_read = client.get(
        f"/api/v2/xingce/task-sessions/{first_session['sessionId']}",
        headers=second_auth,
    )
    first_question = first_session["bundle"]["questions"][0]
    foreign_checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{first_session['sessionId']}/checkpoint",
        headers={**second_auth, "Idempotency-Key": "foreign-checkpoint-as01"},
        json={
            "expectedRevision": first_session["revision"],
            "currentQuestionId": first_question["questionId"],
            "answers": [
                _answer(
                    first_question,
                    selected=["A"],
                    eliminated=[],
                    confidence="high",
                    elapsed=10,
                )
            ],
            "totalElapsedSeconds": 10,
        },
    )
    foreign_submit = client.post(
        f"/api/v2/xingce/task-sessions/{first_session['sessionId']}/submit",
        headers={**second_auth, "Idempotency-Key": "foreign-submit-as01"},
        json={
            "expectedRevision": first_session["revision"],
            "totalElapsedSeconds": 10,
        },
    )

    assert replay.status_code == 200
    assert replay.json()["data"] == first_session
    assert resumed.status_code == 200
    assert resumed.json()["data"]["sessionId"] == first_session["sessionId"]
    assert foreign_read.status_code == 404
    assert foreign_checkpoint.status_code == 404
    assert foreign_submit.status_code == 404
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from learning.xingce_commands "
                    "where command_id = :command_id"
                ),
                {"command_id": shared_key},
            ).scalar_one()
            == 2
        )
        isolated_session = (
            session.execute(
                text(
                    "select state, revision from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": first_session["sessionId"]},
            )
            .mappings()
            .one()
        )
        assert dict(isolated_session) == {"state": "active", "revision": 1}
        assert (
            session.execute(
                text(
                    "select count(*) from learning.xingce_answer_versions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": first_session["sessionId"]},
            ).scalar_one()
            == 0
        )


def test_vj_02_real_xingce_server_as_02(
    db_session_factory: sessionmaker[Session],
) -> None:
    """CAS checkpoints persist every learning signal and replay exactly once."""

    client, auth, started = _started_session(
        db_session_factory,
        token="xingce-as02-token",
    )
    questions = started["bundle"]["questions"]
    checkpoint = {
        "expectedRevision": started["revision"],
        "currentQuestionId": questions[1]["questionId"],
        "answers": [
            _answer(
                questions[0],
                selected=["A"],
                eliminated=["C", "D"],
                confidence="medium",
                elapsed=41,
                marked=True,
                material_position={
                    "anchorId": "material-paragraph-1",
                    "scrollTop": 188,
                    "zoom": 1.25,
                },
            ),
            _answer(
                questions[1],
                selected=[],
                eliminated=["A", "D"],
                confidence=None,
                elapsed=62,
                skipped=True,
            ),
        ],
        "totalElapsedSeconds": 103,
    }
    key = "checkpoint-signals-as02"

    saved = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": key},
        json=checkpoint,
    )
    replay = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": key},
        json=checkpoint,
    )
    stale = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": "checkpoint-stale-as02"},
        json={**checkpoint, "totalElapsedSeconds": 104},
    )
    changed_payload_same_key = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": key},
        json={**checkpoint, "totalElapsedSeconds": 105},
    )

    assert saved.status_code == 200, saved.text
    assert replay.status_code == 200
    assert replay.json()["data"] == saved.json()["data"]
    assert stale.status_code == 409
    assert changed_payload_same_key.status_code == 409
    projection = saved.json()["data"]
    assert projection["revision"] == started["revision"] + 1
    assert projection["currentQuestionId"] == questions[1]["questionId"]
    assert projection["totalElapsedSeconds"] == 103
    assert projection["answers"][0]["selectedOptionIds"] == ["A"]
    assert projection["answers"][0]["eliminatedOptionIds"] == ["C", "D"]
    assert projection["answers"][0]["confidence"] == "medium"
    assert projection["answers"][0]["marked"] is True
    assert projection["answers"][0]["materialPosition"] == {
        "anchorId": "material-paragraph-1",
        "scrollTop": 188,
        "zoom": 1.25,
    }
    assert projection["answers"][1]["skipped"] is True
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from learning.xingce_answer_versions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": started["sessionId"]},
            ).scalar_one()
            == 2
        )


def test_vj_02_real_xingce_server_as_03(
    db_session_factory: sessionmaker[Session],
) -> None:
    """Submit scores frozen answers and atomically creates review and plan evidence."""

    client, auth, started = _started_session(
        db_session_factory,
        token="xingce-as03-token",
    )
    checkpointed = _checkpoint_every_question(
        client,
        auth,
        started,
        choices=["A", "B", "C"],
        confidences=["high", "high", "high"],
        key="checkpoint-all-as03",
    )
    submit_payload = {
        "expectedRevision": checkpointed["revision"],
        "totalElapsedSeconds": 176,
    }
    key = "submit-attempt-as03"

    submitted = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": key},
        json=submit_payload,
    )
    replay = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": key},
        json=submit_payload,
    )

    assert submitted.status_code == 200, submitted.text
    assert replay.status_code == 200
    result = submitted.json()["data"]
    assert replay.json()["data"] == result
    assert result["state"] == "reflection_required"
    assert result["result"]["resultCode"] == "correct"
    assert result["result"]["correctCount"] == 3
    assert result["result"]["totalCount"] == 3
    assert [item["correctOptionIds"] for item in result["result"]["items"]] == [
        ["A"],
        ["B"],
        ["C"],
    ]
    assert result["review"]["state"] == "reflection_required"
    assert result["review"]["aiCandidate"] == {
        "state": "processing",
        "candidateVersionId": None,
        "reason": None,
        "causeCode": None,
        "summary": None,
        "improvement": None,
        "question": None,
        "rationale": None,
        "explanation": None,
        "missingEvidence": [],
        "evidenceRefs": [],
        "limitations": [],
        "quotaState": "not_metered",
        "entitlementPolicyVersion": "included-with-canonical-practice@v1",
    }
    with db_session_factory() as session:
        session_owner = session.execute(
            text(
                "select user_id::text from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": started["sessionId"]},
        ).scalar_one()
        attempt_rows = list(
            session.execute(
                text(
                    """
                    select * from learning.xingce_attempt_records
                    where session_id = cast(:session_id as uuid)
                    order by question_id
                    """
                ),
                {"session_id": started["sessionId"]},
            ).mappings()
        )
        assessment_rows = list(
            session.execute(
                text(
                    """
                    select * from learning.xingce_assessments
                    where session_id = cast(:session_id as uuid)
                    order by question_id
                    """
                ),
                {"session_id": started["sessionId"]},
            ).mappings()
        )
        event_rows = list(
            session.execute(
                text(
                    """
                    select event_id, deduplication_key, aggregate_type,
                           aggregate_id, event_type, payload
                    from platform.outbox_events
                    where event_type in (
                      'attempt-recorded@v1',
                      'xingce-assessment-recorded@v1'
                    )
                    order by event_type, aggregate_id
                    """
                )
            ).mappings()
        )
        counts = (
            session.execute(
                text(
                    """
                select
                  (select count(*) from learning.xingce_assessments
                    where session_id = cast(:session_id as uuid)) as assessments,
                  (select count(*) from learning.xingce_attempt_review_versions
                    where session_id = cast(:session_id as uuid)) as reviews,
                  (select count(*) from learning.xingce_plan_evidence_versions
                    where session_id = cast(:session_id as uuid)) as evidence
                """
                ),
                {"session_id": started["sessionId"]},
            )
            .mappings()
            .one()
        )
        assert dict(counts) == {"assessments": 3, "reviews": 1, "evidence": 1}
        assert len(attempt_rows) == 3
        assert len(assessment_rows) == 3
        assert len(event_rows) == 6
        attempt_events = {
            str(row["aggregate_id"]): row
            for row in event_rows
            if row["event_type"] == "attempt-recorded@v1"
        }
        assessment_events = {
            str(row["aggregate_id"]): row
            for row in event_rows
            if row["event_type"] == "xingce-assessment-recorded@v1"
        }
        review_id = result["review"]["reviewId"]
        for attempt, assessment in zip(
            attempt_rows,
            assessment_rows,
            strict=True,
        ):
            attempt_id = str(attempt["attempt_record_id"])
            assessment_id = str(assessment["assessment_id"])
            answer_version_id = str(attempt["answer_version_id"])
            assert str(assessment["attempt_record_id"]) == attempt_id
            assert str(assessment["answer_version_id"]) == answer_version_id
            attempt_envelope = _assert_event_envelope(
                attempt_events[attempt_id],
                event_type="attempt-recorded@v1",
                producer="task_runtime",
                aggregate_type="attempt_record",
                aggregate_id=attempt_id,
                command_id=key,
                user_id=session_owner,
            )
            assert attempt_envelope["payload"] == {
                "taskSessionRef": (
                    f"task-session:{started['sessionId']}:{attempt['session_revision']}"
                ),
                "targetTrack": "xingce",
                "taskItemRef": str(attempt["question_id"]),
                "itemAttemptId": str(attempt["item_attempt_id"]),
                "attemptRecordRef": f"attempt-record:{attempt_id}",
                "answerRevisionRef": f"answer-version:{answer_version_id}",
                "responseArtifactRef": f"answer-version:{answer_version_id}",
                "contentVersionRef": str(attempt["content_version_id"]),
                "materialPackageRef": "",
                "scoringPolicyVersionRef": str(attempt["scoring_policy_version"]),
                "timingEvidenceRef": str(attempt["timing_evidence_ref"]),
                "submissionHash": str(attempt["submission_hash"]),
                "sourceLineageRefs": list(attempt["source_lineage_refs"]),
            }
            assessment_envelope = _assert_event_envelope(
                assessment_events[assessment_id],
                event_type="xingce-assessment-recorded@v1",
                producer="xingce_learning",
                aggregate_type="xingce_assessment",
                aggregate_id=assessment_id,
                command_id=key,
                user_id=session_owner,
            )
            assert assessment_envelope["payload"] == {
                "writerRouting": "direct_attempt",
                "attemptRecordRef": f"attempt-record:{attempt_id}",
                "answerRevisionRef": f"answer-version:{answer_version_id}",
                "assessmentRef": f"xingce-assessment:{assessment_id}",
                "assessmentKeyHash": str(assessment["assessment_key_hash"]),
                "reviewRef": f"xingce-review:{review_id}",
                "contentVersionRef": str(assessment["content_version_id"]),
                "scoringPolicyVersionRef": str(
                    assessment["scoring_policy_version"]
                ),
                "resultCode": str(assessment["result_code"]),
                "acceptanceId": "",
            }
            assert assessment["assessment_key_hash"] == sha256(
                "\n".join(
                    (
                        attempt_envelope["subjectRef"],
                        f"attempt-record:{attempt_id}",
                        str(assessment["scoring_policy_version"]),
                    )
                ).encode("utf-8")
            ).hexdigest()
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_plan_evidence_versions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": started["sessionId"]},
            ).scalar_one()
            == "pending_reflection"
        )


def test_vj_02_real_xingce_server_as_04(
    db_session_factory: sessionmaker[Session],
) -> None:
    """User voice is versioned before the audited AI candidate and learner choice."""

    client, auth, started = _started_session(
        db_session_factory,
        token="xingce-as04-token",
    )
    checkpointed = _checkpoint_every_question(
        client,
        auth,
        started,
        choices=["D", "B", "C"],
        confidences=["high", "medium", "high"],
        key="checkpoint-one-wrong-as04",
    )
    submitted = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={**auth, "Idempotency-Key": "submit-one-wrong-as04"},
        json={
            "expectedRevision": checkpointed["revision"],
            "totalElapsedSeconds": 182,
        },
    ).json()["data"]
    review_id = submitted["review"]["reviewId"]
    reflection_payload = {
        "expectedRevision": submitted["review"]["revision"],
        "rawText": "我把题干里的‘最主要’看漏了，先凭印象选了 D。",
        "tags": ["题干审读", "凭印象作答"],
        "strategyNote": "以后先圈限定词，再排除选项。",
        "doubtNote": "A 和 D 的边界还需要再辨析。",
    }

    reflected_response = client.put(
        f"/api/v2/xingce/attempt-reviews/{review_id}/reflection",
        headers={**auth, "Idempotency-Key": "reflection-as04"},
        json=reflection_payload,
    )
    assert reflected_response.status_code == 200, reflected_response.text
    reflected = reflected_response.json()["data"]
    assert reflected["state"] == "cause_candidate_processing"
    assert reflected["review"]["state"] == "cause_candidate_pending"
    assert reflected["review"]["reflection"]["rawText"] == reflection_payload["rawText"]
    assert reflected["review"]["reflection"]["tags"] == reflection_payload["tags"]
    assert reflected["review"]["causeDecision"] is None
    assert reflected["review"]["aiCandidate"]["state"] == "processing"
    assert reflected["review"]["aiCandidate"]["quotaState"] == "not_metered"

    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    ready_response = client.get(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}",
        headers=auth,
    )
    assert ready_response.status_code == 200, ready_response.text
    ready = ready_response.json()["data"]
    assert ready["state"] == "cause_decision_required"
    assert ready["review"]["state"] == "cause_decision_required"
    assert ready["review"]["aiCandidate"]["state"] == "candidate_ready"
    assert ready["review"]["aiCandidate"]["candidateVersionId"]
    assert ready["review"]["aiCandidate"]["quotaState"] == "not_metered"

    decided_response = client.put(
        f"/api/v2/xingce/attempt-reviews/{review_id}/cause-decision",
        headers={**auth, "Idempotency-Key": "cause-reject-as04"},
        json={
            "expectedRevision": ready["review"]["revision"],
            "candidateVersionId": ready["review"]["aiCandidate"][
                "candidateVersionId"
            ],
            "decision": "reject",
        },
    )
    assert decided_response.status_code == 200, decided_response.text
    decided = decided_response.json()["data"]
    assert decided["state"] == "completed"
    assert decided["review"]["state"] == "immediate_verification_due"
    assert decided["review"]["causeDecision"]["decision"] == "reject"
    assert decided["review"]["causeDecision"]["confirmedCauseVersionId"] is None
    assert decided["completion"]["receiptId"]
    assert decided["completion"]["plan"]["planVersionId"]

    fetched = client.get(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}",
        headers=auth,
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["completion"] == decided["completion"]
    with db_session_factory() as session:
        assert (
            session.execute(
                text(
                    "select count(*) from learning.xingce_cause_candidate_versions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": started["sessionId"]},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select count(*) from learning.xingce_reflection_versions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": started["sessionId"]},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select count(*) from learning.xingce_cause_decision_versions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": started["sessionId"]},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select count(*) from learning.xingce_plan_evidence_versions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": started["sessionId"]},
            ).scalar_one()
            == 2
        )
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_plan_evidence_versions "
                    "where session_id = cast(:session_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"session_id": started["sessionId"]},
            ).scalar_one()
            == "settled"
        )
        assert (
            session.execute(
                text("select count(*) from ai_audit.interaction_logs")
            ).scalar_one()
            == 0
        )


def _started_session(
    factory: sessionmaker[Session],
    *,
    token: str,
) -> tuple[TestClient, dict[str, str], dict[str, Any]]:
    _seed_learner_and_published_essay_content(factory, access_token=token)
    _seed_objective_questions(factory)
    client = _client(factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _xingce_task(client, auth)
    response = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": f"start-{token}"},
        json={"planTaskId": task["id"]},
    )
    assert response.status_code == 200, response.text
    return client, auth, response.json()["data"]


def _xingce_task(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/v2/plans/today", headers=auth)
    assert response.status_code == 200, response.text
    return next(
        task for task in response.json()["data"]["tasks"] if task["track"] == "xingce"
    )


def _checkpoint_every_question(
    client: TestClient,
    auth: dict[str, str],
    started: dict[str, Any],
    *,
    choices: list[str],
    confidences: list[str],
    key: str,
) -> dict[str, Any]:
    questions = started["bundle"]["questions"]
    response = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={**auth, "Idempotency-Key": key},
        json={
            "expectedRevision": started["revision"],
            "currentQuestionId": questions[-1]["questionId"],
            "answers": [
                _answer(
                    question,
                    selected=[choice],
                    eliminated=[],
                    confidence=confidence,
                    elapsed=45 + index,
                )
                for index, (question, choice, confidence) in enumerate(
                    zip(questions, choices, confidences, strict=True)
                )
            ],
            "totalElapsedSeconds": 150,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _answer(
    question: dict[str, Any],
    *,
    selected: list[str],
    eliminated: list[str],
    confidence: str | None,
    elapsed: int,
    marked: bool = False,
    skipped: bool = False,
    material_position: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "questionId": question["questionId"],
        "selectedOptionIds": selected,
        "eliminatedOptionIds": eliminated,
        "confidence": confidence,
        "marked": marked,
        "skipped": skipped,
        "elapsedSeconds": elapsed,
        "draftText": "",
        "materialPosition": material_position,
    }


def _seed_objective_questions(factory: sessionmaker[Session]) -> None:
    with factory.begin() as session:
        for index, (answer, material) in enumerate(
            (
                ("A", "共用材料：基层治理需要精准施策。"),
                ("B", None),
                ("C", None),
            ),
            start=1,
        ):
            question_id = f"real-xingce-question-{index}"
            session.execute(
                text(
                    """
                    insert into product.questions (
                      question_id, domain, status, module_name, question_type,
                      stem, answer, material_text, shared_context_required,
                      visual_required, occurrence_count, latest_year
                    ) values (
                      :question_id, 'objective', 'canonical', '言语理解', 'single_choice',
                      :stem, :answer, :material, :shared_context_required,
                      false, :occurrence_count, 2026
                    )
                    """
                ),
                {
                    "question_id": question_id,
                    "stem": f"真实行测题干 {index}",
                    "answer": answer,
                    "material": material,
                    "shared_context_required": material is not None,
                    "occurrence_count": 100 - index,
                },
            )
            for option_index, label in enumerate(("A", "B", "C", "D"), start=1):
                session.execute(
                    text(
                        """
                        insert into product.question_options (
                          question_id, option_index, label, option_text
                        ) values (:question_id, :option_index, :label, :option_text)
                        """
                    ),
                    {
                        "question_id": question_id,
                        "option_index": option_index,
                        "label": label,
                        "option_text": f"选项 {label}",
                    },
                )
            session.execute(
                text(
                    """
                    insert into product.question_explanations (
                      question_id, preferred_analysis, analysis_source, analysis_status
                    ) values (
                      :question_id, :analysis, 'own_bank', 'verified'
                    )
                    """
                ),
                {
                    "question_id": question_id,
                    "analysis": f"真实题解析 {index}",
                },
            )


def _seed_second_learner(
    factory: sessionmaker[Session],
    *,
    access_token: str,
) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '第二位行测学习者')"
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid), :token_hash,
                  'wechat-xingce-tenant-test', :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "token_hash": token_hash_for_tests(access_token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id, daily_minutes, study_mode, onboarding_complete,
                  profile_version
                ) values (cast(:user_id as uuid), 45, 'parttime', true, 1)
                """
            ),
            {"user_id": user_id},
        )
    return user_id


def _contains_forbidden_safe_key(value: object) -> bool:
    forbidden = {"answer", "correctanswer", "correctoptionids", "explanation"}
    if isinstance(value, dict):
        return any(
            str(key).casefold() in forbidden or _contains_forbidden_safe_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_safe_key(item) for item in value)
    return False


def _assert_event_envelope(
    row: Any,
    *,
    event_type: str,
    producer: str,
    aggregate_type: str,
    aggregate_id: str,
    command_id: str,
    user_id: str,
) -> dict[str, Any]:
    envelope = dict(row["payload"])
    assert set(envelope) == {
        "eventId",
        "eventType",
        "aggregateId",
        "aggregateRevision",
        "occurredAt",
        "producer",
        "subjectRef",
        "trackId",
        "correlationId",
        "causationId",
        "payload",
        "schemaVersion",
    }
    assert row["deduplication_key"] == f"{event_type}:{aggregate_id}:1"
    assert row["aggregate_type"] == aggregate_type
    assert str(row["aggregate_id"]) == aggregate_id
    assert row["event_type"] == event_type
    assert envelope["eventId"] == str(row["event_id"])
    assert envelope["eventType"] == event_type
    assert envelope["aggregateId"] == aggregate_id
    assert envelope["aggregateRevision"] == 1
    assert envelope["producer"] == producer
    assert envelope["trackId"] == "xingce"
    assert envelope["correlationId"] == command_id
    assert envelope["causationId"] == command_id
    assert envelope["schemaVersion"] == 1
    assert envelope["subjectRef"] != user_id
    assert len(envelope["subjectRef"]) == 64
    assert datetime.fromisoformat(envelope["occurredAt"]).tzinfo is not None
    return envelope
