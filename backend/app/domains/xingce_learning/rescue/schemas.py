from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RescueModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class RescueMethodCard(RescueModel):
    """错题对应考点的已发布方法卡（内容裁剪：识别/方法/易错点）。"""

    card_id: str = Field(alias="cardId")
    title: str
    module_name: str = Field(alias="moduleName")
    tag_path: str = Field(alias="tagPath")
    recognition: str
    method_steps: list[str] = Field(alias="methodSteps")
    pitfall: str | None = None
    fix_action: str = Field(alias="fixAction")
    time_budget_sec: int = Field(alias="timeBudgetSec")


class RescueSimilarOption(RescueModel):
    option_id: str = Field(alias="optionId")
    label: str
    text: str


class RescueSimilarQuestion(RescueModel):
    """按考点从题库选出的同类题（冻结内容，前端直接展示）。"""

    question_id: str = Field(alias="questionId")
    content_version_id: str = Field(alias="contentVersionId")
    module_name: str = Field(alias="moduleName")
    stem: str
    options: list[RescueSimilarOption]


class RescueNodeContext(RescueModel):
    node_id: str = Field(alias="nodeId")
    display_path: str = Field(alias="displayPath")
    module_name: str = Field(alias="moduleName")


class XingceRescueItem(RescueModel):
    """单道错题的救场：错因解释 + 方法卡 + 同类题。"""

    question_id: str = Field(alias="questionId")
    cause_code: str = Field(alias="causeCode")
    cause_label: str = Field(alias="causeLabel")
    explanation: str
    node: RescueNodeContext
    method_card: RescueMethodCard | None = Field(alias="methodCard", default=None)
    similar_question: RescueSimilarQuestion | None = Field(
        alias="similarQuestion",
        default=None,
    )
    teach_back_available: bool = Field(alias="teachBackAvailable", default=False)
    teach_back_reason: str | None = Field(alias="teachBackReason", default=None)


class XingceRescue(RescueModel):
    """做完一组题后的集中救场结果（只读，不打断做题）。"""

    review_id: str = Field(alias="reviewId")
    session_id: str = Field(alias="sessionId")
    items: list[XingceRescueItem] = Field(default_factory=list, max_length=30)
    # 人话解释的汇总兜底：本组没有可救场错因时说明原因。
    summary: str


# 产品决策：只在「方法不当」与「知识缺口」两类错因救场。
RESCUABLE_CAUSE_CODES: frozenset[str] = frozenset(
    {"method_selection", "knowledge_gap"}
)

CAUSE_LABELS: dict[str, str] = {
    "stem_misread": "题干看漏看错",
    "knowledge_gap": "知识点没掌握",
    "method_selection": "方法没选对",
    "calculation_error": "会做但算错了",
    "time_pressure": "时间太紧太仓促",
    "guessing": "凭感觉猜的",
    "active_abandon": "主动放弃这题",
    "other": "都不太像",
}

# 人话解释模板：错因码 -> 句子主干，后面拼考点 display_path 的末段。
_CAUSE_EXPLANATION = {
    "method_selection": "你错在方法选择——{focus}的方法还没完全掌握",
    "knowledge_gap": "你错在知识缺口——{focus}对应的知识点还没吃透",
}

# 每道错题的救场解释上限（超出截断展示，避免刷屏）。
MAX_ITEMS = 3


def cause_label(cause_code: str) -> str:
    return CAUSE_LABELS.get(cause_code, "方法或知识点")


def build_cause_explanation(*, cause_code: str, focus: str) -> str:
    template = _CAUSE_EXPLANATION.get(cause_code)
    if template is None:
        return f"这道题还有可以补的地方——{focus}。"
    return template.format(focus=focus)
