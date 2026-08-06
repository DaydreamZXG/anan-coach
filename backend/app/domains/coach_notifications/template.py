from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any

from app.domains.coach_notifications.schemas import CoachCategory


class CoachTemplateConfigurationError(RuntimeError):
    """Raised when the published WeChat template contract is absent or invalid."""


_FIELD_PATTERN = re.compile(r"^(thing|time)\d+$")
_ROUTES: dict[CoachCategory, str] = {
    "task": "pages/today/index",
    "reflection": "pages/today/index",
    "retest": "pages/today/index",
    "mock": "pages/today/index",
    "weekly_summary": "plans/weekly-plan/index",
}
_ACTIONS: dict[CoachCategory, str] = {
    "task": "打开今日计划继续",
    "reflection": "打开今日计划复盘",
    "retest": "打开今日计划复测",
    "mock": "打开今日计划查看模考",
    "weekly_summary": "打开周计划完成总结",
}


@dataclass(frozen=True)
class CoachTemplateRelease:
    release_id: str
    template_id: str
    title_field: str
    reminder_time_field: str
    action_field: str

    @classmethod
    def from_json(cls, raw: str) -> "CoachTemplateRelease | None":
        if not raw.strip():
            return None
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise CoachTemplateConfigurationError(
                "WECHAT_COACH_TEMPLATE_RELEASE_JSON is not valid JSON"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {
            "releaseId",
            "templateId",
            "fields",
        }:
            raise CoachTemplateConfigurationError(
                "coach template release must contain only releaseId, templateId, and fields"
            )
        release_id = _required(payload, "releaseId", maximum=160)
        template_id = _required(payload, "templateId", maximum=200)
        fields = payload["fields"]
        if not isinstance(fields, dict) or set(fields) != {
            "title",
            "reminderTime",
            "action",
        }:
            raise CoachTemplateConfigurationError(
                "coach template fields must be title, reminderTime, and action"
            )
        title_field = _field(fields, "title", expected="thing")
        reminder_time_field = _field(fields, "reminderTime", expected="time")
        action_field = _field(fields, "action", expected="thing")
        if len({title_field, reminder_time_field, action_field}) != 3:
            raise CoachTemplateConfigurationError("coach template fields must be unique")
        return cls(
            release_id=release_id,
            template_id=template_id,
            title_field=title_field,
            reminder_time_field=reminder_time_field,
            action_field=action_field,
        )

    def message(
        self,
        *,
        category: CoachCategory,
        delivery_id: str,
        local_now: datetime,
    ) -> tuple[str, dict[str, dict[str, str]]]:
        page = f"{_ROUTES[category]}?notificationDeliveryId={delivery_id}"
        values = {
            self.title_field: {"value": "岸岸学习提醒"},
            self.reminder_time_field: {
                "value": local_now.strftime("%Y年%m月%d日 %H:%M")
            },
            self.action_field: {"value": _ACTIONS[category]},
        }
        self._validate_message(values)
        return page, values

    def _validate_message(self, data: dict[str, dict[str, str]]) -> None:
        expected = {self.title_field, self.reminder_time_field, self.action_field}
        if set(data) != expected:
            raise CoachTemplateConfigurationError("template payload field set drifted")
        for key, item in data.items():
            value = item.get("value")
            if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
                raise CoachTemplateConfigurationError("template values must be one-line text")
            if key.startswith("thing") and len(value) > 20:
                raise CoachTemplateConfigurationError("thing template value exceeds 20 characters")


def _required(payload: dict[str, Any], key: str, *, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CoachTemplateConfigurationError(f"coach template {key} is invalid")
    return value.strip()


def _field(payload: dict[str, Any], key: str, *, expected: str) -> str:
    value = _required(payload, key, maximum=80)
    match = _FIELD_PATTERN.fullmatch(value)
    if match is None or match.group(1) != expected:
        raise CoachTemplateConfigurationError(
            f"coach template field {key} must use a {expected}.DATA keyword"
        )
    return value
