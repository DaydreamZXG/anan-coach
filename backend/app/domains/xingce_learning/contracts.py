"""Compatibility exports for the neutral Xingce attempt sizing contract."""

from app.domains.task_runtime.xingce_attempt.contracts import (
    XINGCE_MAX_QUESTION_BUNDLE_SIZE,
    XINGCE_MINUTES_PER_QUESTION,
    XINGCE_QUICK_QUESTION_BUNDLE_SIZE,
    xingce_practice_minutes,
    xingce_question_count,
)

__all__ = [
    "XINGCE_MAX_QUESTION_BUNDLE_SIZE",
    "XINGCE_MINUTES_PER_QUESTION",
    "XINGCE_QUICK_QUESTION_BUNDLE_SIZE",
    "xingce_practice_minutes",
    "xingce_question_count",
]
