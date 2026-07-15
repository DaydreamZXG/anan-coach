from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.qualification.application import QualificationApplicationService
from app.domains.qualification.repository import (
    FactEvidenceInput,
    FactMutation,
    QualificationContractError,
    QualificationRepository,
)
from app.domains.qualification.rule_releases import PositionRuleReleaseService
from app.main import create_app


NOW = datetime(2026, 7, 12, 20, tzinfo=UTC)


def _seed_manual_requirement(
    factory: sessionmaker[Session],
) -> tuple[str, str, str]:
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
                  system_code, region, paper_type, headcount, education_min,
                  major_required, political_req, service_min_yrs, remark
                ) values (
                  :position_id, 'manual-position', 2026, '300110001999',
                  '一级行政执法员', '国家税务总局测试局', 'tax', '江西',
                  'administrativeLaw', 2, '本科', '法学类', '不限', 0,
                  '须持有法律职业资格证书A证'
                )
                """
            ),
            {"position_id": position_id},
        )
        QualificationRepository().replace_facts(
            session,
            user_id=user_id,
            command_id="manual-profile-v1",
            mutations=[
                FactMutation(
                    key="education",
                    value="本科",
                    verification_status="reported",
                    evidence=(FactEvidenceInput(source_type="learner_report"),),
                ),
                FactMutation(
                    key="major",
                    value="法学",
                    verification_status="reported",
                    evidence=(FactEvidenceInput(source_type="learner_report"),),
                ),
            ],
            now=NOW,
        )
        release_service = PositionRuleReleaseService()
        release_id = release_service.ensure_draft(
            session,
            exam_code="guokao",
            exam_year=2026,
            rule_set_version="guokao-2026-rules-v1",
            position_data_version="guokao-2026-manual-v1",
        )
        release_service.compile_draft(session, release_id=release_id, compiled_at=NOW)
        release_service.activate(session, release_id=release_id, activated_at=NOW)
        requirement_id = session.execute(
            text(
                """
                select requirement_id
                from product.position_requirements
                where position_id = :position_id and fact_key = 'major'
                """
            ),
            {"position_id": position_id},
        ).scalar_one()
    return user_id, position_id, str(requirement_id)


def test_manual_confirmation_increments_profile_and_recomputes_eligibility(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, requirement_id = _seed_manual_requirement(db_session_factory)
    service = QualificationApplicationService()
    with db_session_factory.begin() as session:
        before = service.evaluate_position(
            session,
            user_id=user_id,
            position_id=position_id,
            now=NOW,
        )
    assert before.evaluation.status == "needs_confirmation"

    with db_session_factory.begin() as session:
        confirmation, after = service.confirm_requirement(
            session,
            user_id=user_id,
            position_id=position_id,
            requirement_id=requirement_id,
            response="satisfied",
            statement="我的毕业专业属于公告所列法学类。",
            command_id="manual-confirm-major-1",
            now=NOW,
        )
    assert confirmation.profile_version == 3
    # The remark is a separate manual condition, so one confirmation cannot hide it.
    assert after.evaluation.status == "needs_confirmation"
    major_check = next(
        check for check in after.evaluation.checks if check.requirement_id == requirement_id
    )
    assert major_check.status == "met"
    assert major_check.reason_code == "manual_condition_confirmed"
    assert major_check.confirmation_id == confirmation.confirmation_id

    with db_session_factory() as session:
        outbox = session.execute(
            text(
                """
                select payload
                from platform.outbox_events
                where deduplication_key = 'qualification-requirement:manual-confirm-major-1'
                """
            )
        ).scalar_one()
    assert outbox["profileVersion"] == 3


def test_confirmation_is_idempotent_immutable_and_revisable(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, requirement_id = _seed_manual_requirement(db_session_factory)
    repository = QualificationRepository()
    kwargs = {
        "user_id": user_id,
        "position_id": position_id,
        "requirement_id": requirement_id,
        "response": "satisfied",
        "statement": None,
        "command_id": "confirmation-command-1",
        "now": NOW,
    }
    with db_session_factory.begin() as session:
        first = repository.record_requirement_confirmation(session, **kwargs)  # type: ignore[arg-type]
    with db_session_factory.begin() as session:
        repeated = repository.record_requirement_confirmation(session, **kwargs)  # type: ignore[arg-type]
    assert repeated.confirmation_id == first.confirmation_id

    with pytest.raises(QualificationContractError):
        with db_session_factory.begin() as session:
            repository.record_requirement_confirmation(
                session,
                **{**kwargs, "response": "not_satisfied"},  # type: ignore[arg-type]
            )

    with db_session_factory.begin() as session:
        revised = repository.record_requirement_confirmation(
            session,
            **{
                **kwargs,
                "response": "not_satisfied",
                "command_id": "confirmation-command-2",
            },  # type: ignore[arg-type]
        )
    assert revised.profile_version == 4
    assert revised.supersedes_confirmation_id == first.confirmation_id


def test_non_manual_or_inactive_requirement_cannot_be_confirmed(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, _ = _seed_manual_requirement(db_session_factory)
    with db_session_factory() as session:
        deterministic_id = session.execute(
            text(
                """
                select requirement_id
                from product.position_requirements
                where position_id = :position_id and operator <> 'manual_review'
                limit 1
                """
            ),
            {"position_id": position_id},
        ).scalar_one()

    with pytest.raises(QualificationContractError, match="manual-review"):
        with db_session_factory.begin() as session:
            QualificationRepository().record_requirement_confirmation(
                session,
                user_id=user_id,
                position_id=position_id,
                requirement_id=str(deterministic_id),
                response="satisfied",
                statement=None,
                command_id="invalid-confirmation",
                now=NOW,
            )


def test_v2_confirmation_returns_new_profile_version_and_recomputed_snapshot(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, requirement_id = _seed_manual_requirement(db_session_factory)
    app = create_app()

    def override_db() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        learner_key=user_id,
        display_name="学习者",
        email=None,
    )
    client = TestClient(app)
    response = client.put(
        f"/api/v2/positions/{position_id}/requirements/{requirement_id}/confirmation",
        headers={
            "Idempotency-Key": "confirm-major-through-api",
            "X-Request-ID": "confirm-requirement-1",
        },
        json={
            "response": "satisfied",
            "statement": "我的专业属于该专业大类。",
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "confirm-requirement-1"
    body = response.json()
    assert body["data"]["confirmation"]["profileVersion"] == 3
    assert body["data"]["confirmation"]["response"] == "satisfied"
    assert body["data"]["eligibility"]["learnerProfileVersion"] == 3
