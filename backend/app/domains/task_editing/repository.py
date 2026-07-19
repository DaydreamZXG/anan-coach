from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.task_editing.schemas import TaskResourceType


class TaskEditingRepository:
    def lock_user(self, session: Session, *, user_id: str) -> None:
        owner = session.execute(
            text(
                "select user_id from learner.users "
                "where user_id = cast(:user_id as uuid) for update"
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if owner is None:
            raise LookupError("learner_not_found")

    def resource_revision(
        self,
        session: Session,
        *,
        user_id: str,
        resource_type: TaskResourceType,
        resource_id: str,
        require_editable: bool,
    ) -> int | None:
        if resource_type == "xingce_task_session":
            state_clause = "and state = 'active'" if require_editable else ""
            query = (
                "select revision from learning.xingce_task_sessions "
                "where session_id = cast(:resource_id as uuid) "
                "and user_id = cast(:user_id as uuid) " + state_clause
            )
        else:
            state_clause = "and status = 'assigned'" if require_editable else ""
            query = (
                "select revision from learner.mock_exam_sessions "
                "where session_id = cast(:resource_id as uuid) "
                "and user_id = cast(:user_id as uuid) " + state_clause
            )
        value = session.execute(
            text(query), {"resource_id": resource_id, "user_id": user_id}
        ).scalar_one_or_none()
        return int(value) if value is not None else None

    def lease(
        self,
        session: Session,
        *,
        user_id: str,
        resource_type: TaskResourceType,
        resource_id: str,
        for_update: bool = False,
    ) -> RowMapping | None:
        suffix = " for update" if for_update else ""
        return (
            session.execute(
                text(
                    "select * from learning.task_edit_leases "
                    "where user_id = cast(:user_id as uuid) "
                    "and resource_type = :resource_type "
                    "and resource_id = cast(:resource_id as uuid)" + suffix
                ),
                {
                    "user_id": user_id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                },
            )
            .mappings()
            .one_or_none()
        )

    def insert_lease(self, session: Session, *, values: dict[str, Any]) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    insert into learning.task_edit_leases (
                      resource_type, resource_id, user_id, lease_id, generation,
                      device_id, device_label, reconciliation_state,
                      local_base_revision, server_revision, acquired_at,
                      last_seen_at, taken_over_at
                    ) values (
                      :resource_type, cast(:resource_id as uuid), cast(:user_id as uuid),
                      cast(:lease_id as uuid), :generation, :device_id, :device_label,
                      :reconciliation_state, :local_base_revision, :server_revision,
                      :acquired_at, :last_seen_at, :taken_over_at
                    ) returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def update_lease(
        self,
        session: Session,
        *,
        user_id: str,
        resource_type: TaskResourceType,
        resource_id: str,
        values: dict[str, Any],
    ) -> RowMapping:
        parameters = {
            **values,
            "user_id": user_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
        }
        return (
            session.execute(
                text(
                    """
                    update learning.task_edit_leases set
                      lease_id = cast(:lease_id as uuid), generation = :generation,
                      device_id = :device_id, device_label = :device_label,
                      reconciliation_state = :reconciliation_state,
                      local_base_revision = :local_base_revision,
                      server_revision = :server_revision,
                      acquired_at = :acquired_at, last_seen_at = :last_seen_at,
                      taken_over_at = :taken_over_at
                    where user_id = cast(:user_id as uuid)
                      and resource_type = :resource_type
                      and resource_id = cast(:resource_id as uuid)
                    returning *
                    """
                ),
                parameters,
            )
            .mappings()
            .one()
        )

    def append_event(self, session: Session, *, values: dict[str, Any]) -> None:
        session.execute(
            text(
                """
                insert into learning.task_edit_events (
                  event_id, user_id, resource_type, resource_id, lease_id,
                  generation, device_id, device_label, event_type,
                  resource_revision, local_base_revision, resolution,
                  metadata, created_at
                ) values (
                  cast(:event_id as uuid), cast(:user_id as uuid), :resource_type,
                  cast(:resource_id as uuid), cast(:lease_id as uuid), :generation,
                  :device_id, :device_label, :event_type, :resource_revision,
                  :local_base_revision, :resolution, cast(:metadata as jsonb), :created_at
                )
                """
            ),
            {
                **values,
                "metadata": json.dumps(values.get("metadata", {}), ensure_ascii=False),
            },
        )
