from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.planning.service import PlanningService
from tests.test_planning_routes import NOW, _seed_learner_and_published_essay_content


def test_blank_profile_gets_real_unconfirmed_first_day_plan(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learner.profiles
                set daily_minutes = null,
                    study_mode = null,
                    onboarding_complete = false
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        )

    service = PlanningService(now=lambda: NOW)
    with db_session_factory.begin() as session:
        first = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    with db_session_factory.begin() as session:
        replay = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )

    assert first.plan_version_id == replay.plan_version_id
    assert first.capacity.available_minutes == 30
    assert first.capacity.confirmed is False
    assert first.tasks
    first_task = next(task for task in first.tasks if task.stage == "objective_baseline")
    assert first_task.mandatory is True
    assert first_task.status == "ready"
    assert f"planTaskId={first_task.id}" in first_task.start_url

    with db_session_factory() as session:
        profile = (
            session.execute(
                text(
                    """
                    select daily_minutes, study_mode, onboarding_complete
                    from learner.profiles
                    where user_id = cast(:user_id as uuid)
                    """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        preference = (
            session.execute(
                text(
                    """
                    select weekday_minutes, weekend_minutes, source_type, confirmed
                    from learning.weekly_study_preference_versions
                    where user_id = cast(:user_id as uuid)
                    order by version_no desc
                    limit 1
                    """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )

    assert profile["daily_minutes"] is None
    assert profile["study_mode"] is None
    assert profile["onboarding_complete"] is False
    assert preference["weekday_minutes"] == 30
    assert preference["weekend_minutes"] == 30
    assert preference["source_type"] == "system_default"
    assert preference["confirmed"] is False


def test_partial_schedule_keeps_the_fact_the_user_already_provided(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learner.profiles
                set daily_minutes = 75, study_mode = null
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        )
        plan = PlanningService(now=lambda: NOW).get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )

    assert plan.capacity.available_minutes == 75
    assert plan.capacity.confirmed is False
