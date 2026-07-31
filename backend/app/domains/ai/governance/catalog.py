from __future__ import annotations

from dataclasses import dataclass

from app.domains.ai.governance.contracts import AgentKey, ModelTier, ScenarioKey


ROUTE_POLICY_VERSION = "global-ask-route@v1"
PAGE_PERMISSION_POLICY_VERSION = "global-ask-page-permission@v1"
PROMPT_INJECTION_POLICY_VERSION = "retrieval-injection-defense@v1"
AI_RELEASE_GATE_VERSION = "ai-release-gate@v1"


@dataclass(frozen=True)
class ScenarioPolicy:
    scenario_key: ScenarioKey
    responding_agent: AgentKey
    impact_level: str
    model_tier: ModelTier
    prompt_version: str
    schema_version: str
    evaluation_suite_version: str
    entitlement_feature: str | None


_POLICIES: dict[ScenarioKey, ScenarioPolicy] = {
    "position_explanation": ScenarioPolicy(
        scenario_key="position_explanation",
        responding_agent="position",
        impact_level="high",
        model_tier="high",
        prompt_version="companion-grounded@v4",
        schema_version="grounded-answer@v2",
        evaluation_suite_version="stage7-position-tone@v1",
        entitlement_feature="companion_ask",
    ),
    "tutoring_explanation": ScenarioPolicy(
        scenario_key="tutoring_explanation",
        responding_agent="tutor",
        impact_level="medium",
        model_tier="small",
        prompt_version="companion-grounded@v4",
        schema_version="grounded-answer@v2",
        evaluation_suite_version="stage7-tutor-tone@v1",
        entitlement_feature="companion_ask",
    ),
    "xingce_cause_analysis": ScenarioPolicy(
        scenario_key="xingce_cause_analysis",
        responding_agent="tutor",
        impact_level="medium",
        model_tier="small",
        prompt_version="xingce-cause-prompt@v4",
        schema_version="cause-candidate@v2",
        evaluation_suite_version="stage7-xingce-cause@v1",
        entitlement_feature="xingce_cause_analysis",
    ),
    "essay_grounded_review": ScenarioPolicy(
        scenario_key="essay_grounded_review",
        responding_agent="tutor",
        impact_level="high",
        model_tier="high",
        prompt_version="grounded-essay-review-v1",
        schema_version="essay-provider-review-v2",
        evaluation_suite_version="stage7-essay-review@v1",
        entitlement_feature="essay_review",
    ),
    "essay_rubric_compilation": ScenarioPolicy(
        scenario_key="essay_rubric_compilation",
        responding_agent="tutor",
        impact_level="high",
        model_tier="high",
        prompt_version="guokao-essay-rubric-generator-v1+critic-v1",
        schema_version="guokao-essay-rubric-ai-pipeline-v1",
        evaluation_suite_version="stage7-essay-rubric@v1",
        entitlement_feature=None,
    ),
    "planning_explanation": ScenarioPolicy(
        scenario_key="planning_explanation",
        responding_agent="planner",
        impact_level="medium",
        model_tier="small",
        prompt_version="companion-grounded@v4",
        schema_version="grounded-answer@v2",
        evaluation_suite_version="stage7-planning-tone@v1",
        entitlement_feature="companion_ask",
    ),
    "planning_strategic_deprioritization": ScenarioPolicy(
        scenario_key="planning_strategic_deprioritization",
        responding_agent="planner",
        impact_level="high",
        model_tier="high",
        prompt_version="companion-grounded@v4",
        schema_version="grounded-answer@v2",
        evaluation_suite_version="stage7-strategic-deprioritization@v1",
        entitlement_feature="companion_ask",
    ),
    "recognition_printed_ocr": ScenarioPolicy(
        scenario_key="recognition_printed_ocr",
        responding_agent="recognition",
        impact_level="medium",
        model_tier="small",
        prompt_version="recognition-processor-policy@v1",
        schema_version="recognition-processor-output@v1",
        evaluation_suite_version="stage7-ocr-printed@v1",
        entitlement_feature="external_wrong_intake",
    ),
    "recognition_handwriting_ocr": ScenarioPolicy(
        scenario_key="recognition_handwriting_ocr",
        responding_agent="recognition",
        impact_level="high",
        model_tier="high",
        prompt_version="recognition-processor-policy@v1",
        schema_version="recognition-processor-output@v1",
        evaluation_suite_version="stage7-ocr-handwriting@v1",
        entitlement_feature="essay_review",
    ),
    "recognition_score_report": ScenarioPolicy(
        scenario_key="recognition_score_report",
        responding_agent="recognition",
        impact_level="high",
        model_tier="high",
        prompt_version="recognition-processor-policy@v1",
        schema_version="recognition-processor-output@v1",
        evaluation_suite_version="stage7-ocr-score-report@v1",
        entitlement_feature="external_wrong_intake",
    ),
    "external_question_answer": ScenarioPolicy(
        scenario_key="external_question_answer",
        responding_agent="tutor",
        impact_level="high",
        model_tier="high",
        prompt_version="external-question-answer@v1",
        schema_version="external-question-answer-output@v1",
        evaluation_suite_version="stage15-external-question-answer@v1",
        entitlement_feature="photo_explain",
    ),
}


def scenario_policy(scenario_key: ScenarioKey) -> ScenarioPolicy:
    return _POLICIES[scenario_key]


def all_scenario_policies() -> tuple[ScenarioPolicy, ...]:
    return tuple(_POLICIES.values())
