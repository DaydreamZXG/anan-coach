from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domains.ai.agent_runtime.contracts import AgentRuntimeViolation
from app.domains.ai.agent_runtime.runtime import BoundedToolRuntime
from app.domains.companion.schemas import CompanionAllowedAction


COMPANION_AGENT_DEFINITION_RELEASE = "companion-action-agent-2026-08-03.2"
COMPANION_ACTION_POLICY_VERSION = "companion-action-proposal@v2"

Weekday = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


class CompanionActionRequest(BaseModel):
    """One model-proposed action. The server remains the action authority."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    name: Literal[
        "preview_daily_capacity",
        "open_today_training",
        "open_position_workspace",
        "open_private_library",
        "refresh_learning_context",
        "open_profile_archive",
        "open_qualification_profile",
        "open_study_preferences",
        "preview_weekly_schedule",
        "open_weekly_plan",
        "view_learning_evidence",
        "open_position_goal",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class CompanionToolDefinition:
    name: str
    schema_version: str = "v1"
    behavior_version: str = "v1"
    permission_policy_version: str = COMPANION_ACTION_POLICY_VERSION
    # All tools in this release only propose a UI action. They never write state.
    read_only: bool = True


@dataclass(frozen=True)
class CompanionAgentBudget:
    max_tool_calls: int = 3
    max_consecutive_no_progress: int = 2


@dataclass(frozen=True)
class CompanionAgentDefinition:
    tools: tuple[CompanionToolDefinition, ...]
    budget: CompanionAgentBudget = CompanionAgentBudget()
    tool_policy_version: str = COMPANION_ACTION_POLICY_VERSION
    definition_release_id: str = COMPANION_AGENT_DEFINITION_RELEASE


COMPANION_AGENT_DEFINITION = CompanionAgentDefinition(
    tools=tuple(
        CompanionToolDefinition(name=name)
        for name in (
            "preview_daily_capacity",
            "open_today_training",
            "open_position_workspace",
            "open_private_library",
            "refresh_learning_context",
            "open_profile_archive",
            "open_qualification_profile",
            "open_study_preferences",
            "preview_weekly_schedule",
            "open_weekly_plan",
            "view_learning_evidence",
            "open_position_goal",
        )
    )
)


@dataclass(frozen=True)
class CompanionActionOutcome:
    actions: tuple[CompanionAllowedAction, ...]
    events: tuple[dict[str, Any], ...]
    requested_count: int
    succeeded_count: int


class _CapacityArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available_minutes: int | None = Field(
        default=None,
        alias="availableMinutes",
        ge=0,
        le=1440,
    )


class _EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _WeeklyScheduleArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    fixed_rest_day: Weekday | None = Field(default=None, alias="fixedRestDay")
    mock_exam_day: Weekday | None = Field(default=None, alias="mockExamDay")


class _PositionGoalArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    position_id: str = Field(
        alias="positionId",
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    )


@dataclass(frozen=True)
class WeeklyScheduleRequest:
    fixed_rest_day: Weekday | None = None
    mock_exam_day: Weekday | None = None


def execute_companion_actions(
    *,
    requests: list[CompanionActionRequest] | tuple[CompanionActionRequest, ...],
    question_text: str,
    fallback_actions: list[CompanionAllowedAction] | tuple[CompanionAllowedAction, ...],
) -> CompanionActionOutcome:
    """Execute action proposals inside a bounded, non-mutating tool runtime.

    A capacity proposal is accepted only when its value matches an explicit value
    in the user's current message. This prevents the model from inventing a write
    target while preserving the existing user-confirmed preview flow.
    """

    events: list[dict[str, Any]] = []
    explicit_minutes = requested_plan_minutes(question_text)
    explicit_weekly_schedule = requested_weekly_schedule(question_text)

    def preview_capacity(arguments: dict[str, Any]) -> dict[str, Any]:
        parsed = _CapacityArguments.model_validate(arguments)
        if parsed.available_minutes != explicit_minutes:
            raise PermissionError("capacity proposal does not match the explicit user request")
        return _capacity_action(parsed.available_minutes).model_dump(
            by_alias=True,
            mode="json",
        )

    def no_arguments(
        arguments: dict[str, Any],
        *,
        action: CompanionAllowedAction,
    ) -> dict[str, Any]:
        _EmptyArguments.model_validate(arguments)
        return action.model_dump(by_alias=True, mode="json")

    def preview_weekly_schedule(arguments: dict[str, Any]) -> dict[str, Any]:
        parsed = _WeeklyScheduleArguments.model_validate(arguments)
        if parsed.fixed_rest_day is None and parsed.mock_exam_day is None:
            raise PermissionError("weekly schedule proposal requires an explicit day")
        if parsed.fixed_rest_day is not None and parsed.fixed_rest_day == parsed.mock_exam_day:
            raise PermissionError("mock day and recovery day must be different")
        if explicit_weekly_schedule is None or (
            parsed.fixed_rest_day != explicit_weekly_schedule.fixed_rest_day
            or parsed.mock_exam_day != explicit_weekly_schedule.mock_exam_day
        ):
            raise PermissionError("weekly schedule proposal does not match the explicit request")
        return _weekly_schedule_action(explicit_weekly_schedule).model_dump(
            by_alias=True,
            mode="json",
        )

    def open_position_goal(arguments: dict[str, Any]) -> dict[str, Any]:
        parsed = _PositionGoalArguments.model_validate(arguments)
        if not requested_position_goal(question_text, parsed.position_id):
            raise PermissionError("position goal does not match the explicit user request")
        return CompanionAllowedAction(
            kind="open_position_workspace",
            label="预览这个目标岗位",
            targetRef=(
                "/pages/positions/goal/index?" + urlencode({"positionId": parsed.position_id})
            ),
            requiresConfirmation=True,
        ).model_dump(by_alias=True, mode="json")

    handlers = {
        "preview_daily_capacity": preview_capacity,
        "open_today_training": lambda arguments: no_arguments(
            arguments,
            action=CompanionAllowedAction(
                kind="open_training",
                label="查看今日训练安排",
                targetRef="/pages/today/index",
                requiresConfirmation=False,
            ),
        ),
        "open_position_workspace": lambda arguments: no_arguments(
            arguments,
            action=CompanionAllowedAction(
                kind="open_position_workspace",
                label="查看岗位判断",
                targetRef="/pages/positions/index",
                requiresConfirmation=False,
            ),
        ),
        "open_private_library": lambda arguments: no_arguments(
            arguments,
            action=CompanionAllowedAction(
                kind="supply_information",
                label="打开专属资料库",
                targetRef="/pages/private-knowledge/index",
                requiresConfirmation=False,
            ),
        ),
        "refresh_learning_context": lambda arguments: no_arguments(
            arguments,
            action=CompanionAllowedAction(
                kind="refresh_context",
                label="刷新学习证据",
                targetRef=None,
                requiresConfirmation=False,
            ),
        ),
        "open_profile_archive": lambda arguments: no_arguments(
            arguments,
            action=CompanionAllowedAction(
                kind="supply_information",
                label="查看个人档案",
                targetRef="/pages/profile/archive/index",
                requiresConfirmation=False,
            ),
        ),
        "open_qualification_profile": lambda arguments: no_arguments(
            arguments,
            action=CompanionAllowedAction(
                kind="supply_information",
                label="修改报名资格资料",
                targetRef="/pages/onboarding/profile/index",
                requiresConfirmation=False,
            ),
        ),
        "open_study_preferences": lambda arguments: no_arguments(
            arguments,
            action=CompanionAllowedAction(
                kind="supply_information",
                label="修改每周学习时间",
                targetRef="/pages/study-preference/index",
                requiresConfirmation=False,
            ),
        ),
        "preview_weekly_schedule": preview_weekly_schedule,
        "open_weekly_plan": lambda arguments: no_arguments(
            arguments,
            action=CompanionAllowedAction(
                kind="supply_information",
                label="查看本周计划",
                targetRef="/pages/weekly-plan/index",
                requiresConfirmation=False,
            ),
        ),
        "view_learning_evidence": lambda arguments: no_arguments(
            arguments,
            action=CompanionAllowedAction(
                kind="view_evidence",
                label="查看这次回答的学习依据",
                targetRef=None,
                requiresConfirmation=False,
            ),
        ),
        "open_position_goal": open_position_goal,
    }
    runtime = BoundedToolRuntime(
        definition=COMPANION_AGENT_DEFINITION,
        handlers=handlers,
        event_sink=events.append,
    )
    actions: list[CompanionAllowedAction] = []
    for request in requests[: COMPANION_AGENT_DEFINITION.budget.max_tool_calls]:
        try:
            result = runtime.execute(
                tool_name=request.name,
                arguments=request.arguments,
            )
            action = CompanionAllowedAction.model_validate(result.payload)
        except (AgentRuntimeViolation, PermissionError, ValidationError, ValueError):
            continue
        if action not in actions:
            actions.append(action)
    if not actions:
        actions = list(fallback_actions)
    return CompanionActionOutcome(
        actions=tuple(actions[:3]),
        events=tuple(events),
        requested_count=len(requests),
        succeeded_count=runtime.tool_call_count,
    )


def default_companion_actions(
    *,
    answer_type: str,
    question_text: str,
) -> list[CompanionAllowedAction]:
    weekly_schedule = requested_weekly_schedule(question_text)
    if weekly_schedule is not None:
        return [_weekly_schedule_action(weekly_schedule)]
    normalized = "".join(question_text.lower().split())
    if "学习证据" in normalized or "能力证据" in normalized:
        return [
            CompanionAllowedAction(
                kind="view_evidence",
                label="查看这次回答的学习依据",
                targetRef=None,
                requiresConfirmation=False,
            )
        ]
    if "周计划" in normalized or "本周安排" in normalized:
        return [
            CompanionAllowedAction(
                kind="supply_information",
                label="查看本周计划",
                targetRef="/pages/weekly-plan/index",
                requiresConfirmation=False,
            )
        ]
    if any(token in normalized for token in ("报名资料", "资格档案", "报考资格")):
        return [
            CompanionAllowedAction(
                kind="supply_information",
                label="修改报名资格资料",
                targetRef="/pages/onboarding/profile/index",
                requiresConfirmation=False,
            )
        ]
    if any(token in normalized for token in ("学习偏好", "每周学习时间", "周节奏")):
        return [
            CompanionAllowedAction(
                kind="supply_information",
                label="修改每周学习时间",
                targetRef="/pages/study-preference/index",
                requiresConfirmation=False,
            )
        ]
    if any(token in normalized for token in ("我的档案", "个人档案", "个人资料")):
        return [
            CompanionAllowedAction(
                kind="supply_information",
                label="查看个人档案",
                targetRef="/pages/profile/archive/index",
                requiresConfirmation=False,
            )
        ]
    if answer_type == "position_explanation":
        return [
            CompanionAllowedAction(
                kind="open_position_workspace",
                label="查看岗位判断",
                targetRef="/pages/positions/index",
                requiresConfirmation=False,
            )
        ]
    if answer_type in {"plan_explanation", "strategic_deprioritization"}:
        return [_capacity_action(requested_plan_minutes(question_text))]
    return [
        CompanionAllowedAction(
            kind="open_training",
            label="查看今日训练安排",
            targetRef="/pages/today/index",
            requiresConfirmation=False,
        )
    ]


def requested_plan_minutes(question_text: str) -> int | None:
    """Extract one explicit same-day capacity request without guessing intent."""

    normalized = "".join(question_text.lower().split())
    if not normalized:
        return None
    if any(token in normalized for token in ("今天不学", "今晚不学", "今天休息", "今晚休息")):
        return 0

    hour_and_minute_match = re.search(
        r"(?<!\d)(\d{1,2})个?小时(\d{1,2})分钟",
        normalized,
    )
    if hour_and_minute_match is not None:
        return _valid_plan_minutes(
            int(hour_and_minute_match.group(1)) * 60 + int(hour_and_minute_match.group(2))
        )
    hour_and_half_match = re.search(r"(?<!\d)(\d{1,2})个?小时半", normalized)
    if hour_and_half_match is not None:
        return _valid_plan_minutes(int(hour_and_half_match.group(1)) * 60 + 30)
    mixed_hour_match = re.search(r"(?<!\d)(\d{1,2})个?半小时", normalized)
    if mixed_hour_match is not None:
        return _valid_plan_minutes(int(mixed_hour_match.group(1)) * 60 + 30)
    minute_match = re.search(r"(?<!\d)(\d{1,4})分钟", normalized)
    if minute_match is not None:
        return _valid_plan_minutes(int(minute_match.group(1)))
    hour_match = re.search(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)个?小时", normalized)
    if hour_match is not None:
        minutes = float(hour_match.group(1)) * 60
        return _valid_plan_minutes(int(minutes)) if minutes.is_integer() else None
    chinese_hour_and_half = {
        "一小时半": 90,
        "一个小时半": 90,
        "两小时半": 150,
        "两个小时半": 150,
        "三小时半": 210,
    }
    for phrase, minutes in chinese_hour_and_half.items():
        if phrase in normalized:
            return minutes
    chinese_hours = {
        "半小时": 30,
        "一小时": 60,
        "一个小时": 60,
        "两小时": 120,
        "两个小时": 120,
        "二小时": 120,
        "三小时": 180,
        "四小时": 240,
        "五小时": 300,
        "六小时": 360,
        "七小时": 420,
        "八小时": 480,
        "九小时": 540,
        "十小时": 600,
    }
    for phrase, minutes in chinese_hours.items():
        if phrase in normalized:
            return minutes
    return None


def requested_weekly_schedule(question_text: str) -> WeeklyScheduleRequest | None:
    """Extract explicit mock/recovery weekdays without inventing missing fields."""

    normalized = "".join(question_text.lower().split())
    if not normalized:
        return None
    aliases: dict[str, Weekday] = {
        "一": "monday",
        "1": "monday",
        "二": "tuesday",
        "2": "tuesday",
        "三": "wednesday",
        "3": "wednesday",
        "四": "thursday",
        "4": "thursday",
        "五": "friday",
        "5": "friday",
        "六": "saturday",
        "6": "saturday",
        "日": "sunday",
        "天": "sunday",
        "7": "sunday",
    }
    rest_day: Weekday | None = None
    mock_day: Weekday | None = None
    weekday_pattern = r"(?:周|星期)([一二三四五六日天1-7])"
    purpose_pattern = r"(模考|整卷|模拟考试|休息|恢复|不学)"
    for segment in re.split(r"[,，。；;]", normalized):
        if (
            len(re.findall(weekday_pattern, segment)) == 1
            and any(token in segment for token in ("模考", "整卷", "模拟考试"))
            and any(token in segment for token in ("休息", "恢复", "不学"))
        ):
            return None
    for match in re.finditer(
        rf"{weekday_pattern}[^,，。；;]{{0,8}}?{purpose_pattern}",
        normalized,
    ):
        weekday_token, purpose = match.group(1), match.group(2)
        weekday = aliases[weekday_token]
        if purpose in {"模考", "整卷", "模拟考试"}:
            mock_day = weekday
        else:
            rest_day = weekday
    for match in re.finditer(
        rf"{purpose_pattern}[^,，。；;]{{0,8}}?{weekday_pattern}",
        normalized,
    ):
        purpose, weekday_token = match.group(1), match.group(2)
        weekday = aliases[weekday_token]
        if purpose in {"模考", "整卷", "模拟考试"}:
            if mock_day is None:
                mock_day = weekday
        elif rest_day is None:
            rest_day = weekday
    if rest_day is None and mock_day is None:
        return None
    if rest_day is not None and rest_day == mock_day:
        return None
    return WeeklyScheduleRequest(fixed_rest_day=rest_day, mock_exam_day=mock_day)


def requested_position_goal(question_text: str, position_id: str) -> bool:
    normalized = question_text.lower()
    return position_id.lower() in normalized and any(
        token in normalized for token in ("目标岗位", "主目标", "设为目标", "定为目标")
    )


def is_capacity_change_request(question_text: str) -> bool:
    normalized = "".join(question_text.lower().split())
    if not any(scope in normalized for scope in ("今天", "今晚", "今日")):
        return False
    if normalized.startswith(("为什么", "怎么会", "是不是", "为啥")):
        return False
    if requested_plan_minutes(normalized) is None:
        return False
    return any(
        token in normalized
        for token in (
            "调整",
            "改成",
            "重新安排",
            "安排一下",
            "只能学",
            "只有",
            "能学",
            "可学",
            "休息",
            "不学",
        )
    )


def format_requested_minutes(value: int) -> str:
    if value == 0:
        return "今天休息"
    if value % 60 == 0:
        return f"{value // 60} 小时"
    return f"{value} 分钟"


def _capacity_action(value: int | None) -> CompanionAllowedAction:
    target_ref = "/pages/day-adjustment/index"
    if value is not None:
        target_ref = f"{target_ref}?{urlencode({'source': 'anan', 'availableMinutes': value})}"
    return CompanionAllowedAction(
        kind="adjust_plan_preview",
        label=(
            f"预览调整为 {format_requested_minutes(value)}"
            if value is not None
            else "预览今日计划调整"
        ),
        targetRef=target_ref,
        requiresConfirmation=True,
    )


def _weekly_schedule_action(request: WeeklyScheduleRequest) -> CompanionAllowedAction:
    params: dict[str, str] = {"source": "anan"}
    labels: list[str] = []
    if request.mock_exam_day is not None:
        params["mockExamDay"] = request.mock_exam_day
        labels.append(f"{_weekday_label(request.mock_exam_day)}模考")
    if request.fixed_rest_day is not None:
        params["fixedRestDay"] = request.fixed_rest_day
        labels.append(f"{_weekday_label(request.fixed_rest_day)}恢复")
    return CompanionAllowedAction(
        kind="supply_information",
        label=f"预览周节奏：{' / '.join(labels)}",
        targetRef=f"/pages/study-preference/index?{urlencode(params)}",
        requiresConfirmation=True,
    )


def _weekday_label(value: Weekday) -> str:
    return {
        "monday": "周一",
        "tuesday": "周二",
        "wednesday": "周三",
        "thursday": "周四",
        "friday": "周五",
        "saturday": "周六",
        "sunday": "周日",
    }[value]


def _valid_plan_minutes(value: int) -> int | None:
    return value if 0 <= value <= 1440 else None
