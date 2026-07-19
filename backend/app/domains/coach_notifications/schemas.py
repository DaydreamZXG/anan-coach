from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


CoachCategory = Literal["task", "reflection", "retest", "mock", "weekly_summary"]
SubscriptionResult = Literal["accept", "reject", "ban", "filter"]
SubscriptionEventSource = Literal["request_prompt", "settings_snapshot"]


class CoachContract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)


class CoachPreferenceView(CoachContract):
    preference_version_id: UUID = Field(alias="preferenceVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    enabled: bool
    task_reminders_enabled: bool = Field(alias="taskRemindersEnabled")
    reflection_reminders_enabled: bool = Field(alias="reflectionRemindersEnabled")
    retest_reminders_enabled: bool = Field(alias="retestRemindersEnabled")
    mock_reminders_enabled: bool = Field(alias="mockRemindersEnabled")
    weekly_summary_enabled: bool = Field(alias="weeklySummaryEnabled")
    receive_start: time | None = Field(alias="receiveStart")
    receive_end: time | None = Field(alias="receiveEnd")
    quiet_enabled: bool = Field(alias="quietEnabled")
    quiet_start: time | None = Field(alias="quietStart")
    quiet_end: time | None = Field(alias="quietEnd")
    daily_limit: int = Field(alias="dailyLimit", ge=1, le=24)
    timezone: Literal["Asia/Shanghai"]
    source_type: Literal["system_default", "user_confirmed", "user_disabled"] = Field(
        alias="sourceType"
    )
    created_at: datetime = Field(alias="createdAt")


class UpdateCoachPreferenceCommand(CoachContract):
    based_on_preference_version_id: UUID = Field(alias="basedOnPreferenceVersionId")
    enabled: bool
    task_reminders_enabled: bool = Field(alias="taskRemindersEnabled")
    reflection_reminders_enabled: bool = Field(alias="reflectionRemindersEnabled")
    retest_reminders_enabled: bool = Field(alias="retestRemindersEnabled")
    mock_reminders_enabled: bool = Field(alias="mockRemindersEnabled")
    weekly_summary_enabled: bool = Field(alias="weeklySummaryEnabled")
    receive_start: time | None = Field(alias="receiveStart")
    receive_end: time | None = Field(alias="receiveEnd")
    quiet_enabled: bool = Field(alias="quietEnabled")
    quiet_start: time | None = Field(alias="quietStart")
    quiet_end: time | None = Field(alias="quietEnd")
    daily_limit: int = Field(alias="dailyLimit", ge=1, le=24)
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"

    @model_validator(mode="after")
    def validate_windows(self) -> "UpdateCoachPreferenceCommand":
        categories = (
            self.task_reminders_enabled,
            self.reflection_reminders_enabled,
            self.retest_reminders_enabled,
            self.mock_reminders_enabled,
            self.weekly_summary_enabled,
        )
        if self.enabled:
            if not any(categories):
                raise ValueError("enabled supervision requires at least one reminder category")
            if (
                self.receive_start is None
                or self.receive_end is None
                or self.receive_start == self.receive_end
            ):
                raise ValueError("enabled supervision requires a non-empty receive window")
        if self.quiet_enabled:
            if (
                self.quiet_start is None
                or self.quiet_end is None
                or self.quiet_start == self.quiet_end
            ):
                raise ValueError("quiet hours require a non-empty time window")
        elif self.quiet_start is not None or self.quiet_end is not None:
            raise ValueError("disabled quiet hours cannot retain hidden boundaries")
        return self


class DisableCoachNotificationsCommand(CoachContract):
    based_on_preference_version_id: UUID = Field(alias="basedOnPreferenceVersionId")


class WechatSubscriptionEventCommand(CoachContract):
    source: SubscriptionEventSource
    template_code: Literal["coach_reminder"] = Field(alias="templateCode")
    template_id: str = Field(alias="templateId", min_length=1, max_length=200)
    result: SubscriptionResult


class WechatSubscriptionEventView(CoachContract):
    event_id: UUID = Field(alias="eventId")
    source: SubscriptionEventSource
    template_code: Literal["coach_reminder"] = Field(alias="templateCode")
    result: SubscriptionResult
    grant_created: bool = Field(alias="grantCreated")
    available_grants: int = Field(alias="availableGrants", ge=0)
    occurred_at: datetime = Field(alias="occurredAt")


class CoachTemplateView(CoachContract):
    template_code: Literal["coach_reminder"] = Field(alias="templateCode")
    title: str
    authorization_mode: Literal["one_time"] = Field(alias="authorizationMode")
    configured: bool
    template_id: str | None = Field(alias="templateId")
    release_id: str | None = Field(alias="releaseId")
    available_grants: int = Field(alias="availableGrants", ge=0)
    last_result: SubscriptionResult | None = Field(alias="lastResult")
    last_result_source: SubscriptionEventSource | None = Field(alias="lastResultSource")


class CoachNotificationHistoryItem(CoachContract):
    delivery_id: UUID = Field(alias="deliveryId")
    category: CoachCategory
    state: Literal[
        "queued", "sending", "platform_accepted", "platform_rejected", "failed",
        "outcome_unknown", "canceled"
    ]
    created_at: datetime = Field(alias="createdAt")
    accepted_at: datetime | None = Field(alias="acceptedAt")
    opened_at: datetime | None = Field(alias="openedAt")


class CoachNotificationSettings(CoachContract):
    preference: CoachPreferenceView
    template: CoachTemplateView
    recent_deliveries: list[CoachNotificationHistoryItem] = Field(
        alias="recentDeliveries", max_length=20
    )
    delivery_fact_note: str = Field(alias="deliveryFactNote")


class CoachNotificationOpenCommand(CoachContract):
    opened_at: datetime | None = Field(default=None, alias="openedAt")


class CoachNotificationOpenView(CoachContract):
    delivery_id: UUID = Field(alias="deliveryId")
    opened_at: datetime = Field(alias="openedAt")
    task_completion_changed: Literal[False] = Field(
        default=False, alias="taskCompletionChanged"
    )


class EvaluationFact(CoachContract):
    category: CoachCategory
    local_date: date = Field(alias="localDate")
    fact_fingerprint: str = Field(alias="factFingerprint", pattern=r"^[0-9a-f]{64}$")
    fact_refs: list[dict[str, str | int]] = Field(alias="factRefs", min_length=1)
    task_count: int = Field(alias="taskCount", ge=0)
