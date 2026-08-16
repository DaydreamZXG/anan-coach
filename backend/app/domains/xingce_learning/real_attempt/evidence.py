from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository


def record_pending_xingce_evidence(
    session: Session,
    *,
    repository: XingceAttemptRepository,
    command_id: str,
    user_id: str,
    task_session: RowMapping,
    assessment_ids: list[str],
    created_at: datetime,
) -> RowMapping:
    if not assessment_ids or len(assessment_ids) != len(set(assessment_ids)):
        raise ValueError("Xingce planning evidence requires unique assessments")
    return repository.insert_plan_evidence(
        session,
        values={
            "evidence_version_id": str(uuid4()),
            "command_id": command_id,
            "user_id": user_id,
            "session_id": str(task_session["session_id"]),
            "work_item_key": str(task_session["work_item_key"]),
            "version_no": 1,
            "state": "pending_reflection",
            "assessment_ids": json.dumps(assessment_ids, sort_keys=True),
            "execution_version_id": None,
            "supersedes_evidence_version_id": None,
            "created_at": created_at,
        },
    )


def record_settled_xingce_evidence(
    session: Session,
    *,
    repository: XingceAttemptRepository,
    command_id: str,
    user_id: str,
    task_session: RowMapping,
    assessment_ids: list[str],
    execution_version_id: str,
    created_at: datetime,
) -> RowMapping:
    """Record terminal evidence when no reflection is truthfully required."""
    if not assessment_ids or len(assessment_ids) != len(set(assessment_ids)):
        raise ValueError("Xingce planning evidence requires unique assessments")
    return repository.insert_plan_evidence(
        session,
        values={
            "evidence_version_id": str(uuid4()),
            "command_id": command_id,
            "user_id": user_id,
            "session_id": str(task_session["session_id"]),
            "work_item_key": str(task_session["work_item_key"]),
            "version_no": 1,
            "state": "settled",
            "assessment_ids": json.dumps(assessment_ids, sort_keys=True),
            "execution_version_id": execution_version_id,
            "supersedes_evidence_version_id": None,
            "created_at": created_at,
        },
    )


def settle_xingce_evidence(
    session: Session,
    *,
    repository: XingceAttemptRepository,
    command_id: str,
    user_id: str,
    task_session: RowMapping,
    execution_version_id: str,
    created_at: datetime,
) -> RowMapping:
    current = repository.current_plan_evidence(
        session,
        user_id=user_id,
        session_id=str(task_session["session_id"]),
    )
    if current is None or current["state"] != "pending_reflection":
        raise ValueError("Xingce pending planning evidence does not exist")
    return repository.insert_plan_evidence(
        session,
        values={
            "evidence_version_id": str(uuid4()),
            "command_id": command_id,
            "user_id": user_id,
            "session_id": str(task_session["session_id"]),
            "work_item_key": str(task_session["work_item_key"]),
            "version_no": int(current["version_no"]) + 1,
            "state": "settled",
            "assessment_ids": json.dumps(
                list(current["assessment_ids"]), sort_keys=True
            ),
            "execution_version_id": execution_version_id,
            "supersedes_evidence_version_id": str(current["evidence_version_id"]),
            "created_at": created_at,
        },
    )
