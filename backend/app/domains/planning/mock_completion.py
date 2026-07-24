from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from math import ceil
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.mock_exam.repository import (
    MockExamRepository,
    MockExamSessionRecord,
)
from app.domains.planning.lifecycle import require_executable_plan_task
from app.domains.planning.repository import (
    PlanningContractError,
    PlanningRepository,
)
from app.domains.planning.schemas import TodayDashboard
from app.domains.planning.service import PlanningService
from app.platform.outbox import OutboxRepository


def bind_and_start_mock_plan_task(
    session: Session,
    *,
    mock_repository: MockExamRepository,
    user_id: str,
    mock_session: MockExamSessionRecord,
    plan_task_id: str,
    command_id: str,
    started_at: datetime,
) -> MockExamSessionRecord:
    planning_repository = PlanningRepository()
    if mock_session.source_plan_task_id is not None:
        _assert_existing_binding(
            session,
            planning_repository=planning_repository,
            user_id=user_id,
            mock_session=mock_session,
            requested_plan_task_id=plan_task_id,
            started_at=started_at,
        )
        return mock_session

    task = planning_repository.current_task_for_execution(
        session,
        user_id=user_id,
        task_id=plan_task_id,
    )
    _require_weekly_mock_task(task, at=started_at)
    assert task is not None
    current = planning_repository.current_task_execution(
        session,
        user_id=user_id,
        work_item_key=str(task["work_item_key"]),
    )
    if current is not None:
        raise PlanningContractError(
            "Weekly mock plan task already has an execution without its bound session"
        )

    bound = mock_repository.bind_source_plan_task(
        session_id=mock_session.session_id,
        user_id=user_id,
        plan_task_id=str(task["task_id"]),
        updated_at=started_at,
    )
    if bound is None:
        raise PlanningContractError(
            "Weekly mock session could not be bound to the selected plan task"
        )
    execution = planning_repository.insert_task_execution(
        session,
        values={
            "execution_version_id": str(uuid4()),
            "command_id": f"mock-start:{user_id}:{command_id}",
            "user_id": user_id,
            "local_date": task["local_date"],
            "task_id": str(task["task_id"]),
            "plan_version_id": str(task["plan_version_id"]),
            "plan_version_no": int(task["plan_version_no"]),
            "candidate_key": str(task["candidate_key"]),
            "work_item_key": str(task["work_item_key"]),
            "version_no": 1,
            "status": "in_progress",
            "elapsed_minutes": 0,
            "evidence_refs": json.dumps(
                [
                    {
                        "owner": "mock_exam",
                        "entityType": "mock_exam_session",
                        "entityId": mock_session.session_id,
                        "versionId": (
                            f"{mock_session.session_id}:{mock_session.revision}"
                        ),
                        "role": "start",
                    }
                ],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "abandonment_reason": None,
            "input_fingerprint": _fingerprint(
                {
                    "userId": user_id,
                    "taskId": str(task["task_id"]),
                    "sessionId": mock_session.session_id,
                    "revision": mock_session.revision,
                    "status": "in_progress",
                }
            ),
            "supersedes_execution_version_id": None,
            "supersedes_execution_version_no": None,
            "recorded_at": started_at,
        },
    )
    _append_state_event(
        session,
        user_id=user_id,
        command_id=command_id,
        task=task,
        execution=execution,
        mock_session=bound,
        from_state="",
        reason_code="weekly_mock_session_bound_to_plan",
        occurred_at=started_at,
    )
    return bound


def complete_mock_plan_task(
    session: Session,
    *,
    user_id: str,
    mock_session: MockExamSessionRecord,
    command_id: str,
    completed_at: datetime,
) -> TodayDashboard | None:
    if mock_session.source_plan_task_id is None:
        return None
    planning_repository = PlanningRepository()
    task = planning_repository.owned_task_for_execution(
        session,
        user_id=user_id,
        task_id=mock_session.source_plan_task_id,
    )
    _require_weekly_mock_task(task)
    assert task is not None
    current = planning_repository.current_task_execution(
        session,
        user_id=user_id,
        work_item_key=str(task["work_item_key"]),
    )
    if current is None or current["status"] != "in_progress":
        raise PlanningContractError(
            "Weekly mock completion is missing its planning-owned start fact"
        )
    elapsed_minutes = min(
        int(task["estimated_minutes"]),
        max(
            0,
            ceil(
                (completed_at - mock_session.started_at).total_seconds()
                / 60
            ),
        ),
    )
    execution = planning_repository.insert_task_execution(
        session,
        values={
            "execution_version_id": str(uuid4()),
            "command_id": f"mock-complete:{user_id}:{command_id}",
            "user_id": user_id,
            "local_date": task["local_date"],
            "task_id": str(task["task_id"]),
            "plan_version_id": str(task["plan_version_id"]),
            "plan_version_no": int(task["plan_version_no"]),
            "candidate_key": str(task["candidate_key"]),
            "work_item_key": str(task["work_item_key"]),
            "version_no": int(current["version_no"]) + 1,
            "status": "complete",
            "elapsed_minutes": elapsed_minutes,
            "evidence_refs": json.dumps(
                [
                    {
                        "owner": "mock_exam",
                        "entityType": "assessment_attempt",
                        "entityId": mock_session.session_id,
                        "versionId": (
                            f"{mock_session.session_id}:{mock_session.revision}"
                        ),
                        "role": "complete",
                    }
                ],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "abandonment_reason": None,
            "input_fingerprint": _fingerprint(
                {
                    "userId": user_id,
                    "taskId": str(task["task_id"]),
                    "sessionId": mock_session.session_id,
                    "revision": mock_session.revision,
                    "status": "complete",
                }
            ),
            "supersedes_execution_version_id": str(
                current["execution_version_id"]
            ),
            "supersedes_execution_version_no": int(current["version_no"]),
            "recorded_at": completed_at,
        },
    )
    _append_state_event(
        session,
        user_id=user_id,
        command_id=command_id,
        task=task,
        execution=execution,
        mock_session=mock_session,
        from_state="in_progress",
        reason_code="weekly_mock_submitted",
        occurred_at=completed_at,
    )
    return PlanningService(repository=planning_repository).get_or_create_today(
        session,
        user_id=user_id,
        now=completed_at,
    )


def _assert_existing_binding(
    session: Session,
    *,
    planning_repository: PlanningRepository,
    user_id: str,
    mock_session: MockExamSessionRecord,
    requested_plan_task_id: str,
    started_at: datetime,
) -> None:
    assert mock_session.source_plan_task_id is not None
    source_task = planning_repository.owned_task_for_execution(
        session,
        user_id=user_id,
        task_id=mock_session.source_plan_task_id,
    )
    _require_weekly_mock_task(source_task)
    assert source_task is not None
    if requested_plan_task_id != mock_session.source_plan_task_id:
        requested_task = planning_repository.current_task_for_execution(
            session,
            user_id=user_id,
            task_id=requested_plan_task_id,
        )
        _require_weekly_mock_task(requested_task, at=started_at)
        assert requested_task is not None
        if requested_task["work_item_key"] != source_task["work_item_key"]:
            raise PlanningContractError(
                "Assigned weekly mock belongs to a different plan task"
            )
    current = planning_repository.current_task_execution(
        session,
        user_id=user_id,
        work_item_key=str(source_task["work_item_key"]),
    )
    if current is None or current["status"] != "in_progress":
        raise PlanningContractError(
            "Bound weekly mock is missing its active planning execution"
        )


def _require_weekly_mock_task(
    task: RowMapping | None,
    *,
    at: datetime | None = None,
) -> None:
    if (
        task is None
        or task["track"] != "mock"
        or task["stage"] != "weekly_mock"
        or task["kind"] != "mock_exam"
    ):
        raise PlanningContractError(
            "Selected plan task is not an owned weekly mock task"
        )
    if at is not None:
        require_executable_plan_task(task, at=at)


def _append_state_event(
    session: Session,
    *,
    user_id: str,
    command_id: str,
    task: RowMapping,
    execution: RowMapping,
    mock_session: MockExamSessionRecord,
    from_state: str,
    reason_code: str,
    occurred_at: datetime,
) -> None:
    execution_id = str(execution["execution_version_id"])
    event_type = "plan-task-state-changed@v1"
    event_id = uuid4()
    payload = {
        "eventId": str(event_id),
        "eventType": event_type,
        "aggregateId": execution_id,
        "aggregateRevision": int(execution["version_no"]),
        "occurredAt": occurred_at.isoformat(),
        "producer": "planning",
        "subjectRef": _fingerprint(
            {"scope": "event-subject", "subject": user_id}
        ),
        "trackId": "mock",
        "correlationId": command_id,
        "causationId": command_id,
        "payload": {
            "fromState": from_state,
            "toState": str(execution["status"]),
            "resourceVersionRefs": [
                f"plan-task:{task['task_id']}",
                f"task-execution:{execution_id}",
                f"mock-exam-session:{mock_session.session_id}:{mock_session.revision}",
            ],
            "reasonCode": reason_code,
        },
        "schemaVersion": 1,
    }
    OutboxRepository().append(
        session,
        event_id=event_id,
        deduplication_key=(
            f"{event_type}:{execution_id}:{execution['version_no']}"
        ),
        aggregate_type="planning_task_execution",
        aggregate_id=execution_id,
        event_type=event_type,
        payload=payload,
    )


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()
