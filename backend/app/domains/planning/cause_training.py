"""错因级针对性训练方向。

错因 agent（cause_agent）产出逐题错因后，用户裁决会落库为
``learning.xingce_confirmed_cause_versions.cause_code``（AI 确认）或
``replacement_code``（用户结构化改写）。规划侧据此把「针对性训练」从
统计最弱模块，提升为「用户刚确认的错因对应的训练方向」。

本模块只负责两件事：
1. 把 8 类错因码映射到训练方向（cause -> focus）。
2. 把训练方向映射成面向用户的标题后缀（focus -> display）。
不写库、不调错因 agent，也不改选题 SQL。
"""

from __future__ import annotations

from typing import Literal, Mapping, NamedTuple


# 训练方向枚举：每个方向决定针对性训练题目的挑选侧重。
# 与 8 类错因码一一对应，未覆盖的码一律回退到 None（由调用方落到模块级训练）。
XingceTrainingFocus = Literal[
    "stem_reading",       # 审题训练：限定词、问法、陷阱
    "knowledge_base",     # 知识点基础训练：概念与公式
    "method_application", # 方法应用训练：方法选择与变式
    "calculation",        # 计算训练：计算量大、步骤多
    "time_bounded",       # 限时训练：严格计时
    "careful_selection",  # 稳选训练：逐项排除、避免蒙
    "core_drilling",      # 核心打底训练：跳过题所在的基础部分
    "balanced_drilling",  # 均衡训练：暂无明确侧重
]

CAUSE_TO_TRAINING_FOCUS: Mapping[str, XingceTrainingFocus | None] = {
    "stem_misread": "stem_reading",
    "knowledge_gap": "knowledge_base",
    "method_selection": "method_application",
    "calculation_error": "calculation",
    "time_pressure": "time_bounded",
    "guessing": "careful_selection",
    "active_abandon": "core_drilling",
    "other": "balanced_drilling",
}

# 没有可识别错因码时（如仅 user_replacement 无结构化码），不覆盖方向。
_FALLBACK_CODE = "user_replacement"

FOCUS_DISPLAY_NAME: Mapping[XingceTrainingFocus, str] = {
    "stem_reading": "审题",
    "knowledge_base": "知识点基础",
    "method_application": "方法应用",
    "calculation": "计算",
    "time_bounded": "限时",
    "careful_selection": "稳选",
    "core_drilling": "核心打底",
    "balanced_drilling": "均衡",
}

# 训练方向排序：有明确错因的方向优先展示，未覆盖码退到最后。
_FOCUS_ORDER: Mapping[XingceTrainingFocus | None, int] = {
    focus: index for index, focus in enumerate(CAUSE_TO_TRAINING_FOCUS.values())
}


class ConfirmedCauseDirection(NamedTuple):
    """一条已确认错因解析出的训练方向与展示信息。"""

    cause_code: str
    focus: XingceTrainingFocus | None
    display: str | None


def resolve_cause_training_focus(
    cause_code: str | None,
    replacement_code: str | None,
) -> XingceTrainingFocus | None:
    """把错因码解析为训练方向。

    AI 确认的错因用 cause_code；用户结构化改写用 replacement_code
    （两者只存其一，改写时 cause_code 固定为 user_replacement）。
    未覆盖或未知的码返回 None，由调用方回退到模块级训练。
    """

    if replacement_code:
        return CAUSE_TO_TRAINING_FOCUS.get(replacement_code)
    if cause_code and cause_code != _FALLBACK_CODE:
        return CAUSE_TO_TRAINING_FOCUS.get(cause_code)
    return None


def direction_from_codes(
    cause_code: str | None,
    replacement_code: str | None,
) -> ConfirmedCauseDirection | None:
    """把单条错因码解析成完整方向；没有可识别方向时返回 None。"""

    focus = resolve_cause_training_focus(cause_code, replacement_code)
    if focus is None:
        return None
    return ConfirmedCauseDirection(
        cause_code=cause_code or "",
        focus=focus,
        display=FOCUS_DISPLAY_NAME[focus],
    )


def pick_leading_confirmed_cause(
    causes: list[tuple[str, str | None]],
) -> ConfirmedCauseDirection | None:
    """从多条已确认错因里挑出「本次针对性训练」的主导错因。

    ``causes`` 元素为 (cause_code, replacement_code)。排序规则：
    1. 有训练方向的错因优先（按 CAUSE_TO_TRAINING_FOCUS 顺序）；
    2. 同一方向的按最新 created_at（传入顺序即时间升序，取最靠后的）；
    3. 没有任何可识别方向时返回 None。
    """

    index = leading_confirmed_cause_index(causes)
    if index is None:
        return None
    cause_code, replacement_code = causes[index]
    return direction_from_codes(cause_code, replacement_code)


def leading_confirmed_cause_index(
    causes: list[tuple[str, str | None]],
) -> int | None:
    """返回主导错因在 ``causes`` 里的下标；没有任何可识别方向时返回 None。

    排序规则：训练方向按 CAUSE_TO_TRAINING_FOCUS 顺序优先（更靠前、更具体
    的错因方向优先）；同一方向内取最新一条（causes 按时间升序）；没有任何
    可识别方向时返回 None。
    """

    if not causes:
        return None
    best: tuple[int, int, str] | None = None
    best_index: int | None = None
    for index, (cause_code, replacement_code) in enumerate(causes):
        focus = resolve_cause_training_focus(cause_code, replacement_code)
        key = (
            _FOCUS_ORDER.get(focus, 10**6),
            -index,
            cause_code or "",
        )
        if best is None or key < best:
            best = key
            best_index = index
    if best is None or resolve_cause_training_focus(*causes[best_index]) is None:
        return None
    return best_index
