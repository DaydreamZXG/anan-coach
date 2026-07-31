from __future__ import annotations

import re

from app.domains.ai.governance.catalog import scenario_policy
from app.domains.ai.governance.contracts import GlobalAskContext, IntentDecision


class GlobalAskPolicyError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_PAGE_PERMISSIONS: dict[str, tuple[set[str], set[str]]] = {
    "pages/anan/index": (
        {"today", "training", "position", "profile"},
        {"position", "question", "material", "attempt", "plan", "report"},
    ),
    "pages/companion/index": (
        {"today", "training", "position", "profile"},
        {"position", "question", "material", "attempt", "plan", "report"},
    ),
    "pages/today/index": ({"today"}, {"plan", "report"}),
    "pages/plan-basis/index": ({"today"}, {"plan", "report"}),
    "pages/weekly-plan/index": ({"today"}, {"plan", "report"}),
    "pages/daily-settlement/index": ({"today"}, {"plan", "report"}),
    "pages/ability/index": ({"profile"}, {"report", "plan", "attempt"}),
    "pages/positions/index": ({"position"}, {"position", "report"}),
    "pages/positions/detail/index": ({"position"}, {"position", "report"}),
    "pages/positions/goal/index": ({"position"}, {"position", "report"}),
    "pages/practice/index": ({"training"}, {"question", "attempt", "material"}),
    "pages/essay-task/index": ({"training"}, {"question", "attempt", "material"}),
    "pages/error-pattern/index": ({"profile"}, {"report", "attempt"}),
    "pages/essay-review/index": ({"training"}, {"question", "attempt", "material"}),
    "pages/capture-import/index": ({"training"}, {"question", "attempt", "material"}),
    "pages/reflection/index": ({"training"}, {"question", "attempt", "material"}),
    "pages/report-overview/index": (
        {"training", "profile"},
        {"report", "plan", "attempt"},
    ),
    "pages/mock-report/index": ({"training", "profile"}, {"report", "attempt"}),
    "pages/global-report/index": ({"profile"}, {"report", "plan"}),
    "pages/learning-profile/index": ({"profile"}, {"report", "plan", "attempt"}),
}

_INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(all\s+)?(previous|prior|system)\s+instructions?",
        r"reveal\s+(the\s+)?(system|developer)\s+prompt",
        r"you\s+are\s+now\s+(the\s+)?system",
        r"<\s*/?\s*(system|developer|tool)\s*>",
        r"忽略.{0,12}(之前|以上|系统).{0,8}(指令|提示)",
        r"(泄露|输出|展示).{0,12}(系统提示词|开发者指令|密钥|令牌)",
        r"(扮演|切换成).{0,8}(系统|开发者|管理员)",
        r"调用.{0,8}(工具|函数).{0,8}(删除|修改|绕过)",
    )
)

_STRATEGY_TERMS = (
    "放弃",
    "不学",
    "取舍",
    "降权",
    "跳过",
    "战略",
    "别练",
)
_POSITION_TERMS = (
    "岗位",
    "职位",
    "资格",
    "能报",
    "可报",
    "报名",
    "冲稳保",
    "目标分",
    "上岸概率",
)
_CAUSE_TERMS = (
    "为什么错",
    "错因",
    "这题错",
    "做错",
    "失分原因",
)
_PLAN_TERMS = (
    "今天",
    "计划",
    "安排",
    "先做",
    "复测",
    "每天",
    "本周",
    "任务",
)
_ANSWER_LEAK_TERMS = (
    "答案",
    "选哪个",
    "正确选项",
    "参考要点",
    "范文",
    "怎么答",
    "直接写",
)
_REST_TERMS = ("累", "状态差", "休息", "熬夜", "睡不够")
_ENCOURAGEMENT_TERMS = ("进步", "做对了", "完成了", "突破")


def normalize_route_id(value: str) -> str:
    return value.strip().lstrip("/")


def validate_page_permission(context: GlobalAskContext) -> None:
    route = normalize_route_id(context.route_id)
    permission = _PAGE_PERMISSIONS.get(route)
    if permission is None:
        raise GlobalAskPolicyError("page_not_allowlisted")
    workspaces, object_types = permission
    if context.workspace not in workspaces:
        raise GlobalAskPolicyError("workspace_not_allowed_on_page")
    if any(item.type not in object_types for item in context.object_refs):
        raise GlobalAskPolicyError("object_type_not_allowed_on_page")


def prompt_injection_detected(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _INJECTION_PATTERNS)


def route_global_question(*, question: str, context: GlobalAskContext) -> IntentDecision:
    validate_page_permission(context)
    normalized = " ".join(question.strip().split())
    if prompt_injection_detected(normalized):
        policy = scenario_policy("tutoring_explanation")
        return IntentDecision(
            scenarioKey=policy.scenario_key,
            respondingAgent=policy.responding_agent,
            answerType="operation_help",
            modelTier=policy.model_tier,
            policyDecision="security_blocked",
            companionIntent="reminder",
            reasonCodes=("prompt_injection_detected",),
        )

    if context.task_stage in {"reading", "answering"} and any(
        term in normalized for term in _ANSWER_LEAK_TERMS
    ):
        policy = scenario_policy("tutoring_explanation")
        return IntentDecision(
            scenarioKey=policy.scenario_key,
            respondingAgent=policy.responding_agent,
            answerType="operation_help",
            modelTier=policy.model_tier,
            policyDecision="post_submit_only",
            companionIntent="reminder",
            reasonCodes=("answer_leakage_blocked_before_submit",),
        )

    if any(term in normalized for term in _STRATEGY_TERMS):
        policy = scenario_policy("planning_strategic_deprioritization")
        answer_type = "strategic_deprioritization"
        reason = "strategic_language"
    elif any(term in normalized for term in _POSITION_TERMS) or context.workspace == "position":
        policy = scenario_policy("position_explanation")
        answer_type = "position_explanation"
        reason = "position_language_or_workspace"
    elif any(term in normalized for term in _CAUSE_TERMS):
        policy = scenario_policy("tutoring_explanation")
        answer_type = "cause_explanation"
        reason = "cause_language"
    elif any(term in normalized for term in _PLAN_TERMS) or context.workspace == "today":
        policy = scenario_policy("planning_explanation")
        answer_type = "plan_explanation"
        reason = "planning_language_or_workspace"
    else:
        policy = scenario_policy("tutoring_explanation")
        answer_type = "learning_explanation"
        reason = "training_or_profile_default"

    companion_intent = (
        "deprioritize"
        if policy.scenario_key == "planning_strategic_deprioritization"
        else "rest"
        if any(term in normalized for term in _REST_TERMS)
        else "encouragement"
        if any(term in normalized for term in _ENCOURAGEMENT_TERMS)
        else "thinking"
    )
    return IntentDecision(
        scenarioKey=policy.scenario_key,
        respondingAgent=policy.responding_agent,
        answerType=answer_type,
        modelTier=policy.model_tier,
        policyDecision="answer",
        companionIntent=companion_intent,
        reasonCodes=(reason,),
    )


def page_permission_matrix() -> dict[str, dict[str, tuple[str, ...]]]:
    return {
        route: {
            "workspaces": tuple(sorted(workspaces)),
            "objectTypes": tuple(sorted(object_types)),
        }
        for route, (workspaces, object_types) in _PAGE_PERMISSIONS.items()
    }
