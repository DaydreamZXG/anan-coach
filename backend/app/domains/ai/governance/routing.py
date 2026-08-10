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
        {
            "position",
            "question",
            "external_question",
            "material",
            "attempt",
            "plan",
            "report",
            "error_pattern",
        },
    ),
    "pages/companion/index": (
        {"today", "training", "position", "profile"},
        {
            "position",
            "question",
            "external_question",
            "material",
            "attempt",
            "plan",
            "report",
        },
    ),
    "pages/today/index": ({"today"}, {"plan", "report"}),
    "plans/plan-basis/index": ({"today"}, {"plan", "report"}),
    "plans/weekly-plan/index": ({"today"}, {"plan", "report"}),
    "plans/daily-settlement/index": ({"today"}, {"plan", "report"}),
    "pages/ability/index": ({"profile"}, {"report", "plan", "attempt"}),
    "pages/positions/index": ({"position"}, {"position", "report"}),
    "pages/positions/detail/index": ({"position"}, {"position", "report"}),
    "pages/positions/goal/index": ({"position"}, {"position", "report"}),
    "pages/practice/index": ({"training"}, {"question", "attempt", "material"}),
    "pages/essay-task/index": ({"training"}, {"question", "attempt", "material"}),
    "reports/error-pattern/index": (
        {"profile"},
        {"report", "attempt", "error_pattern"},
    ),
    "essay/essay-review/index": ({"training"}, {"question", "attempt", "material"}),
    "pages/capture-import/index": (
        {"training"},
        {"external_question", "attempt", "material"},
    ),
    "pages/reflection/index": ({"training"}, {"question", "attempt", "material"}),
    "reports/report-overview/index": (
        {"training", "profile"},
        {"report", "plan", "attempt"},
    ),
    "reports/mock-report/index": ({"training", "profile"}, {"report", "attempt"}),
    "reports/global-report/index": ({"profile"}, {"report", "plan"}),
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
    "更改",
    "调整",
    "增加",
    "添加",
    "换成",
    "改成",
    "生成一组",
    "练一组",
)
_VISUAL_REASONING_TERMS = ("图形", "图表", "图片", "坐标", "涂画", "曲线", "柱状")
_COMPLEX_REASONING_TERMS = ("推导", "分析思路", "详细讲", "综合比较", "逐步", "论证")
_UNRESOLVED_FOLLOWUP_TERMS = ("没懂", "还是不懂", "再讲", "继续提示", "换个思路")
_REST_TERMS = (
    "累",
    "状态差",
    "休息",
    "熬夜",
    "睡不够",
    "不想学",
    "学不动",
    "撑不住",
    "疲惫",
)
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


def route_global_question(
    *,
    question: str,
    context: GlobalAskContext,
    conversation_turns: int = 0,
) -> IntentDecision:
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
    decision = IntentDecision(
        scenarioKey=policy.scenario_key,
        respondingAgent=policy.responding_agent,
        answerType=answer_type,
        modelTier=policy.model_tier,
        policyDecision="answer",
        companionIntent=companion_intent,
        reasonCodes=(reason,),
    )
    return refine_companion_model_route(
        decision=decision,
        question=normalized,
        conversation_turns=conversation_turns,
    )


def refine_companion_model_route(
    *,
    decision: IntentDecision,
    question: str,
    conversation_turns: int,
    retrieved_texts: tuple[str, ...] = (),
) -> IntentDecision:
    """Select one model before dispatch and persist auditable reason codes.

    This remains a single-provider, single-main-call route.  Frozen source text
    may escalate capability after retrieval, but can never downgrade a policy's
    existing high tier or create an additional model approval pipeline.
    """

    if decision.policy_decision != "answer":
        return decision
    normalized = " ".join(question.strip().split())
    source_text = "\n".join(retrieved_texts)
    normalized_source_text = source_text.casefold()
    reasons = list(decision.reason_codes)
    if any(term in normalized for term in _VISUAL_REASONING_TERMS) or (
        "视觉信息必需：true" in normalized_source_text
    ):
        reasons.append("visual_reasoning_required")
    if len(normalized) >= 120 or len(source_text) >= 3_000:
        reasons.append("long_context_reasoning")
    if any(term in normalized for term in _COMPLEX_REASONING_TERMS):
        reasons.append("complex_reasoning_requested")
    if conversation_turns >= 2 and any(
        term in normalized for term in _UNRESOLVED_FOLLOWUP_TERMS
    ):
        reasons.append("multi_turn_unresolved")
    reasons = list(dict.fromkeys(reasons))[:8]
    capability_reasons = {
        "visual_reasoning_required",
        "long_context_reasoning",
        "complex_reasoning_requested",
        "multi_turn_unresolved",
    }
    model_tier = (
        "high"
        if decision.model_tier == "high" or capability_reasons.intersection(reasons)
        else "small"
    )
    return decision.model_copy(
        update={"model_tier": model_tier, "reason_codes": tuple(reasons)}
    )


def page_permission_matrix() -> dict[str, dict[str, tuple[str, ...]]]:
    return {
        route: {
            "workspaces": tuple(sorted(workspaces)),
            "objectTypes": tuple(sorted(object_types)),
        }
        for route, (workspaces, object_types) in _PAGE_PERMISSIONS.items()
    }
