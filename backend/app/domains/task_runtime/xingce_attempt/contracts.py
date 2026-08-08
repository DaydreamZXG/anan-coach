from __future__ import annotations


XINGCE_QUICK_QUESTION_BUNDLE_SIZE = 3
XINGCE_MAX_QUESTION_BUNDLE_SIZE = 30
XINGCE_MINUTES_PER_QUESTION = 2

# Stored inside the server-private scoring bundle.  Absence is the only
# historical-session discriminator; response projections are replay data and
# must never decide whether compatibility completion runs.
XINGCE_SESSION_GENERATION_KEY = "sessionGeneration"
XINGCE_SESSION_GENERATION_V1 = "xingce-plan-completion-at-submit@v1"


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
    """Use up to half the day for one focused block while preserving essay."""
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


def is_current_xingce_session(task_session: object) -> bool:
    """Return whether a task-session row carries the explicit v1 generation."""
    try:
        scoring_bundle = task_session["scoring_bundle"]  # type: ignore[index]
    except (KeyError, TypeError):
        return False
    return (
        isinstance(scoring_bundle, dict)
        and scoring_bundle.get(XINGCE_SESSION_GENERATION_KEY)
        == XINGCE_SESSION_GENERATION_V1
    )
