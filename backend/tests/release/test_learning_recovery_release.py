from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.domains.daily_opening.schemas import ImpressionCommand
from tests.test_xingce_cause_agent_workflow import (
    test_reflection_is_durable_while_provider_configuration_recovers,
)


def test_failure_replay_and_recovery_contract(
    db_session_factory: sessionmaker[Session],
) -> None:
    """Release-unit acceptance: a dependency failure cannot erase user work."""
    test_reflection_is_durable_while_provider_configuration_recovers(
        db_session_factory
    )

    plan_id = "00000000-0000-4000-8000-000000000101"
    preference_id = "00000000-0000-4000-8000-000000000102"
    impression = ImpressionCommand.model_validate(
        {
            "planVersionId": plan_id,
            "preferenceVersionId": preference_id,
            "locationVersionId": None,
            "weatherFingerprint": None,
            "coffeeState": "disabled",
        }
    )
    assert impression.plan_version_id == UUID(plan_id)
    assert impression.preference_version_id == UUID(preference_id)
