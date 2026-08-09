from __future__ import annotations

from datetime import datetime
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.companion.repository import CompanionRepository
from app.domains.planning.insights_service import PlanningInsightsService
from app.domains.planning.schemas import DailyCapacityCommand
from app.domains.planning.service import PlanningService
from tests.test_planning_routes import (
    NOW,
    _seed_learner_and_published_essay_content,
    _seed_shared_objective_question_bank,
)


def test_daily_settlement_retains_terminal_tasks_removed_by_replanning(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    with db_session_factory.begin() as session:
        _seed_shared_objective_question_bank(session)
    planning = PlanningService(now=lambda: NOW)

    with db_session_factory.begin() as session:
        original = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    xingce = next(task for task in original.tasks if task.track == "xingce")
    essay = next(task for task in original.tasks if task.track == "essay")

    with db_session_factory.begin() as session:
        _insert_terminal_execution(
            session,
            user_id=user_id,
            task_id=str(xingce.id),
            status="complete",
            elapsed_minutes=19,
            recorded_at=NOW,
        )
        _insert_terminal_execution(
            session,
            user_id=user_id,
            task_id=str(essay.id),
            status="abandoned",
            elapsed_minutes=5,
            recorded_at=NOW,
        )

    with db_session_factory.begin() as session:
        replanned = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    assert replanned.tasks == []

    with db_session_factory.begin() as session:
        settlement = PlanningInsightsService(planning=planning).daily_settlement(
            session,
            user_id=user_id,
        )

    assert settlement.plan_version_id == replanned.plan_version_id
    assert settlement.status == "partial"
    assert settlement.completed_task_count == 1
    assert settlement.abandoned_task_count == 1
    assert settlement.open_task_count == 0
    assert settlement.scheduled_minutes == xingce.estimated_minutes + essay.estimated_minutes
    assert settlement.elapsed_minutes == 24
    assert {item.task_id for item in settlement.tasks} == {xingce.id, essay.id}
    assert {item.status for item in settlement.tasks} == {"complete", "abandoned"}
    assert next(item for item in settlement.tasks if item.task_id == xingce.id).evidence_refs
    assert (
        next(item for item in settlement.tasks if item.task_id == essay.id).risk_code
        == "task_abandoned"
    )


def test_daily_settlement_combines_completed_fact_with_current_open_task(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    with db_session_factory.begin() as session:
        _seed_shared_objective_question_bank(session)
    planning = PlanningService(now=lambda: NOW)

    with db_session_factory.begin() as session:
        original = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    xingce = next(task for task in original.tasks if task.track == "xingce")

    with db_session_factory.begin() as session:
        _insert_terminal_execution(
            session,
            user_id=user_id,
            task_id=str(xingce.id),
            status="complete",
            elapsed_minutes=19,
            recorded_at=NOW,
        )
    with db_session_factory.begin() as session:
        replanned = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    open_task = next(task for task in replanned.tasks if task.track == "essay")

    with db_session_factory.begin() as session:
        settlement = PlanningInsightsService(planning=planning).daily_settlement(
            session,
            user_id=user_id,
        )

    assert settlement.status == "partial"
    assert settlement.completed_task_count == 1
    assert settlement.abandoned_task_count == 0
    assert settlement.open_task_count == 1
    assert settlement.scheduled_minutes == xingce.estimated_minutes + open_task.estimated_minutes
    assert settlement.elapsed_minutes == 19
    assert [(item.task_id, item.status) for item in settlement.tasks] == [
        (xingce.id, "complete"),
        (open_task.id, "ready"),
    ]


def test_completed_fulltime_task_keeps_replanned_settlement_within_daily_capacity(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    with db_session_factory.begin() as session:
        _seed_shared_objective_question_bank(session)
    planning = PlanningService(now=lambda: NOW)

    with db_session_factory.begin() as session:
        initial = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
        fulltime = planning.set_today_capacity(
            session,
            user_id=user_id,
            command_id="daily-settlement-fulltime-capacity",
            command=DailyCapacityCommand(
                basedOnCapacityVersionId=str(initial.capacity.capacity_version_id),
                availableMinutes=480,
                energy=3,
                stress=3,
                confidenceLevel=3,
            ),
            now=NOW,
        ).plan
    baseline = next(task for task in fulltime.tasks if task.stage == "objective_baseline")

    with db_session_factory.begin() as session:
        _insert_terminal_execution(
            session,
            user_id=user_id,
            task_id=str(baseline.id),
            status="complete",
            elapsed_minutes=10,
            recorded_at=NOW,
        )
    with db_session_factory.begin() as session:
        replanned = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
        settlement = PlanningInsightsService(planning=planning).daily_settlement(
            session,
            user_id=user_id,
        )
        session.execute(
            text(
                """
                update learning.daily_plan_versions
                   set valid_until = statement_timestamp() + interval '1 day'
                 where plan_version_id = cast(:plan_version_id as uuid)
                """
            ),
            {"plan_version_id": str(replanned.plan_version_id)},
        )
        companion_context = CompanionRepository(session).get_context(
            user_id=user_id,
            node_id=None,
        )

    assert sum(task.estimated_minutes for task in replanned.tasks) <= 420
    assert settlement.scheduled_minutes <= 480
    assert settlement.completed_task_count == 1
    assert any(item["status"] == "complete" for item in companion_context.today_plan_tasks)


def _insert_terminal_execution(
    session: Session,
    *,
    user_id: str,
    task_id: str,
    status: str,
    elapsed_minutes: int,
    recorded_at: datetime,
) -> None:
    task = (
        session.execute(
            text(
                """
                select *
                from learning.daily_plan_tasks
                where task_id = cast(:task_id as uuid)
                  and user_id = cast(:user_id as uuid)
                """
            ),
            {"task_id": task_id, "user_id": user_id},
        )
        .mappings()
        .one()
    )
    evidence_refs = (
        [
            {
                "owner": "objective_learning",
                "entityType": "question_attempt",
                "entityId": str(uuid4()),
            }
        ]
        if status == "complete"
        else []
    )
    session.execute(
        text(
            """
            insert into learning.task_execution_versions (
              execution_version_id, command_id, user_id, local_date,
              task_id, plan_version_id, plan_version_no, candidate_key,
              work_item_key, version_no, status, elapsed_minutes, evidence_refs,
              abandonment_reason, input_fingerprint,
              supersedes_execution_version_id,
              supersedes_execution_version_no, recorded_at
            ) values (
              cast(:execution_version_id as uuid), :command_id,
              cast(:user_id as uuid), :local_date,
              cast(:task_id as uuid), cast(:plan_version_id as uuid),
              :plan_version_no, :candidate_key, :work_item_key, 1, :status,
              :elapsed_minutes, cast(:evidence_refs as jsonb),
              :abandonment_reason, :input_fingerprint, null, null, :recorded_at
            )
            """
        ),
        {
            "execution_version_id": str(uuid4()),
            "command_id": f"daily-settlement:{user_id}:{task_id}",
            "user_id": user_id,
            "local_date": task["local_date"],
            "task_id": task_id,
            "plan_version_id": str(task["plan_version_id"]),
            "plan_version_no": int(task["plan_version_no"]),
            "candidate_key": str(task["candidate_key"]),
            "work_item_key": str(task["work_item_key"]),
            "status": status,
            "elapsed_minutes": elapsed_minutes,
            "evidence_refs": json.dumps(evidence_refs, ensure_ascii=False),
            "abandonment_reason": ("学习者主动结束该任务。" if status == "abandoned" else None),
            "input_fingerprint": "f" * 64,
            "recorded_at": recorded_at,
        },
    )
