from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal
from urllib.parse import urlencode

from app.domains.companion.schemas import CompanionAllowedAction


COMPANION_AGENT_DEFINITION_RELEASE = "companion-action-agent-2026-08-04.2"

XingceModule = Literal[
    "言语理解与表达",
    "数量关系",
    "判断推理",
    "资料分析",
    "常识判断",
    "政治理论",
]
XINGCE_MODULES: tuple[XingceModule, ...] = (
    "言语理解与表达",
    "数量关系",
    "判断推理",
    "资料分析",
    "常识判断",
    "政治理论",
)

Weekday = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


@dataclass(frozen=True)
class WeeklyScheduleRequest:
    fixed_rest_day: Weekday | None = None
    mock_exam_day: Weekday | None = None


@dataclass(frozen=True)
class PlanFocusRequest:
    module: XingceModule
    xingce_only: bool = False


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
                targetRef="/plans/weekly-plan/index",
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
                targetRef="/plans/study-preference/index",
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
        return [
            _capacity_action(
                requested_plan_minutes(question_text),
                requested_plan_focus(question_text),
            )
        ]
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


def requested_plan_focus(question_text: str) -> PlanFocusRequest | None:
    """Extract an explicit same-day Xingce module preference from learner language."""

    normalized = "".join(question_text.lower().split())
    if not normalized or not any(scope in normalized for scope in ("今天", "今晚", "今日")):
        return None
    module = next((item for item in XINGCE_MODULES if item in normalized), None)
    if module is None:
        return None
    exclusive = any(
        token in normalized
        for token in (
            f"只学{module}",
            f"只做{module}",
            f"只练{module}",
            f"专门学{module}",
            f"专练{module}",
        )
    )
    return PlanFocusRequest(module=module, xingce_only=exclusive)


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
    focus = requested_plan_focus(normalized)
    if requested_plan_minutes(normalized) is None and focus is None:
        return False
    if focus is not None:
        return True
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


def _capacity_action(
    value: int | None,
    focus: PlanFocusRequest | None = None,
) -> CompanionAllowedAction:
    target_ref = "/plans/day-adjustment/index"
    params: dict[str, str | int] = {}
    if value is not None or focus is not None:
        params["source"] = "anan"
    if value is not None:
        params["availableMinutes"] = value
    if focus is not None:
        params["preferredXingceModule"] = focus.module
        params["xingceOnly"] = "1" if focus.xingce_only else "0"
    if params:
        target_ref = f"{target_ref}?{urlencode(params)}"
    if focus is not None:
        label = (
            f"预览今天只练{focus.module}"
            if focus.xingce_only
            else f"预览今天重点练{focus.module}"
        )
        if value is not None:
            label = f"{label} · {format_requested_minutes(value)}"
    else:
        label = (
            f"预览调整为 {format_requested_minutes(value)}"
            if value is not None
            else "预览今日计划调整"
        )
    return CompanionAllowedAction(
        kind="adjust_plan_preview",
        label=label,
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
        targetRef=f"/plans/study-preference/index?{urlencode(params)}",
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
