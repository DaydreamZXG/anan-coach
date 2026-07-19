from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


TaskResourceType = Literal["xingce_task_session", "mock_exam_session"]
TaskEditChoice = Literal["use_server", "use_this_device"]


class TaskEditingModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, extra="forbid", str_strip_whitespace=True
    )


class ClaimTaskEditLeaseCommand(TaskEditingModel):
    resource_type: TaskResourceType = Field(alias="resourceType")
    resource_id: UUID = Field(alias="resourceId")
    device_id: str = Field(alias="deviceId", min_length=24, max_length=120)
    device_label: str = Field(alias="deviceLabel", min_length=2, max_length=120)
    local_base_revision: int | None = Field(
        default=None, alias="localBaseRevision", ge=1
    )
    has_local_changes: bool = Field(alias="hasLocalChanges")


class TakeOverTaskEditLeaseCommand(TaskEditingModel):
    expected_lease_id: UUID = Field(alias="expectedLeaseId")
    expected_generation: int = Field(alias="expectedGeneration", ge=1)
    device_id: str = Field(alias="deviceId", min_length=24, max_length=120)
    device_label: str = Field(alias="deviceLabel", min_length=2, max_length=120)
    local_base_revision: int | None = Field(
        default=None, alias="localBaseRevision", ge=1
    )
    has_local_changes: bool = Field(alias="hasLocalChanges")


class ResolveTaskEditConflictCommand(TaskEditingModel):
    expected_lease_id: UUID = Field(alias="expectedLeaseId")
    expected_generation: int = Field(alias="expectedGeneration", ge=1)
    expected_server_revision: int = Field(alias="expectedServerRevision", ge=1)
    choice: TaskEditChoice


class BeginTaskEditConflictCommand(TaskEditingModel):
    expected_lease_id: UUID = Field(alias="expectedLeaseId")
    expected_generation: int = Field(alias="expectedGeneration", ge=1)
    local_base_revision: int = Field(alias="localBaseRevision", ge=1)


class TaskEditLeaseProjection(TaskEditingModel):
    state: Literal["owned", "takeover_required", "conflict_choice_required"]
    resource_type: TaskResourceType = Field(alias="resourceType")
    resource_id: UUID = Field(alias="resourceId")
    lease_id: UUID = Field(alias="leaseId")
    generation: int = Field(ge=1)
    device_id: str = Field(alias="deviceId")
    device_label: str = Field(alias="deviceLabel")
    server_revision: int = Field(alias="serverRevision", ge=1)
    local_base_revision: int | None = Field(default=None, alias="localBaseRevision")
    acquired_at: datetime = Field(alias="acquiredAt")
    last_seen_at: datetime = Field(alias="lastSeenAt")
    taken_over_at: datetime | None = Field(default=None, alias="takenOverAt")


class TaskEditLeaseToken(BaseModel):
    lease_id: UUID
    generation: int = Field(ge=1)
