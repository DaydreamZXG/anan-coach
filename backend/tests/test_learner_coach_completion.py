from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.companion.memory import (
    CompanionCoachRecheckConflict,
    CompanionMemoryStore,
)
from app.domains.companion.schemas import CompanionCoachRecheckCommand


NOW = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-4000-8000-00000000d151")
PROFILE_ID = UUID("00000000-0000-4000-8000-00000000d152")
PATTERN_ID = "b" * 64


def _coach_context() -> dict[str, object]:
    return {
        "contractVersion": "learner-coach-context@v1",
        "profileVersionId": str(PROFILE_ID),
        "profile": {
            "profileVersionId": str(PROFILE_ID),
            "decisionSummary": {"reviewDueCount": 2},
            "modules": [
                {"moduleKey": "data_analysis", "dueCount": 3, "stableMasteryCount": 4},
                {"moduleKey": "verbal", "dueCount": 1, "stableMasteryCount": 2},
            ],
            "errorPatterns": [
                {
                    "patternId": PATTERN_ID,
                    "displayName": "基期量判断不稳定",
                    "status": "confirmed",
                }
            ],
        },
        "recognition": {"level": "verifying"},
        "freshness": "current",
    }


def _seed_user(session: Session) -> None:
    session.execute(
        text(
            "insert into learner.users (user_id, display_name, account_status) "
            "values (:user_id, '岸岸认识页测试', 'active')"
        ),
        {"user_id": str(USER_ID)},
    )
    session.commit()


def test_context_v2_projects_explicit_semantics_retest_and_append_only_recheck(
    db_session: Session,
) -> None:
    _seed_user(db_session)
    store = CompanionMemoryStore(db_session)
    preference = store.create(
        user_id=USER_ID,
        idempotency_key="coach-memory-pref-015",
        category="explanation_preference",
        content="资料分析先列式，再解释为什么。",
        now=NOW,
    )
    constraint = store.create(
        user_id=USER_ID,
        idempotency_key="coach-memory-time-015",
        category="study_constraint",
        content="工作日只能学习一小时。",
        now=NOW,
    )
    db_session.commit()

    understanding = store.coach_understanding(
        user_id=USER_ID,
        coach_context=_coach_context(),
    )

    assert understanding.contract_version == "learner-coach-context@v2"
    assert understanding.coach_context["profileVersionId"] == str(PROFILE_ID)
    by_id = {item.memory_id: item for item in understanding.explicit_preferences}
    assert by_id[preference.memory_id].effect == "applied_to_answers"
    assert by_id[constraint.memory_id].effect == "needs_confirmation"
    assert by_id[constraint.memory_id].action_url == "/plans/study-preference/index"
    assert understanding.retest.due_now == 2
    assert understanding.retest.scheduled == 4
    assert understanding.retest.stable == 6
    assert understanding.retest.action_url.endswith("bucket=due_verification")
    assert store.state(user_id=USER_ID, now=NOW).maximum_long_term_items is None

    command = CompanionCoachRecheckCommand(
        commandId="coach-recheck-command-015",
        profileVersionId=PROFILE_ID,
        patternId=PATTERN_ID,
        note="我觉得这更像计算步骤不稳",
    )
    receipt = store.request_coach_recheck(
        user_id=USER_ID,
        command=command,
        coach_context=_coach_context(),
        now=NOW,
    )
    replay = store.request_coach_recheck(
        user_id=USER_ID,
        command=command,
        coach_context=_coach_context(),
        now=NOW,
    )
    db_session.commit()

    assert replay == receipt
    assert receipt.state == "pending_reverification"
    assert receipt.retest_url.endswith("bucket=due_verification")
    assert db_session.execute(
        text(
            "select count(*) from learning.coach_judgment_recheck_versions "
            "where user_id = :user_id"
        ),
        {"user_id": USER_ID},
    ).scalar_one() == 1
    assert db_session.execute(
        text(
            "select count(*) from learning.xingce_mastery_state_versions "
            "where user_id = :user_id"
        ),
        {"user_id": USER_ID},
    ).scalar_one() == 0

    refreshed = store.coach_understanding(
        user_id=USER_ID,
        coach_context=_coach_context(),
    )
    assert len(refreshed.pending_rechecks) == 1
    assert refreshed.pending_rechecks[0].pattern_id == PATTERN_ID


def test_recheck_rejects_stale_profile_and_changed_idempotent_payload(
    db_session: Session,
) -> None:
    _seed_user(db_session)
    store = CompanionMemoryStore(db_session)
    stale = CompanionCoachRecheckCommand(
        commandId="coach-recheck-stale-015",
        profileVersionId=UUID("00000000-0000-4000-8000-00000000d159"),
        patternId=PATTERN_ID,
    )
    with pytest.raises(CompanionCoachRecheckConflict):
        store.request_coach_recheck(
            user_id=USER_ID,
            command=stale,
            coach_context=_coach_context(),
            now=NOW,
        )
    db_session.rollback()

    current = CompanionCoachRecheckCommand(
        commandId="coach-recheck-reuse-015",
        profileVersionId=PROFILE_ID,
        patternId=PATTERN_ID,
    )
    store.request_coach_recheck(
        user_id=USER_ID,
        command=current,
        coach_context=_coach_context(),
        now=NOW,
    )
    db_session.commit()
    with pytest.raises(CompanionCoachRecheckConflict):
        store.request_coach_recheck(
            user_id=USER_ID,
            command=current.model_copy(update={"note": "changed"}),
            coach_context=_coach_context(),
            now=NOW,
        )
