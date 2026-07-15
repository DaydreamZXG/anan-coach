from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from math import ceil
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.planning.repository import PlanningContractError, PlanningRepository
from app.platform.outbox import OutboxRepository


def start_xingce_plan_task(
    session: Session,
    *,
    user_id: str,
    task: RowMapping,
    task_session: RowMapping,
    command_id: str,
    started_at: datetime,
) -> RowMapping:
    """Record the planning-owned in-progress fact pinned by a runtime session."""
    repository = PlanningRepository()
    current = repository.current_task_execution(
        session,
        user_id=user_id,
        work_item_key=str(task["work_item_key"]),
    )
    if current is not None:
        if (
            current["status"] == "in_progress"
            and str(current["task_id"]) == str(task["task_id"])
            and str(current["plan_version_id"]) == str(task["plan_version_id"])
        ):
            return current
        raise PlanningContractError(
            "Xingce plan task already has an incompatible execution state"
        )

    execution_version_id = str(uuid4())
    evidence_refs = [
        {
            "owner": "task_runtime",
            "entityType": "task_session",
            "entityId": str(task_session["session_id"]),
            "versionId": f"{task_session['session_id']}:1",
            "role": "start",
        }
    ]
    fingerprint = _fingerprint(
        {
            "userId": user_id,
            "taskId": str(task["task_id"]),
            "planVersionId": str(task["plan_version_id"]),
            "sessionId": str(task_session["session_id"]),
            "startedAt": started_at.isoformat(),
        }
    )
    row = repository.insert_task_execution(
        session,
        values={
            "execution_version_id": execution_version_id,
            "command_id": f"xingce-start:{user_id}:{command_id}",
            "user_id": user_id,
            "local_date": task["local_date"],
            "task_id": str(task["task_id"]),
            "plan_version_id": str(task["plan_version_id"]),
            "plan_version_no": int(task["plan_version_no"]),
            "candidate_key": str(task["candidate_key"]),
            "work_item_key": str(task["work_item_key"]),
            "version_no": 1,
            "status": "in_progress",
            "elapsed_minutes": max(
                0,
                ceil(int(task_session["total_elapsed_seconds"]) / 60),
            ),
            "evidence_refs": json.dumps(
                evidence_refs, ensure_ascii=False, sort_keys=True
            ),
            "abandonment_reason": None,
            "input_fingerprint": fingerprint,
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
        execution=row,
        session_id=str(task_session["session_id"]),
        occurred_at=started_at,
    )
    return row


def _append_state_event(
    session: Session,
    *,
    user_id: str,
    command_id: str,
    task: RowMapping,
    execution: RowMapping,
    session_id: str,
    occurred_at: datetime,
) -> None:
    event_id = uuid4()
    execution_id = str(execution["execution_version_id"])
    event_type = "plan-task-state-changed@v1"
    payload = {
        "eventId": str(event_id),
        "eventType": event_type,
        "aggregateId": execution_id,
        "aggregateRevision": int(execution["version_no"]),
        "occurredAt": occurred_at.isoformat(),
        "producer": "planning",
        "subjectRef": _fingerprint({"scope": "event-subject", "subject": user_id}),
        "trackId": "xingce",
        "correlationId": command_id,
        "causationId": command_id,
        "payload": {
            "fromState": "",
            "toState": "in_progress",
            "resourceVersionRefs": [
                f"plan-task:{task['task_id']}",
                f"task-execution:{execution_id}",
                f"task-session:{session_id}:1",
            ],
            "reasonCode": "xingce_session_started_within_plan_validity",
        },
        "schemaVersion": 1,
    }
    OutboxRepository().append(
        session,
        event_id=event_id,
        deduplication_key=f"{event_type}:{execution_id}:1",
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
