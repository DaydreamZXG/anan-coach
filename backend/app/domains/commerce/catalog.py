from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductConfig:
    product_id: str
    tier: str
    billing_period: str
    title: str
    subtitle: str
    price_band: str
    sort_order: int
    feature_highlights: tuple[str, ...]


@dataclass(frozen=True)
class FeatureQuotaConfig:
    feature: str
    title: str
    period: str
    free_limit: int
    pro_limit: int


PRODUCTS: tuple[ProductConfig, ...] = (
    ProductConfig(
        product_id="gongkao_ai_pro_monthly",
        tier="pro",
        billing_period="monthly",
        title="AI Pro 月度",
        subtitle="适合冲刺期先试用一轮岗位决策和 AI 训练。",
        price_band="CNY_29_39",
        sort_order=10,
        feature_highlights=(
            "岸岸 AI 问答每日 60 次",
            "申论 AI 批改每日 20 次",
            "外部错题解析每日 30 次",
            "岗位风险解释和计划重排高额度",
        ),
    ),
    ProductConfig(
        product_id="gongkao_ai_pro_yearly",
        tier="pro",
        billing_period="yearly",
        title="AI Pro 年度",
        subtitle="适合完整国考周期，覆盖选岗、训练、复盘和冲刺。",
        price_band="CNY_199_299",
        sort_order=20,
        feature_highlights=(
            "包含月度 Pro 全部 AI 权益",
            "覆盖全年岗位变化和阶段性计划调整",
            "更适合长期备考与多轮模考复盘",
        ),
    ),
)


FEATURE_QUOTAS: tuple[FeatureQuotaConfig, ...] = (
    FeatureQuotaConfig(
        feature="companion_ask",
        title="岸岸 AI 问答",
        period="day",
        free_limit=3,
        pro_limit=60,
    ),
    FeatureQuotaConfig(
        feature="essay_review",
        title="申论 AI 批改",
        period="day",
        free_limit=1,
        pro_limit=20,
    ),
    FeatureQuotaConfig(
        feature="xingce_cause_analysis",
        title="行测错因分析",
        period="day",
        free_limit=5,
        pro_limit=100,
    ),
    FeatureQuotaConfig(
        feature="external_wrong_intake",
        title="外部错题解析",
        period="day",
        free_limit=1,
        pro_limit=30,
    ),
    FeatureQuotaConfig(
        feature="photo_explain",
        title="拍照解析",
        period="day",
        free_limit=1,
        pro_limit=30,
    ),
    FeatureQuotaConfig(
        feature="position_narrative",
        title="岗位风险解释",
        period="day",
        free_limit=2,
        pro_limit=50,
    ),
    FeatureQuotaConfig(
        feature="study_plan_regeneration",
        title="学习计划重排",
        period="month",
        free_limit=0,
        pro_limit=10,
    ),
)


def product_ids() -> list[str]:
    return [product.product_id for product in PRODUCTS]


def quota_limit(*, tier: str, feature: str) -> int:
    quota = quota_for_feature(feature)
    if tier == "pro":
        return quota.pro_limit
    return quota.free_limit


def quota_for_feature(feature: str) -> FeatureQuotaConfig:
    quota = next((item for item in FEATURE_QUOTAS if item.feature == feature), None)
    if quota is None:
        raise KeyError(feature)
    return quota
