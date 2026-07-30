from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.position_decision.goals import (
    ConfirmPrimaryGoalCommand,
    PositionGoalContractError,
    PositionGoalService,
)
from app.domains.position_decision.schemas import PrimaryGoalPreview, TargetScoreRange
from app.domains.positions.target_scores import TargetScoreExpiredError
from app.domains.qualification.repository import (
    FactEvidenceInput,
    FactMutation,
    QualificationRepository,
)
from app.domains.qualification.schemas import PositionRequirement
from app.domains.qualification.service import evaluate_eligibility


NOW = datetime(2026, 7, 12, 14, tzinfo=UTC)
WINDOW_CLOSE = datetime(2026, 10, 31, tzinfo=UTC)


def _seed_context(factory: sessionmaker[Session]) -> tuple[str, str, int, str]:
    user_id = str(uuid4())
    position_id = str(uuid4())
    release_id = str(uuid4())
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
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status
                ) values (
                  cast(:release_id as uuid), 'guokao', 2026,
                  'guokao-2026-v1', 'guokao-2026-20260712', 'draft'
                )
                """
            ),
            {"release_id": release_id},
        )
        session.execute(
            text(
                """
                insert into product.position_release_windows (
                  window_id, release_id, window_kind, sequence_no,
                  opens_at, closes_at, official_source_url,
                  official_source_title, official_source_published_at,
                  official_source_received_at, official_source_sha256,
                  official_source_receipt
                ) values (
                  gen_random_uuid(), cast(:release_id as uuid), 'primary', 1,
                  :opens_at, :closes_at,
                  'https://example.gov.cn/guokao/2026/announcement',
                  '2026年度考试录用公务员公告',
                  :published_at, :received_at, repeat('c', 64),
                  'position-goal-fixture-primary'
                )
                """
            ),
            {
                "release_id": release_id,
                "opens_at": datetime(2026, 7, 10, tzinfo=UTC),
                "closes_at": WINDOW_CLOSE,
                "published_at": datetime(2026, 7, 1, tzinfo=UTC),
                "received_at": datetime(2026, 7, 2, tzinfo=UTC),
            },
        )
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'active', activated_at = :activated_at
                where release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": release_id, "activated_at": NOW},
        )
        repository = QualificationRepository()
        version, facts = repository.replace_facts(
            session,
            user_id=user_id,
            command_id=f"seed-profile-{user_id}",
            mutations=[
                FactMutation(
                    key="education",
                    value="本科",
                    verification_status="confirmed",
                    evidence=(FactEvidenceInput(source_type="learner_report"),),
                    effective_at=NOW,
                )
            ],
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
        snapshot_id = repository.save_snapshot(
            session,
            user_id=user_id,
            position_id=position_id,
            learner_profile_version=version,
            evaluation=evaluation,
        )
    return user_id, position_id, version, snapshot_id


def _target_score() -> TargetScoreRange:
    return TargetScoreRange(
        lower_bound=126,
        recommended=129,
        upper_bound=132,
        basis_level="direct",
        evidence_level="direct",
        sample_count=6,
        generated_at=NOW,
        valid_until=WINDOW_CLOSE,
    )


def _create_preview(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    position_id: str,
    version: int,
    snapshot_id: str,
    request_id: str,
    now: datetime = NOW,
) -> PrimaryGoalPreview:
    with factory.begin() as session:
        return PositionGoalService().create_primary_goal_preview(
            session,
            request_id=request_id,
            user_id=user_id,
            position_id=position_id,
            eligibility_snapshot_id=snapshot_id,
            learner_profile_version=version,
            position_data_version="guokao-2026-20260712",
            target_score=_target_score(),
            now=now,
        )


def _command(
    *,
    user_id: str,
    preview: PrimaryGoalPreview,
    command_id: str = "select-primary-1",
    reason: str = "确认主报岗位",
) -> ConfirmPrimaryGoalCommand:
    return ConfirmPrimaryGoalCommand(
        command_id=command_id,
        preview_id=preview.preview_id,
        user_id=user_id,
        position_id=preview.position_id,
        eligibility_snapshot_id=preview.eligibility_snapshot_id,
        expected_previous_goal_version_id=preview.current_goal_version_id,
        learner_profile_version=preview.learner_profile_version,
        position_data_version=preview.position_data_version,
        target_score=preview.target_score,
        change_reason=reason,
        plan_impact=preview.plan_impact,
    )


def test_confirm_primary_goal_writes_history_projection_event_and_outbox_atomically(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    service = PositionGoalService()
    preview = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-primary-1",
    )
    command = _command(
        user_id=user_id,
        preview=preview,
    )

    with db_session_factory.begin() as session:
        goal = service.confirm_primary_goal(session, command=command, now=NOW)

    with db_session_factory() as session:
        profile = (
            session.execute(
                text(
                    """
                select target_exam, target_region, position_group,
                       target_position_id, target_score,
                       target_position_data_version
                from learner.profiles
                where user_id = :id
                """
                ),
                {"id": user_id},
            )
            .mappings()
            .one()
        )
        event = (
            session.execute(
                text("select * from learner.position_goal_events where command_id = :id"),
                {"id": command.command_id},
            )
            .mappings()
            .one()
        )
        outbox = (
            session.execute(
                text("select * from platform.outbox_events where deduplication_key = :key"),
                {"key": f"position-goal:{command.command_id}"},
            )
            .mappings()
            .one()
        )

    assert goal.status == "confirmed"
    assert goal.role == "primary"
    assert profile["target_exam"] == "guokao"
    assert profile["target_region"] is None
    assert profile["position_group"] is None
    assert str(profile["target_position_id"]) == position_id
    assert float(profile["target_score"]) == 129
    assert profile["target_position_data_version"] == "guokao-2026-20260712"
    assert event["event_type"] == "goal_selected"
    assert outbox["event_type"] == "position_goal.changed"
    assert outbox["aggregate_id"] == goal.goal_version_id
    with db_session_factory() as session:
        used_preview = (
            session.execute(
                text(
                    """
                select used_at, used_by_command_id
                from learner.position_goal_previews
                where preview_id = :preview_id
                """
                ),
                {"preview_id": preview.preview_id},
            )
            .mappings()
            .one()
        )
    assert used_preview["used_at"] == NOW
    assert used_preview["used_by_command_id"] == command.command_id


def test_goal_command_is_idempotent_but_immutable(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    service = PositionGoalService()
    preview = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-idempotent-1",
    )
    command = _command(
        user_id=user_id,
        preview=preview,
    )
    with db_session_factory.begin() as session:
        first = service.confirm_primary_goal(session, command=command, now=NOW)
    with db_session_factory.begin() as session:
        repeated = service.confirm_primary_goal(session, command=command, now=NOW)
    assert repeated.goal_version_id == first.goal_version_id

    changed = _command(
        user_id=user_id,
        preview=preview,
        reason="偷偷改变同一命令",
    )
    with pytest.raises(PositionGoalContractError):
        with db_session_factory.begin() as session:
            service.confirm_primary_goal(session, command=changed, now=NOW)


def test_legacy_target_score_preview_and_command_replay_fail_with_stable_expiry(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    service = PositionGoalService()
    preview = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-legacy-replay",
    )
    command = _command(
        user_id=user_id,
        preview=preview,
        command_id="confirm-legacy-replay",
    )
    with db_session_factory.begin() as session:
        service.confirm_primary_goal(session, command=command, now=NOW)
        session.execute(
            text(
                """
                update learner.position_goal_events
                set command_payload = jsonb_set(
                  command_payload,
                  '{targetScore}',
                  (command_payload -> 'targetScore') - 'basisLevel' - 'generatedAt'
                )
                where command_id = :command_id
                """
            ),
            {"command_id": command.command_id},
        )

    with pytest.raises(TargetScoreExpiredError) as replay_error:
        with db_session_factory() as session:
            service.replay_confirm_primary_goal(
                session,
                command_id=command.command_id,
                user_id=user_id,
                preview_id=preview.preview_id,
                change_reason=command.change_reason,
            )
    assert replay_error.value.code == "target_score_basis_expired"

    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update learner.position_goal_previews
                set target_score = target_score - 'basisLevel' - 'generatedAt'
                where preview_id = cast(:preview_id as uuid)
                """
            ),
            {"preview_id": preview.preview_id},
        )

    with pytest.raises(TargetScoreExpiredError) as preview_error:
        with db_session_factory() as session:
            service.get_primary_goal_preview(
                session,
                user_id=user_id,
                preview_id=preview.preview_id,
            )
    assert preview_error.value.code == "target_score_basis_expired"


def test_preview_request_is_idempotent_but_immutable(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    first = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-idempotency",
    )
    repeated = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-idempotency",
        now=NOW + timedelta(seconds=10),
    )
    assert repeated.preview_id == first.preview_id
    assert repeated.expires_at == first.expires_at

    with pytest.raises(PositionGoalContractError, match="different goal inputs"):
        with db_session_factory.begin() as session:
            PositionGoalService().create_primary_goal_preview(
                session,
                request_id="preview-idempotency",
                user_id=user_id,
                position_id=str(uuid4()),
                eligibility_snapshot_id=snapshot_id,
                learner_profile_version=version,
                position_data_version="guokao-2026-20260712",
                target_score=_target_score(),
                now=NOW,
            )


def test_preview_rejects_a_snapshot_owned_by_another_user(
    db_session_factory: sessionmaker[Session],
) -> None:
    owner_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    other_user_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id) values (:user_id)"),
            {"user_id": other_user_id},
        )
        session.execute(
            text("insert into learner.profiles (user_id, profile_version) values (:user_id, 1)"),
            {"user_id": other_user_id},
        )

    with pytest.raises(PositionGoalContractError, match="owned"):
        with db_session_factory.begin() as session:
            PositionGoalService().create_primary_goal_preview(
                session,
                request_id="preview-foreign-snapshot",
                user_id=other_user_id,
                position_id=position_id,
                eligibility_snapshot_id=snapshot_id,
                learner_profile_version=version,
                position_data_version="guokao-2026-20260712",
                target_score=_target_score(),
                now=NOW,
            )

    assert owner_id != other_user_id


def test_new_goal_version_retires_previous_primary_without_destroying_history(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    service = PositionGoalService()
    first_preview = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-change-1",
    )
    first_command = _command(
        user_id=user_id,
        preview=first_preview,
    )
    with db_session_factory.begin() as session:
        first = service.confirm_primary_goal(session, command=first_command, now=NOW)
    second_preview = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-change-2",
        now=NOW + timedelta(seconds=1),
    )
    second_command = _command(
        user_id=user_id,
        preview=second_preview,
        command_id="select-primary-2",
        reason="根据最新策略重新确认",
    )
    with db_session_factory.begin() as session:
        second = service.confirm_primary_goal(
            session,
            command=second_command,
            now=NOW + timedelta(seconds=2),
        )

    with db_session_factory() as session:
        rows = (
            session.execute(
                text(
                    """
                select goal_version_id, status, supersedes_goal_version_id
                from learner.position_goal_versions
                where user_id = :user_id
                order by created_at, goal_version_id
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )
    by_id = {str(row["goal_version_id"]): row for row in rows}
    assert by_id[first.goal_version_id]["status"] == "retired"
    assert by_id[second.goal_version_id]["status"] == "confirmed"
    assert str(by_id[second.goal_version_id]["supersedes_goal_version_id"]) == first.goal_version_id


def test_stale_profile_version_cannot_confirm_a_goal(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    preview = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-before-profile-change",
    )
    with db_session_factory.begin() as session:
        QualificationRepository().replace_facts(
            session,
            user_id=user_id,
            command_id="change-profile-before-goal",
            mutations=[
                FactMutation(
                    key="major",
                    value="法学",
                    verification_status="confirmed",
                    evidence=(FactEvidenceInput(source_type="learner_report"),),
                )
            ],
            now=NOW,
        )

    with pytest.raises(PositionGoalContractError, match="profile changed"):
        with db_session_factory.begin() as session:
            PositionGoalService().confirm_primary_goal(
                session,
                command=_command(
                    user_id=user_id,
                    preview=preview,
                ),
                now=NOW,
            )


def test_expired_preview_cannot_confirm_a_goal(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    preview = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-expired",
    )

    with pytest.raises(PositionGoalContractError, match="expired"):
        with db_session_factory.begin() as session:
            PositionGoalService().confirm_primary_goal(
                session,
                command=_command(user_id=user_id, preview=preview),
                now=NOW + timedelta(minutes=16),
            )


def test_target_score_expiry_is_enforced_before_preview_and_confirmation(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    expiring_target = _target_score().model_copy(update={"valid_until": NOW + timedelta(minutes=5)})
    with db_session_factory.begin() as session:
        preview = PositionGoalService().create_primary_goal_preview(
            session,
            request_id="preview-target-expiry",
            user_id=user_id,
            position_id=position_id,
            eligibility_snapshot_id=snapshot_id,
            learner_profile_version=version,
            position_data_version="guokao-2026-20260712",
            target_score=expiring_target,
            now=NOW,
        )

    with pytest.raises(TargetScoreExpiredError):
        with db_session_factory.begin() as session:
            PositionGoalService().confirm_primary_goal(
                session,
                command=_command(user_id=user_id, preview=preview),
                now=NOW + timedelta(minutes=6),
            )

    expired_target = _target_score().model_copy(
        update={
            "generated_at": NOW - timedelta(days=1),
            "valid_until": NOW - timedelta(seconds=1),
        }
    )
    with pytest.raises(TargetScoreExpiredError):
        with db_session_factory.begin() as session:
            PositionGoalService().create_primary_goal_preview(
                session,
                request_id="preview-already-expired-target",
                user_id=user_id,
                position_id=position_id,
                eligibility_snapshot_id=snapshot_id,
                learner_profile_version=version,
                position_data_version="guokao-2026-20260712",
                target_score=expired_target,
                now=NOW,
            )


def test_goal_change_after_preview_requires_a_new_preview(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, position_id, version, snapshot_id = _seed_context(db_session_factory)
    first_preview = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-concurrent-first",
    )
    stale_preview = _create_preview(
        db_session_factory,
        user_id=user_id,
        position_id=position_id,
        version=version,
        snapshot_id=snapshot_id,
        request_id="preview-concurrent-stale",
    )
    with db_session_factory.begin() as session:
        PositionGoalService().confirm_primary_goal(
            session,
            command=_command(user_id=user_id, preview=first_preview),
            now=NOW,
        )

    with pytest.raises(PositionGoalContractError, match="changed after preview"):
        with db_session_factory.begin() as session:
            PositionGoalService().confirm_primary_goal(
                session,
                command=_command(
                    user_id=user_id,
                    preview=stale_preview,
                    command_id="select-primary-after-stale-preview",
                ),
                now=NOW + timedelta(seconds=1),
            )
