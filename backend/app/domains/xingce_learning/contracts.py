from __future__ import annotations


XINGCE_QUICK_QUESTION_BUNDLE_SIZE = 3
XINGCE_MAX_QUESTION_BUNDLE_SIZE = 30
XINGCE_MINUTES_PER_QUESTION = 2


def xingce_question_count(*, mode: str, estimated_minutes: int) -> int:
    """Derive the frozen bundle size from the immutable plan task."""
    if mode == "verification":
        return XINGCE_QUICK_QUESTION_BUNDLE_SIZE
    return min(
        XINGCE_MAX_QUESTION_BUNDLE_SIZE,
        max(
            XINGCE_QUICK_QUESTION_BUNDLE_SIZE,
            estimated_minutes // XINGCE_MINUTES_PER_QUESTION,
        ),
    )


def xingce_practice_minutes(*, available_minutes: int, question_supply: int) -> int:
    """Use up to half the day for one focused block while preserving the essay track."""
    minimum_minutes = XINGCE_QUICK_QUESTION_BUNDLE_SIZE * XINGCE_MINUTES_PER_QUESTION
    if (
        available_minutes < minimum_minutes
        or question_supply < XINGCE_QUICK_QUESTION_BUNDLE_SIZE
    ):
        return 0
    desired_minutes = min(60, available_minutes, max(20, available_minutes // 2))
    question_count = min(
        XINGCE_MAX_QUESTION_BUNDLE_SIZE,
        question_supply,
        max(
            XINGCE_QUICK_QUESTION_BUNDLE_SIZE,
            desired_minutes // XINGCE_MINUTES_PER_QUESTION,
        ),
    )
    return question_count * XINGCE_MINUTES_PER_QUESTION
