"""「今天练什么」文本查询：planning 当日计划的只读投影摘要。

只读边界：本模块只读取当前计划版本、目标就绪度与当日容量确认状态，
不生成计划、不写任何表、不展示能力数据（分差、掌握度等一律不出现）。
空态文案对齐 planning `_plan_copy` 的三分支（先定目标 / 先确认时长 / 未就绪）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.domains.planning.repository import PlanningRepository
from app.domains.planning.service import SHANGHAI, _goal_gap


PLAN_QUERY_COMMANDS = frozenset({"今天练什么", "今天学什么", "今日安排"})
OPEN_MINI_PROGRAM_HINT = "打开小程序「今天」页查看和调整；微信里只有只读摘要，不改计划。"


def is_plan_query(text: str) -> bool:
    return text.strip() in PLAN_QUERY_COMMANDS


def today_plan_reply(session: Session, *, user_id: UUID, now: datetime) -> str:
    repository = PlanningRepository()
    day = now.astimezone(SHANGHAI).date()
    plan = repository.current_plan(session, user_id=str(user_id), local_date=day)
    if plan is not None:
        tasks = repository.plan_tasks(
            session, plan_version_id=str(plan["plan_version_id"])
        )
        headline = str(plan["headline"])
        if tasks:
            minutes = int(plan["scheduled_minutes"])
            summary = f"{headline}\n今日共 {len(tasks)} 个任务 · 约 {minutes} 分钟。"
        else:
            summary = headline
        return f"{summary}\n{OPEN_MINI_PROGRAM_HINT}"
    goal_gap = _goal_gap(
        repository.goal_and_ability(session, user_id=str(user_id)), now
    )
    capacity = repository.current_capacity(
        session, user_id=str(user_id), local_date=day
    )
    return empty_plan_reply(
        goal_ready=goal_gap.readiness == "complete",
        capacity_confirmed=bool(capacity["confirmed"]) if capacity is not None else False,
    )


def empty_plan_reply(*, goal_ready: bool, capacity_confirmed: bool) -> str:
    if not goal_ready:
        body = (
            "先定目标，今天的安排才有方向。"
            "在小程序「我的」里确认目标岗位后，岸岸会生成真实训练，不用凑数任务填满今天。"
        )
    elif not capacity_confirmed:
        body = (
            "先确认今天能学多久。"
            "在小程序「今天」页点「调整今天」，告诉岸岸可用时间和状态，安排马上生成。"
        )
    else:
        body = (
            "今天的正式安排还没就绪。"
            "到小程序「今天」页刷新即可生成；岸岸不会硬塞不可执行的任务。"
        )
    return f"{body}\n{OPEN_MINI_PROGRAM_HINT}"
