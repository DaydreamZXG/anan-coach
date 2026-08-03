from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.planning.repository import GoalAbilitySnapshot
from app.domains.planning.service import PlanningService, _goal_gap
from app.domains.position_decision.learner_target_schemas import (
    LearnerTargetScoreProjection,
    LearnerTargetScoreRange,
)
from tests.test_position_decision import (
    _make_db_client,
    _seed_position_with_score,
    _seed_user,
)


def test_historical_target_score_does_not_require_current_position_eligibility(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "historical-target-token"
    user_id = _seed_user(db_session_factory, token=token)
    _seed_target_profile(db_session_factory, user_id=user_id)
    for index, score in enumerate((118.0, 124.0, 131.0), start=1):
        _seed_position_with_score(
            db_session_factory,
            source_id=f"historical-target-{index}",
            position_code=f"30011088000{index}",
            department_full=f"国家税务总局历史{index}县税务局",
            headcount=1,
            scores=[(2026, score)],
        )

    response = _make_db_client(db_session_factory).get(
        "/api/v2/learner-target-score",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "available"
    assert data["referenceYear"] == 2026
    assert data["fallbackLevel"] == "latest_local_exact_major"
    assert data["sampleCount"] == 3
    assert data["targetScore"]["lower"] <= data["targetScore"]["recommended"]
    assert data["targetScore"]["recommended"] <= data["targetScore"]["upper"]
    assert len(data["referencePositions"]) == 3


def test_planning_uses_historical_target_without_primary_position_goal() -> None:
    now = datetime.now(UTC)

    class ReadyTargetRuntime:
        def projection(self, session: Session, *, user_id: str) -> LearnerTargetScoreProjection:
            del session, user_id
            return LearnerTargetScoreProjection(
                status="available",
                reasonCode="historical_cohort_ready",
                versionId="b" * 64,
                modelVersion="historical-position-cohort-v4",
                profileVersion=2,
                referenceYear=2026,
                targetScore=LearnerTargetScoreRange(lower=120, recommended=126, upper=132),
                fallbackLevel="latest_local_exact_major",
                sampleCount=3,
                referencePositions=[],
                limitations=[],
            )

    snapshot = GoalAbilitySnapshot(
        goal_version_id=None,
        position_name=None,
        department_name=None,
        target_lower=None,
        target_recommended=None,
        target_upper=None,
        target_evidence_level=None,
        target_valid_until=None,
        estimate_version_id=str(uuid4()),
        estimate_status="complete",
        xingce_lower=60,
        xingce_recommended=65,
        xingce_upper=70,
        shenlun_lower=55,
        shenlun_recommended=60,
        shenlun_upper=65,
        total_lower=115,
        total_recommended=125,
        total_upper=135,
        estimate_confidence="medium",
        estimate_valid_until=now + timedelta(days=7),
        evidence_summary={"evidenceCount": 4},
    )
    service = PlanningService(learner_target=ReadyTargetRuntime())  # type: ignore[arg-type]

    enriched = service._with_learner_target(  # noqa: SLF001
        object(),  # type: ignore[arg-type]
        user_id=str(uuid4()),
        snapshot=snapshot,
    )
    gap = _goal_gap(enriched, now)

    assert enriched.target_owner == "learner_target_score"
    assert enriched.department_name is None
    assert gap.readiness == "complete"
    assert gap.goal_id == "b" * 64
    assert gap.baseline_gap == 1


def _seed_target_profile(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
) -> None:
    change_id = str(uuid4())
    now = datetime.now(UTC)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "insert into learner.profiles (user_id, profile_version) "
                "values (cast(:user_id as uuid), 2)"
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.qualification_profile_changes (
                  change_id, command_id, user_id, from_profile_version,
                  to_profile_version, command_payload, created_at
                ) values (
                  cast(:change_id as uuid), :command_id, cast(:user_id as uuid),
                  1, 2, cast(:payload as jsonb), :created_at
                )
                """
            ),
            {
                "change_id": change_id,
                "command_id": f"target-profile-{user_id}",
                "user_id": user_id,
                "payload": "{}",
                "created_at": now,
            },
        )
        for key, value in (
            ("major", '"法学"'),
            ("education", '"本科"'),
            ("gender", '"男"'),
            ("political_status", '"群众"'),
            ("grassroots_years", "0"),
        ):
            session.execute(
                text(
                    """
                    insert into learner.qualification_facts (
                      fact_id, change_id, user_id, profile_version, fact_key,
                      value, verification_status, effective_at, recorded_at
                    ) values (
                      cast(:fact_id as uuid), cast(:change_id as uuid),
                      cast(:user_id as uuid), 2, :fact_key, cast(:value as jsonb),
                      'confirmed', :recorded_at, :recorded_at
                    )
                    """
                ),
                {
                    "fact_id": str(uuid4()),
                    "change_id": change_id,
                    "user_id": user_id,
                    "fact_key": key,
                    "value": value,
                    "recorded_at": now,
                },
            )
        session.execute(
            text(
                """
                insert into learner.position_preference_versions (
                  preference_version_id, command_id, user_id, version_no,
                  preferred_regions, accepts_relocation, accepted_position_levels,
                  source_type, input_fingerprint, created_at
                ) values (
                  cast(:preference_id as uuid), :command_id, cast(:user_id as uuid), 1,
                  array['江西']::text[], false, array['county_or_below']::text[],
                  'user_confirmed', :fingerprint, :created_at
                )
                """
            ),
            {
                "preference_id": str(uuid4()),
                "command_id": f"target-preference-{user_id}",
                "user_id": user_id,
                "fingerprint": "a" * 64,
                "created_at": now,
            },
        )
