from __future__ import annotations

from typing import Any, Mapping


def displayable_explanation(source: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return every non-empty bank explanation; internal review state is never a display gate."""
    preferred = str(source.get("preferred_analysis") or "").strip()
    if not preferred:
        return None, None
    return preferred, str(source.get("analysis_source") or "question_bank")


def has_stored_explanation(source: Mapping[str, Any]) -> bool:
    explanation, _source = displayable_explanation(source)
    return explanation is not None


def stored_explanation(source: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """Recover the exact frozen content-version explanation for already submitted attempts."""
    if source is None:
        return None, None
    return displayable_explanation(source)


# freeze_learner_bundle 的历史参数名叫 published_explanation；保留兼容别名，
# 但策略已明确改为“有文本即展示”，不再读取任何审核状态。
published_explanation = displayable_explanation
