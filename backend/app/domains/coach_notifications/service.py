from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.engine import RowMapping

from app.core.config import Settings
from app.domains.coach_notifications.repository import CoachNotificationRepository
from app.domains.coach_notifications.schemas import (
    CoachNotificationHistoryItem,
    CoachNotificationOpenCommand,
    CoachNotificationOpenView,
    CoachNotificationSettings,
    CoachPreferenceView,
    CoachTemplateView,
    DisableCoachNotificationsCommand,
    UpdateCoachPreferenceCommand,
    WechatSubscriptionEventCommand,
    WechatSubscriptionEventView,
)
from app.domains.coach_notifications.template import CoachTemplateRelease


DELIVERY_FACT_NOTE = (
    "微信授权、发送请求、平台受理、打开消息和完成任务分别记录；"
    "打开通知不会自动把任务标记为完成。"
)


class CoachNotificationService:
    def __init__(
        self,
        repository: CoachNotificationRepository,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._template_release_json = settings.wechat_coach_template_release_json
        self._clock = clock or (lambda: datetime.now(UTC))

    def settings(self, *, user_id: str) -> CoachNotificationSettings:
        now = self._clock()
        template = self._template()
        preference = self._repository.current_or_create_preference(
            user_id=user_id, now=now
        )
        grants = (
            self._repository.available_grants(
                user_id=user_id, template_id=template.template_id
            )
            if template is not None
            else 0
        )
        latest = self._repository.latest_subscription_event(user_id=user_id)
        return CoachNotificationSettings(
            preference=_preference_view(preference),
            template=CoachTemplateView(
                templateCode="coach_reminder",
                title="岸岸 AI 督学提醒",
                authorizationMode="one_time",
                configured=template is not None,
                templateId=template.template_id if template else None,
                releaseId=template.release_id if template else None,
                availableGrants=grants,
                lastResult=str(latest["result"]) if latest is not None else None,
                lastResultSource=(str(latest["source"]) if latest is not None else None),
            ),
            recentDeliveries=[
                CoachNotificationHistoryItem(
                    deliveryId=row["delivery_id"],
                    category=row["category"],
                    state=row["state"],
                    createdAt=row["created_at"],
                    acceptedAt=row["accepted_at"],
                    openedAt=row["opened_at"],
                )
                for row in self._repository.recent_deliveries(user_id=user_id)
            ],
            deliveryFactNote=DELIVERY_FACT_NOTE,
        )

    def update_preference(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        command: UpdateCoachPreferenceCommand,
    ) -> CoachPreferenceView:
        row = self._repository.update_preference(
            user_id=user_id,
            command_id=f"coach-preference:{user_id}:{idempotency_key}",
            command=command,
            now=self._clock(),
        )
        return _preference_view(row)

    def disable(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        command: DisableCoachNotificationsCommand,
    ) -> CoachPreferenceView:
        current = self._repository.current_or_create_preference(
            user_id=user_id, now=self._clock()
        )
        update = UpdateCoachPreferenceCommand.model_validate(
            {
                "basedOnPreferenceVersionId": command.based_on_preference_version_id,
                "enabled": False,
                "taskRemindersEnabled": bool(current["task_reminders_enabled"]),
                "reflectionRemindersEnabled": bool(
                    current["reflection_reminders_enabled"]
                ),
                "retestRemindersEnabled": bool(current["retest_reminders_enabled"]),
                "mockRemindersEnabled": bool(current["mock_reminders_enabled"]),
                "weeklySummaryEnabled": bool(current["weekly_summary_enabled"]),
                "receiveStart": current["receive_start"],
                "receiveEnd": current["receive_end"],
                "quietEnabled": bool(current["quiet_enabled"]),
                "quietStart": current["quiet_start"],
                "quietEnd": current["quiet_end"],
                "dailyLimit": int(current["daily_limit"]),
                "timezone": str(current["timezone"]),
            }
        )
        row = self._repository.update_preference(
            user_id=user_id,
            command_id=f"coach-disable:{user_id}:{idempotency_key}",
            command=update,
            now=self._clock(),
            force_disabled=True,
        )
        return _preference_view(row)

    def record_subscription_event(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        command: WechatSubscriptionEventCommand,
    ) -> WechatSubscriptionEventView:
        template = self._template()
        if template is None:
            raise ValueError("微信督学订阅模板尚未发布")
        if command.template_id != template.template_id:
            raise ValueError("订阅结果不属于当前发布模板")
        now = self._clock()
        row, grant_created = self._repository.record_subscription_event(
            user_id=user_id,
            command_id=f"coach-subscription:{user_id}:{idempotency_key}",
            command=command,
            now=now,
        )
        return WechatSubscriptionEventView(
            eventId=row["event_id"],
            source=row["source"],
            templateCode=row["template_code"],
            result=row["result"],
            grantCreated=grant_created,
            availableGrants=self._repository.available_grants(
                user_id=user_id, template_id=template.template_id
            ),
            occurredAt=row["occurred_at"],
        )

    def record_open(
        self,
        *,
        user_id: str,
        delivery_id: UUID,
        idempotency_key: str,
        command: CoachNotificationOpenCommand,
    ) -> CoachNotificationOpenView:
        del command  # Receipt time is server-authoritative; client clocks are not trusted.
        row = self._repository.record_open(
            user_id=user_id,
            delivery_id=str(delivery_id),
            command_id=f"coach-open:{user_id}:{idempotency_key}",
            opened_at=self._clock(),
        )
        return CoachNotificationOpenView(
            deliveryId=row["delivery_id"],
            openedAt=row["opened_at"],
            taskCompletionChanged=False,
        )

    def _template(self) -> CoachTemplateRelease | None:
        return CoachTemplateRelease.from_json(self._template_release_json)


def _preference_view(row: RowMapping) -> CoachPreferenceView:
    return CoachPreferenceView(
        preferenceVersionId=row["preference_version_id"],
        versionNo=row["version_no"],
        enabled=row["enabled"],
        taskRemindersEnabled=row["task_reminders_enabled"],
        reflectionRemindersEnabled=row["reflection_reminders_enabled"],
        retestRemindersEnabled=row["retest_reminders_enabled"],
        mockRemindersEnabled=row["mock_reminders_enabled"],
        weeklySummaryEnabled=row["weekly_summary_enabled"],
        receiveStart=row["receive_start"],
        receiveEnd=row["receive_end"],
        quietEnabled=row["quiet_enabled"],
        quietStart=row["quiet_start"],
        quietEnd=row["quiet_end"],
        dailyLimit=row["daily_limit"],
        timezone=row["timezone"],
        sourceType=row["source_type"],
        createdAt=row["created_at"],
    )
