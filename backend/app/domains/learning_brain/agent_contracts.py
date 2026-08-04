"""中控大脑策略指令的 AI 契约。

策略指令（strategy directive）是中控大脑决策 agent 的产出：它表达「本周
该怎么学」的策略意图（focus / cause_direction / essay_emphasis /
mock_schedule / rationale），以及可被现有计划引擎执行的取舍决策 action。
本模块只定义 agent 与 provider 之间的 frozen 契约，与学习画像的 profile
schema（schemas.py）分开存放，镜像 cause_agent/contracts.py 的职责边界。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


LEARNING_BRAIN_DIRECTIVE_CONTRACT_VERSION = "weekly-strategy-directive@v1"
LEARNING_BRAIN_AGENT_EVIDENCE_VERSION = "learning-brain-agent-evidence@v1"


class DirectiveModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class LearningBrainStrategyAction(DirectiveModel):
    """现有计划引擎可执行的取舍决策。

    只支持三种既有 decision_type（exam_skip / phase_deprioritize /
    strategic_abandon），scope 限定 track 或 module。模型只选方向，
    可复现校验（scope 必须命中画像里存在且有证据的模块）由服务端完成。
    """

    decision_type: Literal["exam_skip", "phase_deprioritize", "strategic_abandon"]
    scope_type: Literal["track", "module"]
    scope_key: str = Field(min_length=1, max_length=160)


class LearningBrainStrategyInstruction(DirectiveModel):
    """中控大脑产出的一周策略指令。

    focus / cause_direction / essay_emphasis / mock_schedule 描述「本周侧重
    什么」，action 描述为侧重腾出空间的取舍（必选：本周侧重一个模块，就必须
    明确降权/放弃另一个，否则没有可执行的取舍）。rationale 是面向用户的人话
    解释，展示给用户并随候选落库可追溯。
    """

    focus: str = Field(min_length=1, max_length=160)
    cause_direction: str = Field(min_length=1, max_length=80)
    essay_emphasis: str = Field(min_length=1, max_length=240)
    mock_schedule: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=4, max_length=1000)
    action: LearningBrainStrategyAction


class LearningBrainStrategyEnvelope(DirectiveModel):
    contract_version: Literal["weekly-strategy-directive@v1"] = Field(
        default=LEARNING_BRAIN_DIRECTIVE_CONTRACT_VERSION,
        alias="contractVersion",
    )
    output: LearningBrainStrategyInstruction


class LearningBrainAgentEvidence(DirectiveModel):
    """喂给决策 agent 的全量学习状态快照（只读投影，脱敏后外发）。

    镜像 cause_agent 的 evidence 结构：冻结各证据段，provider 只基于
    evidence 判断，不得编造。不携带任何账户/存储标识（userId、token 等）。
    """

    ability: dict
    modules: list[dict]
    error_patterns: list[dict]
    knowledge_point_weaknesses: list[dict]
    goal_gap: dict
    weekly_rhythm: dict
    week: dict

    @property
    def module_keys(self) -> set[str]:
        return {
            str(item["moduleKey"])
            for item in self.modules
            if isinstance(item, dict) and item.get("moduleKey")
        }
