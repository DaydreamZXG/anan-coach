from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from math import ceil
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.planning.repository import PlanningContractError, PlanningRepository
from app.domains.planning.service import PlanningService
from app.domains.planning.xingce_evidence import settle_xingce_evidence
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository
from app.domains.xingce_learning.real_attempt.schemas import XingceCompletionReceipt
from app.platform.outbox import OutboxRepository


def complete_xingce_plan_task(
    session: Session,
    *,
    repository: XingceAttemptRepository,
    user_id: str,
    task_session: RowMapping,
    command_id: str,
    completed_at: datetime,
) -> XingceCompletionReceipt:
    task = _source_task_for_started_session(
        session,
        user_id=user_id,
        work_item_key=str(task_session["work_item_key"]),
        source_plan_task_id=str(task_session["source_plan_task_id"]),
        source_plan_version_id=str(task_session["source_plan_version_id"]),
    )
    if task is None:
        raise PlanningContractError("Current Xingce plan task does not exist")

    planning_repository = PlanningRepository()
    current = planning_repository.current_task_execution(
        session,
        user_id=user_id,
        work_item_key=str(task["work_item_key"]),
    )
    if current is None:
        raise PlanningContractError(
            "Xingce completion is missing its planning-owned in-progress start fact"
        )
    if current["status"] == "abandoned":
        return _link_late_evidence(
            session,
            repository=repository,
            planning_repository=planning_repository,
            user_id=user_id,
            task=task,
            task_session=task_session,
            execution=current,
            command_id=command_id,
            completed_at=completed_at,
        )
    if current["status"] != "in_progress":
        raise PlanningContractError("Terminal Xingce plan task cannot be rewritten")
    if current["recorded_at"] > task["valid_until"]:
        raise PlanningContractError(
            "Xingce in-progress evidence was not recorded within plan validity"
        )

    execution_version_id = str(uuid4())
    evidence_ref = {
        "owner": "xingce_learning",
        "entityType": "question_attempt",
        "entityId": str(task_session["session_id"]),
        "versionId": str(task_session["session_id"]),
        "role": "complete",
    }
    input_fingerprint = _fingerprint(
        {
            "userId": user_id,
            "sessionId": str(task_session["session_id"]),
            "taskId": str(task["task_id"]),
            "revision": int(task_session["revision"]),
            "status": "complete",
        }
    )
    row = planning_repository.insert_task_execution(
        session,
        values={
            "execution_version_id": execution_version_id,
            "command_id": f"xingce:{user_id}:{command_id}",
            "user_id": user_id,
            "local_date": task["local_date"],
            "task_id": str(task["task_id"]),
            "plan_version_id": str(task["plan_version_id"]),
            "plan_version_no": int(task["plan_version_no"]),
            "candidate_key": str(task["candidate_key"]),
            "work_item_key": str(task["work_item_key"]),
            "version_no": int(current["version_no"]) + 1,
            "status": "complete",
            "elapsed_minutes": min(
                1440,
                ceil(int(task_session["total_elapsed_seconds"]) / 60),
            ),
            "evidence_refs": json.dumps(
                [evidence_ref], ensure_ascii=False, sort_keys=True
            ),
            "abandonment_reason": None,
            "input_fingerprint": input_fingerprint,
            "supersedes_execution_version_id": str(current["execution_version_id"]),
            "supersedes_execution_version_no": int(current["version_no"]),
            "recorded_at": completed_at,
        },
    )
    _append_plan_state_event(
        session,
        user_id=user_id,
        command_id=command_id,
        task=task,
        previous=current,
        current=row,
        task_session=task_session,
        occurred_at=completed_at,
    )
    receipt = settle_xingce_evidence(
        session,
        repository=repository,
        command_id=command_id,
        user_id=user_id,
        task_session=task_session,
        execution_version_id=str(row["execution_version_id"]),
        created_at=completed_at,
    )
    plan = PlanningService(repository=planning_repository).get_or_create_today(
        session,
        user_id=user_id,
        now=completed_at,
    )
    return XingceCompletionReceipt(
        receiptId=str(receipt["evidence_version_id"]),
        settlementStatus="completed",
        plan=plan,
    )


def _link_late_evidence(
    session: Session,
    *,
    repository: XingceAttemptRepository,
    planning_repository: PlanningRepository,
    user_id: str,
    task: RowMapping,
    task_session: RowMapping,
    execution: RowMapping,
    command_id: str,
    completed_at: datetime,
) -> XingceCompletionReceipt:
    late_evidence_id = str(uuid4())
    session_id = str(task_session["session_id"])
    execution_id = str(execution["execution_version_id"])
    input_version_refs = [
        f"plan-task:{task['task_id']}",
        f"plan-version:{task['plan_version_id']}",
        f"task-execution:{execution_id}",
        f"task-session:{session_id}:{task_session['revision']}",
    ]
    evidence_refs = [
        f"attempt-group:{task_session['attempt_group_id']}",
        f"task-session:{session_id}:{task_session['revision']}",
    ]
    row = (
        session.execute(
            text(
                """
                insert into learning.plan_task_late_evidence_versions (
                  late_evidence_id, command_id, user_id, session_id,
                  work_item_key, source_execution_version_id, reason_code,
                  input_version_refs, evidence_refs, created_at
                ) values (
                  cast(:late_evidence_id as uuid), :command_id,
                  cast(:user_id as uuid), cast(:session_id as uuid),
                  :work_item_key, cast(:source_execution_version_id as uuid),
                  'terminal_execution_preserved', cast(:input_version_refs as jsonb),
                  cast(:evidence_refs as jsonb), :created_at
                ) returning *
                """
            ),
            {
                "late_evidence_id": late_evidence_id,
                "command_id": f"late:{user_id}:{command_id}",
                "user_id": user_id,
                "session_id": session_id,
                "work_item_key": str(task["work_item_key"]),
                "source_execution_version_id": execution_id,
                "input_version_refs": json.dumps(input_version_refs),
                "evidence_refs": json.dumps(evidence_refs),
                "created_at": completed_at,
            },
        )
        .mappings()
        .one()
    )
    settle_xingce_evidence(
        session,
        repository=repository,
        command_id=command_id,
        user_id=user_id,
        task_session=task_session,
        execution_version_id=execution_id,
        created_at=completed_at,
    )
    plan = PlanningService(repository=planning_repository).get_or_create_today(
        session,
        user_id=user_id,
        now=completed_at,
    )
    return XingceCompletionReceipt(
        receiptId=str(row["late_evidence_id"]),
        settlementStatus="late_evidence_linked",
        plan=plan,
    )


def _append_plan_state_event(
    session: Session,
    *,
    user_id: str,
    command_id: str,
    task: RowMapping,
    previous: RowMapping,
    current: RowMapping,
    task_session: RowMapping,
    occurred_at: datetime,
) -> None:
    event_id = uuid4()
    execution_id = str(current["execution_version_id"])
    event_type = "plan-task-state-changed@v1"
    payload = {
        "eventId": str(event_id),
        "eventType": event_type,
        "aggregateId": execution_id,
        "aggregateRevision": int(current["version_no"]),
        "occurredAt": occurred_at.isoformat(),
        "producer": "planning",
        "subjectRef": _fingerprint({"scope": "event-subject", "subject": user_id}),
        "trackId": "xingce",
        "correlationId": command_id,
        "causationId": command_id,
        "payload": {
            "fromState": str(previous["status"]),
            "toState": str(current["status"]),
            "resourceVersionRefs": [
                f"plan-task:{task['task_id']}",
                f"task-execution:{execution_id}",
                f"task-session:{task_session['session_id']}:{task_session['revision']}",
            ],
            "reasonCode": "xingce_reflection_and_cause_recorded",
        },
        "schemaVersion": 1,
    }
    OutboxRepository().append(
        session,
        event_id=event_id,
        deduplication_key=f"{event_type}:{execution_id}:{current['version_no']}",
        aggregate_type="planning_task_execution",
        aggregate_id=execution_id,
        event_type=event_type,
        payload=payload,
    )


def _source_task_for_started_session(
    session: Session,
    *,
    user_id: str,
    work_item_key: str,
    source_plan_task_id: str,
    source_plan_version_id: str,
) -> RowMapping | None:
    return (
        session.execute(
            text(
                """
            select task.*, plan.valid_until
            from learning.daily_plan_tasks task
            join learning.daily_plan_versions plan
              on plan.plan_version_id = task.plan_version_id
             and plan.user_id = task.user_id
             and plan.local_date = task.local_date
             and plan.version_no = task.plan_version_no
            where task.user_id = cast(:user_id as uuid)
              and task.work_item_key = :work_item_key
              and task.task_id = cast(:source_plan_task_id as uuid)
              and task.plan_version_id = cast(:source_plan_version_id as uuid)
              and task.track = 'xingce'
            for update of task
            """
            ),
            {
                "user_id": user_id,
                "work_item_key": work_item_key,
                "source_plan_task_id": source_plan_task_id,
                "source_plan_version_id": source_plan_version_id,
            },
        )
        .mappings()
        .one_or_none()
    )


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()
