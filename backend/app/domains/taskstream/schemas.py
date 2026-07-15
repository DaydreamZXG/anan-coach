from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TaskstreamBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class TargetContext(TaskstreamBaseModel):
    exam: str
    position_group: str = Field(alias="positionGroup")
    score_gap: float = Field(alias="scoreGap")
    score_gap_available: bool = Field(alias="scoreGapAvailable")


class TrackMixItem(TaskstreamBaseModel):
    track: str
    minutes: int
    reason: str


class PrimaryAction(TaskstreamBaseModel):
    title: str
    subtitle: str


class TodayAlert(TaskstreamBaseModel):
    id: str
    severity: str
    title: str
    subtitle: str
    action_url: str = Field(alias="actionUrl")


class TodayCalibration(TaskstreamBaseModel):
    xingce_score: float | None = Field(default=None, alias="xingceScore")
    confidence: str
    status_label: str = Field(alias="statusLabel")
    data_line: str = Field(alias="dataLine")


class TodayTask(TaskstreamBaseModel):
    id: str
    title: str
    subtitle: str
    kind: str
    track: str
    module_name: str | None = Field(default=None, alias="moduleName")
    estimated_minutes: int = Field(alias="estimatedMinutes")
    reason: str
    source_grounding: str = Field(alias="sourceGrounding")
    question_ids: list[str] = Field(default_factory=list, alias="questionIds")
    start_url: str = Field(alias="startUrl")


class TodayTaskstream(TaskstreamBaseModel):
    date: str
    headline: str
    plan_basis: str = Field(alias="planBasis")
    target: TargetContext
    track_mix: list[TrackMixItem] = Field(alias="trackMix")
    primary_action: PrimaryAction = Field(alias="primaryAction")
    rationale: str
    calibration: TodayCalibration
    alerts: list[TodayAlert] = Field(default_factory=list)
    tasks: list[TodayTask]
    completed_task_ids: list[str] = Field(alias="completedTaskIds")


class TomorrowFocus(TaskstreamBaseModel):
    module: str
    question_count: int = Field(alias="questionCount")
    reason: str


class TomorrowPreview(TaskstreamBaseModel):
    date: str
    headline: str
    target: TargetContext
    track_mix: list[TrackMixItem] = Field(alias="trackMix")
    focus: TomorrowFocus
    primary_action: PrimaryAction = Field(alias="primaryAction")
