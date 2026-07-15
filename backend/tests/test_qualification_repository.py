from __future__ import annotations

from datetime import UTC, datetime
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domains.qualification.repository import (
    FactEvidenceInput,
    FactMutation,
    QualificationContractError,
    QualificationRepository,
)
from app.domains.qualification.schemas import PositionRequirement
from app.domains.qualification.service import evaluate_eligibility


NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


def _seed_user_and_position(factory: sessionmaker[Session]) -> tuple[str, str]:
    user_id = str(uuid4())
    position_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
            {"id": user_id},
        )
        session.execute(
            text(
                """
                insert into product.positions (
                  position_id, source_id, year, position_code, name, department_full,
                  system_code, region, paper_type, headcount
                ) values (
                  :position_id, :source_id, 2026, '300110001001', '一级行政执法员',
                  '国家税务总局测试局', 'tax', '江西', 'administrativeLaw', 2
                )
                """
            ),
            {"position_id": position_id, "source_id": f"source-{position_id}"},
        )
    return user_id, position_id


def _mutation(key: str, value: object, *, fact_id: str | None = None) -> FactMutation:
    return FactMutation(
        key=key,  # type: ignore[arg-type]
        value=value,  # type: ignore[arg-type]
        verification_status="confirmed",
        evidence=(
            FactEvidenceInput(
                source_type="learner_report",
                source_reference="onboarding:A02",
                recorded_at=NOW,
            ),
        ),
        effective_at=NOW,
        fact_id=fact_id,
    )


def test_fact_changes_are_append_only_and_increment_one_profile_version(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, _ = _seed_user_and_position(db_session_factory)
    repository = QualificationRepository()

    with db_session_factory.begin() as session:
        version_2, first = repository.replace_facts(
            session,
            user_id=user_id,
            command_id="profile-change-1",
            mutations=[_mutation("education", "本科"), _mutation("major", "法学")],
            now=NOW,
        )
    with db_session_factory.begin() as session:
        version_3, second = repository.replace_facts(
            session,
            user_id=user_id,
            command_id="profile-change-2",
            mutations=[_mutation("major", "法律")],
            now=NOW,
        )

    assert version_2 == 2
    assert version_3 == 3
    assert second[0].supersedes_fact_id == first[1].fact_id
    with db_session_factory() as session:
        facts = repository.list_current_facts(session, user_id=user_id)
        stored = session.execute(
            text(
                """
                select fact_key, profile_version, value
                from learner.qualification_facts
                where user_id = :user_id
                order by profile_version, fact_key
                """
            ),
            {"user_id": user_id},
        ).mappings().all()
    assert {fact.key: fact.value for fact in facts} == {
        "education": "本科",
        "major": "法律",
    }
    assert [(row["fact_key"], row["profile_version"]) for row in stored] == [
        ("education", 2),
        ("major", 2),
        ("major", 3),
    ]


def test_profile_change_command_is_idempotent_and_immutable(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, _ = _seed_user_and_position(db_session_factory)
    repository = QualificationRepository()
    mutation = _mutation("education", "本科")
    with db_session_factory.begin() as session:
        first_version, first = repository.replace_facts(
            session,
            user_id=user_id,
            command_id="same-profile-command",
            mutations=[mutation],
            now=NOW,
        )
    with db_session_factory.begin() as session:
        repeated_version, repeated = repository.replace_facts(
            session,
            user_id=user_id,
            command_id="same-profile-command",
            mutations=[mutation],
            now=NOW,
        )
    assert repeated_version == first_version
    assert repeated[0].fact_id == first[0].fact_id

    with pytest.raises(QualificationContractError):
        with db_session_factory.begin() as session:
            repository.replace_facts(
                session,
                user_id=user_id,
                command_id="same-profile-command",
                mutations=[_mutation("education", "硕士")],
                now=NOW,
            )


def test_fact_set_rolls_back_as_one_transaction(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, _ = _seed_user_and_position(db_session_factory)
    repository = QualificationRepository()

    with pytest.raises(IntegrityError):
        with db_session_factory.begin() as session:
            repository.replace_facts(
                session,
                user_id=user_id,
                command_id="profile-change-rollback",
                mutations=[
                    _mutation("education", "本科"),
                    FactMutation(
                        key="major",
                        value="法学",
                        verification_status="invalid",
                        evidence=(FactEvidenceInput(source_type="learner_report"),),
                        effective_at=NOW,
                    ),
                ],
                now=NOW,
            )

    with db_session_factory() as session:
        count = session.execute(
            text("select count(*) from learner.qualification_facts where user_id = :id"),
            {"id": user_id},
        ).scalar_one()
        profile = session.execute(
            text("select profile_version from learner.profiles where user_id = :id"),
            {"id": user_id},
        ).scalar_one_or_none()
    assert count == 0
    assert profile is None


def test_snapshot_is_idempotent_for_identical_input_versions_and_rejects_drift(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id = _seed_user_and_position(db_session_factory)
    repository = QualificationRepository()
    with db_session_factory.begin() as session:
        version, facts = repository.replace_facts(
            session,
            user_id=user_id,
            command_id="profile-change-snapshot",
            mutations=[_mutation("education", "本科")],
            now=NOW,
        )
        evaluation = evaluate_eligibility(
            facts=facts,
            requirements=[
                PositionRequirement.model_validate(
                    {
                        "requirementId": str(uuid4()),
                        "field": "education",
                        "operator": "equals",
                        "expected": "本科",
                        "sourceText": "本科及以上",
                        "sourceReference": "2026-guokao:300110001001",
                    }
                )
            ],
            rule_set_version="guokao-2026-v1",
            position_data_version="guokao-2026-20260712",
            evaluated_at=NOW,
        )
        first_id = repository.save_snapshot(
            session,
            user_id=user_id,
            position_id=position_id,
            learner_profile_version=version,
            evaluation=evaluation,
        )
        repeated_id = repository.save_snapshot(
            session,
            user_id=user_id,
            position_id=position_id,
            learner_profile_version=version,
            evaluation=evaluation,
        )

    assert repeated_id == first_id

    drifted = evaluation.model_copy(update={"status": "ineligible"})
    with pytest.raises(QualificationContractError):
        with db_session_factory.begin() as session:
            repository.save_snapshot(
                session,
                user_id=user_id,
                position_id=position_id,
                learner_profile_version=version,
                evaluation=drifted,
            )


def test_database_rejects_non_versioned_duplicate_fact_key(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, _ = _seed_user_and_position(db_session_factory)
    repository = QualificationRepository()
    with db_session_factory.begin() as session:
        repository.replace_facts(
            session,
            user_id=user_id,
            command_id="profile-change-duplicate",
            mutations=[_mutation("education", "本科")],
            now=NOW,
        )

    with pytest.raises(IntegrityError):
        with db_session_factory.begin() as session:
            session.execute(
                text(
                    """
                    insert into learner.qualification_facts (
                      fact_id, change_id, user_id, profile_version, fact_key, value,
                      verification_status, effective_at
                    ) values (
                      :fact_id,
                      (select change_id from learner.qualification_profile_changes where command_id = 'profile-change-duplicate'),
                      :user_id, 2, 'education', cast(:value as jsonb),
                      'confirmed', :effective_at
                    )
                    """
                ),
                {
                    "fact_id": str(uuid4()),
                    "user_id": user_id,
                    "value": json.dumps("硕士"),
                    "effective_at": NOW,
                },
            )
