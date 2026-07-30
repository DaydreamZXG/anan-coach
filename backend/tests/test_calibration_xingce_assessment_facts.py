from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.auth.repository import token_hash_for_tests
from app.domains.calibration.repository import CalibrationRepository
from tests.factories import (
    FrozenKnowledgeNodeSeed,
    FrozenQuestionSeed,
    seed_canonical_frozen_release,
)
from tests.task_editing_helpers import claim_task_edit_headers
from tests.test_planning_routes import (
    _client,
    _seed_active_route_day_preference,
    _seed_learner_and_published_essay_content,
)
from tests.test_xingce_learning_routes import (
    _xingce_task,
)


def test_calibration_aggregates_assessments_without_legacy_practice_answers(
    db_session_factory: sessionmaker[Session],
) -> None:
    first_token = "calibration-assessment-owner"
    first_user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=first_token,
        include_objective_questions=False,
    )
    _seed_calibration_frozen_release(db_session_factory)
    _submit_assessments(
        db_session_factory,
        token=first_token,
        selected_options=["A", "A", "C"],
    )

    second_token = "calibration-assessment-other"
    second_user_id = _seed_second_user(
        db_session_factory,
        access_token=second_token,
    )
    _submit_assessments(
        db_session_factory,
        token=second_token,
        selected_options=["D", "D", "D"],
    )

    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from learner.practice_answers")).scalar_one() == 0
        )
        first = CalibrationRepository(session).get_raw(user_id=first_user_id)
        second = CalibrationRepository(session).get_raw(user_id=second_user_id)

    assert first.practice_answers == 3
    assert first.practice_correct == 2
    assert first.practice_modules == [
        {
            "module_name": "资料分析",
            "sample_size": 2,
            "correct_count": 1,
        },
        {
            "module_name": "判断推理",
            "sample_size": 1,
            "correct_count": 1,
        },
    ]
    assert first.knowledge_points == [
        {
            "module_name": "资料分析",
            "tag_value": "基期量计算",
            "sample_size": 2,
            "correct_count": 1,
        },
        {
            "module_name": "判断推理",
            "tag_value": "图形推理",
            "sample_size": 1,
            "correct_count": 1,
        },
    ]

    assert second.practice_answers == 3
    assert second.practice_correct == 0
    assert sum(item["sample_size"] for item in second.practice_modules) == 3
    assert sum(item["correct_count"] for item in second.practice_modules) == 0
    assert first.practice_correct == 2


def _seed_calibration_frozen_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    # Keep this aggregation fixture to one complete bundle. Question delivery
    # intentionally uses a per-user stable cursor when a larger pool is available.
    node_by_index = (
        ("calibration-data-node", "资料分析", "基期量计算"),
        ("calibration-data-node", "资料分析", "基期量计算"),
        ("calibration-reasoning-node", "判断推理", "图形推理"),
    )
    with db_session_factory.begin() as session:
        seed_canonical_frozen_release(
            session,
            release_key="calibration-assessment",
            questions=tuple(
                FrozenQuestionSeed(
                    question_id=f"xingce-route-question-{index}",
                    stem=f"行测路由验收题 {index}",
                    answer=("A", "B", "C", "D")[(index - 1) % 4],
                    node_id=node_id,
                    module_name=module_name,
                    occurrence_count=100 - index,
                    explanation=f"解析 {index}",
                )
                for index, (node_id, module_name, _tag_value) in enumerate(
                    node_by_index,
                    start=1,
                )
            ),
            nodes=tuple(
                FrozenKnowledgeNodeSeed(
                    node_id=node_id,
                    module_name=module_name,
                    tag_value=tag_value,
                )
                for node_id, module_name, tag_value in dict.fromkeys(node_by_index)
            ),
        )


def _submit_assessments(
    db_session_factory: sessionmaker[Session],
    *,
    token: str,
    selected_options: list[str],
) -> None:
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _xingce_task(client, auth)
    started_response = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": f"start-{token}"},
        json={"planTaskId": task["id"]},
    )
    assert started_response.status_code == 200, started_response.text
    started = started_response.json()["data"]
    questions = started["bundle"]["questions"]
    assert len(questions) == len(selected_options)
    edit_headers = claim_task_edit_headers(
        client,
        auth,
        resource_type="xingce_task_session",
        resource_id=started["sessionId"],
        revision=started["revision"],
        device_suffix=token[-8:],
    )
    checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={
            **auth,
            **edit_headers,
            "Idempotency-Key": f"checkpoint-{token}",
        },
        json={
            "expectedRevision": started["revision"],
            "currentQuestionId": questions[-1]["questionId"],
            "answers": [
                {
                    "questionId": question["questionId"],
                    "selectedOptionIds": [selected],
                    "eliminatedOptionIds": [],
                    "confidence": "high",
                    "marked": False,
                    "skipped": False,
                    "elapsedSeconds": 30 + index,
                    "draftText": "",
                    "materialPosition": None,
                }
                for index, (question, selected) in enumerate(
                    zip(questions, selected_options, strict=True)
                )
            ],
            "totalElapsedSeconds": 93,
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    submitted = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={
            **auth,
            **edit_headers,
            "Idempotency-Key": f"submit-{token}",
        },
        json={
            "expectedRevision": checkpoint.json()["data"]["revision"],
            "totalElapsedSeconds": 93,
        },
    )
    assert submitted.status_code == 200, submitted.text


def _seed_second_user(
    db_session_factory: sessionmaker[Session],
    *,
    access_token: str,
) -> str:
    user_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (cast(:user_id as uuid), '另一位校准学习者')
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid),
                  :token_hash, 'calibration-assessment-device', :expires_at
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
                  user_id, daily_minutes, study_mode,
                  onboarding_complete, profile_version
                ) values (
                  cast(:user_id as uuid), 45, 'parttime', true, 1
                )
                """
            ),
            {"user_id": user_id},
        )
        _seed_active_route_day_preference(session, user_id=user_id)
    return user_id
