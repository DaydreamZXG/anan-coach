from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.auth.deps import get_auth_repository
from app.domains.auth.repository import AuthenticatedUser
from app.domains.memory.deps import get_memory_service
from app.domains.memory.repository import (
    LearnerMemoryRecord,
    LearnerMemorySummaryRecord,
    LearnerMemoryRepository,
    LearnerProfileRecord,
)
from app.domains.memory.schemas import LearnerProfileUpdate
from app.domains.taskstream.service import TODAY_ESSAY_TASK_ID, TODAY_XINGCE_TASK_ID
from app.main import create_app


class FakeAuthRepository:
    def get_user_by_access_token(self, access_token: str) -> AuthenticatedUser | None:
        if access_token not in {"user-1", "user-2"}:
            return None
        return AuthenticatedUser(
            user_id=access_token,
            learner_key=access_token,
            display_name=None,
            email=None,
        )


class InMemoryLearnerMemoryService:
    def __init__(self) -> None:
        self.profiles: dict[str, LearnerProfileRecord] = {}

    def get_memory(self, *, user_id: str) -> LearnerMemoryRecord:
        profile = self.profiles.get(
            user_id,
            LearnerProfileRecord(
                target_exam=None,
                target_region=None,
                position_group=None,
                daily_minutes=None,
                onboarding_complete=False,
            ),
        )
        return LearnerMemoryRecord(
            profile=profile,
            summary=LearnerMemorySummaryRecord(
                practice_answers=0,
                wrong_answers=0,
                essay_attempts=0,
            ),
        )

    def update_profile(
        self,
        *,
        user_id: str,
        update: LearnerProfileUpdate,
    ) -> LearnerProfileRecord:
        profile = LearnerProfileRecord(
            target_exam=update.target_exam,
            target_region=update.target_region,
            position_group=update.position_group,
            daily_minutes=update.daily_minutes,
            onboarding_complete=True,
        )
        self.profiles[user_id] = profile
        return profile


def make_client() -> TestClient:
    app = create_app()
    memory = InMemoryLearnerMemoryService()
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[get_memory_service] = lambda: memory
    return TestClient(app)


def test_learner_profile_memory_is_private_per_authenticated_user() -> None:
    client = make_client()

    update = client.put(
        "/api/v1/learner/profile",
        headers={"Authorization": "Bearer user-1"},
        json={
            "targetExam": "国考",
            "targetRegion": "全国",
            "positionGroup": "中央机关综合管理岗",
            "dailyMinutes": 42,
        },
    )

    assert update.status_code == 410
    assert update.json()["code"] == "learner_profile_v1_retired"

    user_1 = client.get(
        "/api/v1/learner/memory",
        headers={"Authorization": "Bearer user-1"},
    )
    user_2 = client.get(
        "/api/v1/learner/memory",
        headers={"Authorization": "Bearer user-2"},
    )

    assert user_1.json()["profile"]["targetExam"] is None
    assert user_2.json()["profile"]["targetExam"] is None
    assert user_2.json()["profile"]["onboardingComplete"] is False


def test_learner_memory_rejects_unauthenticated_access() -> None:
    response = make_client().get("/api/v1/learner/memory")

    assert response.status_code == 401


def test_completed_task_ids_are_derived_from_learner_tables(
    db_session: Session,
) -> None:
    user_id = str(uuid4())
    db_session.execute(
        text(
            """
            insert into learner.users (user_id, display_name)
            values (cast(:user_id as uuid), '学习者')
            """
        ),
        {"user_id": user_id},
    )
    db_session.execute(
        text(
            """
            insert into learner.practice_answers (
              answer_id,
              user_id,
              task_id,
              question_id,
              selected_answer,
              correct_answer,
              correct,
              answered_at
            ) values
              (
                gen_random_uuid(),
                cast(:user_id as uuid),
                :today_xingce,
                'cq_today',
                'B',
                'B',
                true,
                now()
              ),
              (
                gen_random_uuid(),
                cast(:user_id as uuid),
                'yesterday-task',
                'cq_yesterday',
                'A',
                'B',
                false,
                now() - interval '1 day'
              )
            """
        ),
        {"user_id": user_id, "today_xingce": TODAY_XINGCE_TASK_ID},
    )
    db_session.execute(
        text(
            """
            insert into learner.essay_attempts (
              attempt_id,
              learner_key,
              task_id,
              original_answer,
              review_payload,
              created_at,
              updated_at
            ) values (
              'er_today',
              :learner_key,
              :today_essay,
              '平台分散。',
              '{"dimensions":[],"missingPoints":[]}'::jsonb,
              now(),
              now()
            )
            """
        ),
        {"learner_key": user_id, "today_essay": TODAY_ESSAY_TASK_ID},
    )
    db_session.commit()

    completed = LearnerMemoryRepository(db_session).list_completed_task_ids_for_date(
        user_id=user_id,
        completed_on=datetime.now(UTC).date(),
    )

    assert set(completed) == {TODAY_XINGCE_TASK_ID, TODAY_ESSAY_TASK_ID}


def test_record_practice_answer_persists_to_learner_memory(
    db_session: Session,
) -> None:
    user_id = str(uuid4())
    db_session.execute(
        text(
            """
            insert into learner.users (user_id, display_name)
            values (cast(:user_id as uuid), '学习者')
            """
        ),
        {"user_id": user_id},
    )
    db_session.commit()

    LearnerMemoryRepository(db_session).record_practice_answer(
        user_id=user_id,
        task_id=TODAY_XINGCE_TASK_ID,
        question_id="cq_real",
        selected_answer="A",
        correct_answer="B",
        correct=False,
    )

    row = db_session.execute(
        text(
            """
            select task_id, question_id, selected_answer, correct_answer, correct
            from learner.practice_answers
            where user_id = cast(:user_id as uuid)
            """
        ),
        {"user_id": user_id},
    ).mappings().one()

    assert row["task_id"] == TODAY_XINGCE_TASK_ID
    assert row["question_id"] == "cq_real"
    assert row["selected_answer"] == "A"
    assert row["correct_answer"] == "B"
    assert row["correct"] is False
