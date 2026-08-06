from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.task_editing.repository import TaskEditingRepository
from app.domains.task_editing.schemas import (
    BeginTaskEditConflictCommand,
    ClaimTaskEditLeaseCommand,
    ResolveTaskEditConflictCommand,
    TakeOverTaskEditLeaseCommand,
    TaskEditLeaseProjection,
    TaskEditLeaseToken,
    TaskResourceType,
)


class TaskEditingError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class TaskEditingService:
    def __init__(self, repository: TaskEditingRepository | None = None) -> None:
        self._repository = repository or TaskEditingRepository()

    def claim(
        self,
        session: Session,
        *,
        user_id: str,
        command: ClaimTaskEditLeaseCommand,
        now: datetime | None = None,
    ) -> TaskEditLeaseProjection:
        at = _utc(now)
        resource_id = str(command.resource_id)
        self._repository.lock_user(session, user_id=user_id)
        server_revision = self._required_revision(
            session,
            user_id=user_id,
            resource_type=command.resource_type,
            resource_id=resource_id,
            require_editable=True,
        )
        current = self._repository.lease(
            session,
            user_id=user_id,
            resource_type=command.resource_type,
            resource_id=resource_id,
            for_update=True,
        )
        if current is None:
            row = self._repository.insert_lease(
                session,
                values={
                    "resource_type": command.resource_type,
                    "resource_id": resource_id,
                    "user_id": user_id,
                    "lease_id": str(uuid4()),
                    "generation": 1,
                    "device_id": command.device_id,
                    "device_label": command.device_label.strip(),
                    "reconciliation_state": "clean",
                    "local_base_revision": None,
                    "server_revision": server_revision,
                    "acquired_at": at,
                    "last_seen_at": at,
                    "taken_over_at": None,
                },
            )
            self._event(session, row=row, event_type="acquired", at=at)
            return _projection(row, state="owned")
        if str(current["device_id"]) != command.device_id:
            self._event(
                session,
                row=current,
                event_type="takeover_requested",
                at=at,
                resource_revision=server_revision,
                local_base_revision=command.local_base_revision,
                metadata={"requestingDeviceLabel": command.device_label.strip()},
            )
            return _projection(
                current, state="takeover_required", server_revision=server_revision
            )
        state = str(current["reconciliation_state"])
        if (
            command.has_local_changes
            and command.local_base_revision is not None
            and command.local_base_revision != server_revision
        ):
            state = "choice_required"
        row = self._repository.update_lease(
            session,
            user_id=user_id,
            resource_type=command.resource_type,
            resource_id=resource_id,
            values={
                **_lease_values(current),
                "device_label": command.device_label.strip(),
                "reconciliation_state": state,
                "local_base_revision": (
                    command.local_base_revision if state == "choice_required" else None
                ),
                "server_revision": server_revision,
                "last_seen_at": at,
            },
        )
        self._event(session, row=row, event_type="reconnected", at=at)
        if state == "choice_required" and str(current["reconciliation_state"]) != state:
            self._event(
                session,
                row=row,
                event_type="conflict_detected",
                at=at,
                local_base_revision=command.local_base_revision,
            )
        return _projection(
            row,
            state="conflict_choice_required" if state == "choice_required" else "owned",
        )

    def take_over(
        self,
        session: Session,
        *,
        user_id: str,
        resource_type: TaskResourceType,
        resource_id: str,
        command: TakeOverTaskEditLeaseCommand,
        now: datetime | None = None,
    ) -> TaskEditLeaseProjection:
        at = _utc(now)
        self._repository.lock_user(session, user_id=user_id)
        server_revision = self._required_revision(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            require_editable=True,
        )
        current = self._required_lease(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            for_update=True,
        )
        if (
            str(current["lease_id"]) != str(command.expected_lease_id)
            or int(current["generation"]) != command.expected_generation
        ):
            raise self._ownership_conflict(current, server_revision)
        local_base = command.local_base_revision
        choice_required = bool(
            command.has_local_changes
            and local_base is not None
            and local_base != server_revision
        )
        row = self._repository.update_lease(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            values={
                "lease_id": str(uuid4()),
                "generation": int(current["generation"]) + 1,
                "device_id": command.device_id,
                "device_label": command.device_label.strip(),
                "reconciliation_state": "choice_required"
                if choice_required
                else "clean",
                "local_base_revision": local_base if choice_required else None,
                "server_revision": server_revision,
                "acquired_at": at,
                "last_seen_at": at,
                "taken_over_at": at,
            },
        )
        self._event(
            session,
            row=row,
            event_type="taken_over",
            at=at,
            local_base_revision=local_base,
            metadata={"hadLocalChanges": command.has_local_changes},
        )
        if choice_required:
            self._event(
                session,
                row=row,
                event_type="conflict_detected",
                at=at,
                local_base_revision=local_base,
            )
        return _projection(
            row, state="conflict_choice_required" if choice_required else "owned"
        )

    def resolve(
        self,
        session: Session,
        *,
        user_id: str,
        resource_type: TaskResourceType,
        resource_id: str,
        command: ResolveTaskEditConflictCommand,
        now: datetime | None = None,
    ) -> TaskEditLeaseProjection:
        at = _utc(now)
        self._repository.lock_user(session, user_id=user_id)
        actual_revision = self._required_revision(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            require_editable=True,
        )
        current = self._required_lease(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            for_update=True,
        )
        if (
            str(current["lease_id"]) != str(command.expected_lease_id)
            or int(current["generation"]) != command.expected_generation
        ):
            raise self._ownership_conflict(current, actual_revision)
        if str(current["reconciliation_state"]) != "choice_required":
            raise TaskEditingError(
                "task_edit_conflict_not_pending",
                "当前任务没有待选择的断网冲突。",
                status_code=409,
            )
        if actual_revision != command.expected_server_revision:
            row = self._repository.update_lease(
                session,
                user_id=user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                values={
                    **_lease_values(current),
                    "server_revision": actual_revision,
                    "last_seen_at": at,
                },
            )
            raise TaskEditingError(
                "task_edit_server_moved",
                "选择期间云端进度再次变化，请重新对比后选择。",
                status_code=409,
                details=_details(row, actual_revision),
            )
        row = self._repository.update_lease(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            values={
                **_lease_values(current),
                "reconciliation_state": "clean",
                "local_base_revision": None,
                "server_revision": actual_revision,
                "last_seen_at": at,
            },
        )
        self._event(
            session,
            row=row,
            event_type="conflict_resolved",
            at=at,
            local_base_revision=(
                int(current["local_base_revision"])
                if current["local_base_revision"] is not None
                else None
            ),
            resolution=command.choice,
        )
        return _projection(row, state="owned")

    def begin_conflict(
        self,
        session: Session,
        *,
        user_id: str,
        resource_type: TaskResourceType,
        resource_id: str,
        command: BeginTaskEditConflictCommand,
        now: datetime | None = None,
    ) -> TaskEditLeaseProjection:
        at = _utc(now)
        self._repository.lock_user(session, user_id=user_id)
        actual_revision = self._required_revision(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            require_editable=True,
        )
        current = self._required_lease(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            for_update=True,
        )
        if (
            str(current["lease_id"]) != str(command.expected_lease_id)
            or int(current["generation"]) != command.expected_generation
        ):
            raise self._ownership_conflict(current, actual_revision)
        if command.local_base_revision == actual_revision:
            raise TaskEditingError(
                "task_edit_conflict_not_present",
                "本机与云端版本一致，不需要冲突选择。",
                status_code=409,
            )
        row = self._repository.update_lease(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            values={
                **_lease_values(current),
                "reconciliation_state": "choice_required",
                "local_base_revision": command.local_base_revision,
                "server_revision": actual_revision,
                "last_seen_at": at,
            },
        )
        self._event(
            session,
            row=row,
            event_type="conflict_detected",
            at=at,
            local_base_revision=command.local_base_revision,
        )
        return _projection(row, state="conflict_choice_required")

    def authorize_write(
        self,
        session: Session,
        *,
        user_id: str,
        resource_type: TaskResourceType,
        resource_id: str,
        token: TaskEditLeaseToken | None,
        expected_revision: int,
        now: datetime | None = None,
    ) -> None:
        at = _utc(now)
        actual_revision = self._required_revision(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            require_editable=False,
        )
        if token is None:
            raise TaskEditingError(
                "task_edit_lease_required",
                "请先取得当前任务的设备编辑权。",
                status_code=409,
            )
        current = self._required_lease(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            for_update=True,
        )
        if (
            str(current["lease_id"]) != str(token.lease_id)
            or int(current["generation"]) != token.generation
        ):
            raise self._ownership_conflict(current, actual_revision)
        if str(current["reconciliation_state"]) != "clean":
            raise TaskEditingError(
                "task_edit_choice_required",
                "请先选择保留本机草稿还是云端进度。",
                status_code=409,
                details=_details(current, actual_revision),
            )
        row = self._repository.update_lease(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            values={
                **_lease_values(current),
                "server_revision": actual_revision,
                "last_seen_at": at,
            },
        )
        self._event(
            session,
            row=row,
            event_type="write_authorized",
            at=at,
            metadata={
                "expectedRevision": expected_revision,
                "actualRevision": actual_revision,
            },
        )

    def _required_revision(
        self,
        session: Session,
        *,
        user_id: str,
        resource_type: TaskResourceType,
        resource_id: str,
        require_editable: bool,
    ) -> int:
        revision = self._repository.resource_revision(
            session,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            require_editable=require_editable,
        )
        if revision is None:
            raise TaskEditingError(
                "task_edit_resource_unavailable",
                "任务不存在、已结束或不属于当前账户。",
                status_code=404,
            )
        return revision

    def _required_lease(self, session: Session, **kwargs: object) -> RowMapping:
        row = self._repository.lease(session, **kwargs)  # type: ignore[arg-type]
        if row is None:
            raise TaskEditingError(
                "task_edit_lease_required",
                "请先取得当前任务的设备编辑权。",
                status_code=409,
            )
        return row

    @staticmethod
    def _ownership_conflict(row: RowMapping, server_revision: int) -> TaskEditingError:
        return TaskEditingError(
            "task_edit_owned_elsewhere",
            "此任务已由另一台设备接管。",
            status_code=409,
            details=_details(row, server_revision),
        )

    def _event(
        self,
        session: Session,
        *,
        row: RowMapping,
        event_type: str,
        at: datetime,
        resource_revision: int | None = None,
        local_base_revision: int | None = None,
        resolution: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self._repository.append_event(
            session,
            values={
                "event_id": str(uuid4()),
                "user_id": str(row["user_id"]),
                "resource_type": str(row["resource_type"]),
                "resource_id": str(row["resource_id"]),
                "lease_id": str(row["lease_id"]),
                "generation": int(row["generation"]),
                "device_id": str(row["device_id"]),
                "device_label": str(row["device_label"]),
                "event_type": event_type,
                "resource_revision": resource_revision or int(row["server_revision"]),
                "local_base_revision": local_base_revision,
                "resolution": resolution,
                "metadata": metadata or {},
                "created_at": at,
            },
        )


def _projection(
    row: RowMapping, *, state: str, server_revision: int | None = None
) -> TaskEditLeaseProjection:
    return TaskEditLeaseProjection.model_validate(
        {
            "state": state,
            "resourceType": row["resource_type"],
            "resourceId": row["resource_id"],
            "leaseId": row["lease_id"],
            "generation": row["generation"],
            "deviceId": row["device_id"],
            "deviceLabel": row["device_label"],
            "serverRevision": server_revision or row["server_revision"],
            "localBaseRevision": row["local_base_revision"],
            "acquiredAt": row["acquired_at"],
            "lastSeenAt": row["last_seen_at"],
            "takenOverAt": row["taken_over_at"],
        }
    )


def _lease_values(row: RowMapping) -> dict[str, object]:
    return {
        "lease_id": str(row["lease_id"]),
        "generation": int(row["generation"]),
        "device_id": str(row["device_id"]),
        "device_label": str(row["device_label"]),
        "reconciliation_state": str(row["reconciliation_state"]),
        "local_base_revision": row["local_base_revision"],
        "server_revision": int(row["server_revision"]),
        "acquired_at": row["acquired_at"],
        "last_seen_at": row["last_seen_at"],
        "taken_over_at": row["taken_over_at"],
    }


def _details(row: RowMapping, server_revision: int) -> dict[str, object]:
    return {
        "leaseId": str(row["lease_id"]),
        "generation": int(row["generation"]),
        "deviceLabel": str(row["device_label"]),
        "serverRevision": server_revision,
        "conflictChoiceRequired": str(row["reconciliation_state"]) == "choice_required",
    }


def _utc(value: datetime | None) -> datetime:
    resolved = value or datetime.now(UTC)
    return resolved.astimezone(UTC)
