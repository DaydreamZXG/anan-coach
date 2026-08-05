from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import re
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.domains.ilink_channel.repository import ILinkRepository
from app.domains.ilink_channel.plan_projection import today_plan_reply
from app.domains.learning_brain.service import (
    LearningBrainContractError,
    LearningBrainService,
)
from app.domains.onboarding.progress_repository import OnboardingProgressError
from app.domains.onboarding.progress_service import OnboardingProgressService
from app.domains.companion.agent import (
    is_capacity_change_request,
    requested_plan_focus,
    requested_plan_minutes,
)
from app.domains.planning.errors import PlanningContractError
from app.domains.planning.schemas import DailyCapacityCommand, TodayDashboard
from app.domains.planning.service import PlanningService
from app.domains.planning.insights_service import PlanningInsightsService
from app.domains.private_knowledge.repository import PrivateKnowledgeRepository
from app.domains.private_knowledge.projection import build_projection_gateway
from app.domains.private_knowledge.service import PrivateKnowledgeService
from app.domains.qualification.repository import PositionRuleReleaseUnavailable


HELP_COMMANDS = frozenset({"主控台", "帮助", "岸岸帮助", "你能做什么"})
PLAN_COMMANDS = frozenset({"今天", "今天练什么", "今天学什么", "今日安排"})
NEXT_COMMANDS = frozenset({"下一步", "接下来做什么", "现在做什么"})
PROGRESS_COMMANDS = frozenset({"进度", "今日进度", "计划进度"})
MATERIAL_LIST_COMMANDS = frozenset({"我的资料", "资料", "资料库"})
PROFILE_COMMANDS = frozenset({"我的档案", "个人档案", "报考档案"})
POSITION_GOAL_COMMANDS = frozenset({"岗位目标", "目标岗位", "我的岗位"})
WEEKLY_PLAN_COMMANDS = frozenset({"周计划", "本周计划", "这周怎么学"})
LEARNING_EVIDENCE_COMMANDS = frozenset({"学习证据", "能力证据", "我的学习记录"})
_CAPACITY_RE = re.compile(
    r"^(?:调整今天|今天(?:能)?学|今天(?:可)?学习)\s*(\d+(?:\.\d+)?)\s*(小时|分钟)$"
)
_MATERIAL_SEARCH_RE = re.compile(r"^查资料\s+(.+)$")
_MATERIAL_DELETE_RE = re.compile(r"^删除资料\s+(.+)$")
_MATERIAL_CONFIRM_RE = re.compile(r"^确认删除资料\s+([0-9]{6})$")
_DELETE_CONFIRM_TTL = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class ControlPlaneResult:
    reply: str
    mutation_applied: bool = False
    requires_confirmation: bool = False


class ILinkControlPlane:
    def __init__(
        self,
        *,
        planning: PlanningService | None = None,
        knowledge_factory: Callable[[Session], PrivateKnowledgeService] | None = None,
        receipt_factory: Callable[[Session], ILinkRepository] | None = None,
        onboarding_result_factory: Callable[[Session, str], object] | None = None,
        weekly_plan_factory: Callable[[Session, str], object] | None = None,
        learning_brain_factory: Callable[[Session, str], object] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._planning = planning or PlanningService()
        self._knowledge_factory = knowledge_factory or (
            lambda session: PrivateKnowledgeService(
                PrivateKnowledgeRepository(session),
                projection_gateway=build_projection_gateway(session),
            )
        )
        self._receipt_factory = receipt_factory or ILinkRepository
        self._onboarding_result_factory = onboarding_result_factory or (
            lambda session, user_id: OnboardingProgressService().get_result_projection(
                session,
                user_id=user_id,
            )
        )
        self._weekly_plan_factory = weekly_plan_factory or (
            lambda session, user_id: PlanningInsightsService().weekly_plan(
                session,
                user_id=user_id,
            )
        )
        self._learning_brain_factory = learning_brain_factory or (
            lambda session, user_id: LearningBrainService().get_or_create_profile(
                session,
                user_id=user_id,
            )
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def handle(
        self,
        session: Session,
        *,
        user_id: UUID,
        source_message_id: str,
        text: str,
    ) -> ControlPlaneResult | None:
        command = text.strip()
        if command in HELP_COMMANDS:
            return ControlPlaneResult(_help_reply())
        if command in PLAN_COMMANDS:
            try:
                dashboard = self._today(session, user_id)
            except PlanningContractError:
                return ControlPlaneResult(
                    today_plan_reply(session, user_id=user_id, now=self._clock())
                )
            return ControlPlaneResult(_plan_reply(dashboard))
        if command in NEXT_COMMANDS:
            try:
                dashboard = self._today(session, user_id)
            except PlanningContractError:
                return ControlPlaneResult(_planning_setup_reply())
            return ControlPlaneResult(_next_reply(dashboard))
        if command in PROGRESS_COMMANDS:
            try:
                dashboard = self._today(session, user_id)
            except PlanningContractError:
                return ControlPlaneResult(_planning_setup_reply())
            return ControlPlaneResult(_progress_reply(dashboard))
        if command in PROFILE_COMMANDS:
            return ControlPlaneResult(self._profile_reply(session, user_id))
        if command in POSITION_GOAL_COMMANDS:
            return ControlPlaneResult(self._position_goal_reply(session, user_id))
        if command in WEEKLY_PLAN_COMMANDS:
            return ControlPlaneResult(self._weekly_plan_reply(session, user_id))
        if command in LEARNING_EVIDENCE_COMMANDS:
            return ControlPlaneResult(self._learning_evidence_reply(session, user_id))
        capacity = _CAPACITY_RE.fullmatch(command)
        natural_minutes = (
            requested_plan_minutes(command) if is_capacity_change_request(command) else None
        )
        natural_focus = requested_plan_focus(command) if is_capacity_change_request(command) else None
        if capacity is not None or natural_minutes is not None or natural_focus is not None:
            minutes = (
                _capacity_minutes(capacity.group(1), capacity.group(2))
                if capacity is not None
                else natural_minutes
            )
            if capacity is not None and minutes is None:
                return ControlPlaneResult(
                    "今天可学时间需在 0 到 24 小时之间，例如「调整今天 10小时」。"
                )
            try:
                current = self._today(session, user_id)
            except PlanningContractError:
                return ControlPlaneResult(_planning_setup_reply())
            result = self._planning.set_today_capacity(
                session,
                user_id=str(user_id),
                command_id=f"ilink-capacity:{source_message_id}",
                command=DailyCapacityCommand(
                    basedOnCapacityVersionId=str(current.capacity.capacity_version_id),
                    availableMinutes=(
                        minutes if minutes is not None else current.capacity.available_minutes
                    ),
                    energy=current.capacity.energy,
                    stress=current.capacity.stress,
                    confidenceLevel=current.capacity.confidence_level,
                    preferredXingceModule=(
                        natural_focus.module
                        if natural_focus is not None
                        else current.capacity.preferred_xingce_module
                    ),
                    xingceOnly=(
                        natural_focus.xingce_only
                        if natural_focus is not None
                        else current.capacity.xingce_only
                    ),
                ),
                now=self._clock(),
            )
            changes: list[str] = []
            if minutes is not None:
                changes.append(f"可学时间调整为 {_minutes_label(minutes)}")
            if natural_focus is not None:
                changes.append(
                    (
                        f"今天只练{natural_focus.module}"
                        if natural_focus.xingce_only
                        else f"今天优先练{natural_focus.module}"
                    )
                )
            return ControlPlaneResult(
                f"已{'，'.join(changes)}。\n{_plan_reply(result.plan)}",
                mutation_applied=True,
            )
        if command in MATERIAL_LIST_COMMANDS:
            return ControlPlaneResult(self._material_list(session, user_id))
        search = _MATERIAL_SEARCH_RE.fullmatch(command)
        if search is not None:
            return ControlPlaneResult(
                self._material_search(session, user_id, search.group(1).strip())
            )
        delete = _MATERIAL_DELETE_RE.fullmatch(command)
        if delete is not None:
            return self._request_material_delete(
                session,
                user_id=user_id,
                source_message_id=source_message_id,
                target=delete.group(1).strip(),
            )
        confirm = _MATERIAL_CONFIRM_RE.fullmatch(command)
        if confirm is not None:
            return self._confirm_material_delete(
                session,
                user_id=user_id,
                source_message_id=source_message_id,
                code=confirm.group(1),
            )
        if command == "取消删除":
            return ControlPlaneResult("已取消。未删除任何资料。")
        return None

    def _today(self, session: Session, user_id: UUID) -> TodayDashboard:
        return self._planning.get_or_create_today(
            session,
            user_id=str(user_id),
            now=self._clock(),
        )

    def _material_list(self, session: Session, user_id: UUID) -> str:
        sources = self._knowledge_factory(session).list(owner_user_id=str(user_id)).sources
        if not sources:
            return "你的资料库还是空的。发送讲义或笔记图片后回复「存资料」即可收进来。"
        lines = [
            f"{index}. {source.title}（{source.page_count} 页）"
            for index, source in enumerate(sources[:10], 1)
        ]
        suffix = f"\n另有 {len(sources) - 10} 份未展开。" if len(sources) > 10 else ""
        return "你的资料：\n" + "\n".join(lines) + suffix + "\n可发送「查资料 关键词」。"

    def _profile_reply(self, session: Session, user_id: UUID) -> str:
        try:
            result = self._onboarding_result_factory(session, str(user_id))
        except (
            OnboardingProgressError,
            LearningBrainContractError,
            PositionRuleReleaseUnavailable,
        ):
            session.rollback()
            return "报考档案这次没有读取完成。请到小程序「我的」里打开档案重试。"
        goal = getattr(result, "primary_goal", None)
        preference = getattr(result, "weekly_preference", None)
        lines = [f"档案状态：{_onboarding_status_label(str(result.status))}。"]
        if goal is not None:
            lines.append(f"主目标：{goal.department_name} · {goal.position_name}。")
        else:
            lines.append("主目标：尚未确认，不影响继续学习。")
        if preference is not None:
            lines.append(
                "常规时间："
                f"工作日 {_minutes_label(preference.weekday_minutes)}，"
                f"周末 {_minutes_label(preference.weekend_minutes)}。"
            )
        lines.append("修改资格事实或学习时间时，请在小程序档案页确认后保存。")
        return "\n".join(lines)

    def _position_goal_reply(self, session: Session, user_id: UUID) -> str:
        try:
            result = self._onboarding_result_factory(session, str(user_id))
        except (
            OnboardingProgressError,
            LearningBrainContractError,
            PositionRuleReleaseUnavailable,
        ):
            session.rollback()
            return "岗位目标这次没有读取完成。请到小程序「岗位决策台」刷新。"
        goal = getattr(result, "primary_goal", None)
        if goal is None:
            return (
                "你还没有确认主目标岗位。这不会阻塞学习；"
                "可以在小程序「岗位决策台」查看历史参照和可选岗位。"
            )
        score = goal.target_score
        return (
            f"当前主目标：{goal.department_name} · {goal.position_name}\n"
            f"目标分区间：{score.lower_bound:g}–{score.upper_bound:g} 分，"
            f"建议值 {score.recommended:g} 分。\n"
            "更换主目标会在小程序先展示对计划的影响，确认后才生效。"
        )

    def _weekly_plan_reply(self, session: Session, user_id: UUID) -> str:
        try:
            weekly = self._weekly_plan_factory(session, str(user_id))
        except (PlanningContractError, PositionRuleReleaseUnavailable):
            session.rollback()
            return "周计划还不能生成。请先在小程序确认每周学习时间。"
        lines = []
        for day in weekly.days:
            lines.append(
                f"{_date_weekday_label(day.local_date)}：{_weekly_mode_label(day.mode)} · "
                f"{day.scheduled_minutes} 分钟 · {day.completed_task_count}/{day.total_task_count} 完成"
            )
        return f"本周 {weekly.rhythm} 节奏：\n" + "\n".join(lines)

    def _learning_evidence_reply(self, session: Session, user_id: UUID) -> str:
        try:
            profile = self._learning_brain_factory(session, str(user_id))
        except (LearningBrainContractError, PositionRuleReleaseUnavailable):
            session.rollback()
            return "学习证据这次没有汇总完成。请到小程序「能力」页刷新。"
        evidence = profile.evidence_summary
        xingce_modules = sum(item.track == "xingce" for item in profile.modules)
        essay_modules = sum(item.track == "essay" for item in profile.modules)
        return (
            f"学习证据状态：{_brain_readiness_label(profile.readiness)}\n"
            f"行测作答 {evidence.xingce_attempt_count} 份，行测掌握证据 "
            f"{evidence.xingce_mastery_count} 条，申论掌握证据 "
            f"{evidence.essay_mastery_count} 条，计划执行 {evidence.task_execution_count} 条。\n"
            f"当前可解释模块：行测 {xingce_modules} 个，申论 {essay_modules} 个；"
            f"待确认判断 {profile.candidate_summary.awaiting_confirmation} 条。"
        )

    def _material_search(self, session: Session, user_id: UUID, query: str) -> str:
        result = self._knowledge_factory(session).search(
            owner_user_id=str(user_id), query=query, maximum_sources=5
        )
        if not result.citations:
            return f"没有在你的资料里找到“{query}”。"
        lines = []
        for citation in result.citations[:5]:
            excerpt = " ".join(citation.excerpt.split())
            lines.append(f"• {citation.title} · 第 {citation.page_no} 页\n{excerpt[:160]}")
        return f"在你的资料里找到 {len(result.citations)} 处：\n" + "\n".join(lines)

    def _request_material_delete(
        self,
        session: Session,
        *,
        user_id: UUID,
        source_message_id: str,
        target: str,
    ) -> ControlPlaneResult:
        sources = self._knowledge_factory(session).list(owner_user_id=str(user_id)).sources
        source = _resolve_source(sources, target)
        if source is None:
            return ControlPlaneResult("没有找到这份资料。发送「我的资料」查看编号和标题。")
        code = _confirmation_code(user_id, source.source_id, source_message_id)
        now = self._clock()
        repository = self._receipt_factory(session)
        command_id = f"ilink-material-delete-pending:{source_message_id}"
        if repository.command_receipt(command_id=command_id) is None:
            repository.save_command_receipt(
                command_id=command_id,
                command_type="control_material_delete_pending",
                user_id=user_id,
                request_fingerprint=sha256(target.encode("utf-8")).hexdigest(),
                result_payload={
                    "sourceId": str(source.source_id),
                    "title": source.title,
                    "code": code,
                    "expiresAt": (now + _DELETE_CONFIRM_TTL).isoformat(),
                },
                now=now,
            )
        return ControlPlaneResult(
            f"准备删除资料《{source.title}》。此操作会删除它的全部版本。\n"
            f"10 分钟内发送「确认删除资料 {code}」才会执行；发送「取消删除」可取消。",
            requires_confirmation=True,
        )

    def _confirm_material_delete(
        self,
        session: Session,
        *,
        user_id: UUID,
        source_message_id: str,
        code: str,
    ) -> ControlPlaneResult:
        repository = self._receipt_factory(session)
        pending = repository.pending_material_delete(user_id=user_id, code=code)
        if pending is None:
            return ControlPlaneResult("这个删除确认不存在或已经使用。请重新发送「删除资料 编号」。")
        payload = dict(pending["result_payload"])
        expires_at = datetime.fromisoformat(str(payload["expiresAt"]))
        if self._clock() > expires_at:
            return ControlPlaneResult("这个删除确认已过期，未删除任何资料。请重新发起删除。")
        pending_id = str(pending["command_id"])
        if repository.material_delete_consumed(pending_command_id=pending_id):
            return ControlPlaneResult("这个删除确认已经使用，未重复执行。")
        deletion = self._knowledge_factory(session).request_delete(
            owner_user_id=str(user_id),
            source_id=UUID(str(payload["sourceId"])),
            idempotency_key=f"ilink-material-delete:{pending_id}",
        )
        repository.save_command_receipt(
            command_id=f"ilink-material-delete-confirm:{source_message_id}",
            command_type="control_material_delete_consumed",
            user_id=user_id,
            request_fingerprint=sha256(pending_id.encode("utf-8")).hexdigest(),
            result_payload={
                "pendingCommandId": pending_id,
                "deletionRequestId": str(deletion.deletion_request_id),
            },
            now=self._clock(),
        )
        return ControlPlaneResult(
            f"已受理删除《{payload['title']}》，正在清理它的全部版本。",
            mutation_applied=True,
        )


def _help_reply() -> str:
    return (
        "这里是岸岸主控台。你可以发送：\n"
        "• 今天 / 下一步 / 进度\n"
        "• 调整今天 10小时（或 600分钟）\n"
        "• 我的资料 / 查资料 关键词\n"
        "• 我的档案 / 岗位目标 / 本周计划 / 学习证据\n"
        "• 删除资料 编号（会再次确认）\n"
        "也可以直接问学习问题，或发送题目、讲义图片。"
    )


def _planning_setup_reply() -> str:
    return (
        "还缺少目标或学习安排。先在小程序完成目标与常规学习时间，之后就能直接在这里控制今日计划。"
    )


def _plan_reply(dashboard: TodayDashboard) -> str:
    if not dashboard.tasks:
        return f"{dashboard.headline}\n今天暂时没有可执行任务，可发送「调整今天 120分钟」重新安排。"
    lines = [
        f"{task.priority}. {task.title} · {task.estimated_minutes} 分钟 · {_status_label(task.status)}"
        for task in dashboard.tasks[:8]
    ]
    remaining = sum(
        task.estimated_minutes
        for task in dashboard.tasks
        if task.status in {"ready", "in_progress"}
    )
    return (
        f"{dashboard.headline}\n今日 {len(dashboard.tasks)} 个任务，剩余约 {remaining} 分钟：\n"
        + "\n".join(lines)
    )


def _next_reply(dashboard: TodayDashboard) -> str:
    task = next((item for item in dashboard.tasks if item.status == "in_progress"), None)
    task = task or next((item for item in dashboard.tasks if item.status == "ready"), None)
    if task is None:
        return "今天的计划已经没有待执行任务了。发送「进度」可以查看汇总。"
    return (
        f"下一步：{task.title}\n预计 {task.estimated_minutes} 分钟。\n"
        f"完成标准：{task.completion_rule}\n打开小程序「今天」页即可开始。"
    )


def _progress_reply(dashboard: TodayDashboard) -> str:
    total = len(dashboard.tasks)
    complete = sum(task.status == "complete" for task in dashboard.tasks)
    active = sum(task.status == "in_progress" for task in dashboard.tasks)
    remaining = sum(
        task.estimated_minutes
        for task in dashboard.tasks
        if task.status in {"ready", "in_progress"}
    )
    return f"今日进度：已完成 {complete}/{total}，进行中 {active}，剩余约 {remaining} 分钟。"


def _capacity_minutes(value: str, unit: str) -> int | None:
    minutes = round(float(value) * 60) if unit == "小时" else round(float(value))
    if minutes < 0 or minutes > 1440:
        return None
    return minutes


def _minutes_label(minutes: int) -> str:
    if minutes % 60 == 0:
        return f"{minutes // 60} 小时"
    return f"{minutes} 分钟"


def _onboarding_status_label(status: str) -> str:
    return {
        "evidence_sufficient": "档案与双科证据已齐",
        "baseline_required": "还需补充能力证据",
        "position_selection_required": "资格档案已就绪，主目标待选",
        "no_eligible_position": "当前无可确认岗位，可继续学习",
    }.get(status, "待刷新")


def _weekly_mode_label(mode: str) -> str:
    return {"training": "训练", "mock": "整卷模考", "recovery": "恢复"}.get(
        mode,
        mode,
    )


def _date_weekday_label(value) -> str:
    return f"{value.month}月{value.day}日 周{'一二三四五六日'[value.isoweekday() - 1]}"


def _brain_readiness_label(readiness: str) -> str:
    return {
        "ready": "可用",
        "partial": "部分可用",
        "stale": "需更新",
        "conflicted": "有冲突待处理",
        "unavailable": "尚无证据",
    }.get(readiness, "待刷新")


def _status_label(status: str) -> str:
    return {
        "ready": "待开始",
        "in_progress": "进行中",
        "complete": "已完成",
        "abandoned": "已放弃",
    }.get(status, status)


def _resolve_source(sources, target: str):
    if target.isdigit():
        index = int(target) - 1
        return sources[index] if 0 <= index < len(sources) else None
    exact = [source for source in sources if source.title == target]
    return exact[0] if len(exact) == 1 else None


def _confirmation_code(user_id: UUID, source_id: UUID, message_id: str) -> str:
    digest = sha256(f"{user_id}:{source_id}:{message_id}".encode("utf-8")).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"
