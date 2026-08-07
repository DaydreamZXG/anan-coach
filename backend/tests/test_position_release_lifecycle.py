from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database_compatibility import load_artifact_database_compatibility
from app.domains.positions.discovery import PositionDiscoveryService
from app.domains.qualification.repository import (
    PositionRuleReleaseUnavailable,
    QualificationRepository,
)
from app.domains.qualification.rule_releases import (
    PositionReleaseWindowInput,
    PositionRuleReleaseService,
    RuleReleaseContractError,
)
from scripts.compile_position_rule_release import _load_window_manifest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
PRIMARY_OPEN = datetime(2026, 10, 15, 0, tzinfo=UTC)
PRIMARY_CLOSE = datetime(2026, 10, 25, 0, tzinfo=UTC)
SUPPLEMENTAL_OPEN = datetime(2026, 11, 10, 0, tzinfo=UTC)
SUPPLEMENTAL_CLOSE = datetime(2026, 11, 15, 0, tzinfo=UTC)
ACTIVATED_AT = datetime(2026, 10, 10, 0, tzinfo=UTC)


def _window(
    *,
    kind: Literal["primary", "supplemental"],
    sequence_no: int,
    opens_at: datetime,
    closes_at: datetime,
    marker: str,
) -> PositionReleaseWindowInput:
    return PositionReleaseWindowInput(
        kind=kind,
        sequence_no=sequence_no,
        opens_at=opens_at,
        closes_at=closes_at,
        official_source_url=f"https://example.gov.cn/guokao/{marker}",
        official_source_title=f"公务员招录公告-{marker}",
        official_source_published_at=opens_at - timedelta(days=10),
        official_source_received_at=opens_at - timedelta(days=9),
        official_source_sha256=(marker[0].lower() if marker[0].isalnum() else "a") * 64,
        official_source_receipt=f"receipt-{marker}",
    )


def _primary_window(
    *,
    opens_at: datetime = PRIMARY_OPEN,
    closes_at: datetime = PRIMARY_CLOSE,
    marker: str = "a-primary",
) -> PositionReleaseWindowInput:
    return _window(
        kind="primary",
        sequence_no=1,
        opens_at=opens_at,
        closes_at=closes_at,
        marker=marker,
    )


def _supplemental_window(
    *,
    opens_at: datetime = SUPPLEMENTAL_OPEN,
    closes_at: datetime = SUPPLEMENTAL_CLOSE,
    marker: str = "b-supplemental",
) -> PositionReleaseWindowInput:
    return replace(
        _window(
            kind="supplemental",
            sequence_no=1,
            opens_at=opens_at,
            closes_at=closes_at,
            marker=marker,
        ),
        official_source_published_at=ACTIVATED_AT - timedelta(days=2),
        official_source_received_at=ACTIVATED_AT - timedelta(days=1),
    )


def _seed_compiled_release(
    session: Session,
    *,
    exam_year: int,
    rule_set_version: str,
    position_data_version: str,
    windows: list[PositionReleaseWindowInput] | None,
    activated_at: datetime,
) -> tuple[str, str]:
    position_id = str(uuid4())
    session.execute(
        text(
            """
            insert into product.positions (
              position_id, source_id, year, position_code, name,
              department_full, system_code, region, city, paper_type,
              headcount, education_min, major_required, political_req,
              service_min_yrs, hukou_req, remark, raw_json
            ) values (
              cast(:position_id as uuid), :source_id, :exam_year, :position_code,
              '一级行政执法员', '国家税务总局生命周期测试局', 'tax',
              '江西', '南昌市', 'administrativeLaw', 2, '不限', '不限',
              '不限', 0, '不限', '无特殊要求', '{}'::jsonb
            )
            """
        ),
        {
            "position_id": position_id,
            "source_id": f"position-release-lifecycle-{position_data_version}",
            "exam_year": exam_year,
            "position_code": f"300110{exam_year}001",
        },
    )
    service = PositionRuleReleaseService()
    release_id = service.ensure_draft(
        session,
        exam_code="guokao",
        exam_year=exam_year,
        rule_set_version=rule_set_version,
        position_data_version=position_data_version,
    )
    service.compile_draft(
        session,
        release_id=release_id,
        compiled_at=activated_at,
    )
    if windows is not None:
        service.configure_windows(
            session,
            release_id=release_id,
            windows=windows,
        )
    _seed_position_score(
        session,
        position_id=position_id,
        exam_year=exam_year,
        marker=position_data_version,
    )
    return release_id, position_id


def _seed_position_score(
    session: Session,
    *,
    position_id: str,
    exam_year: int,
    marker: str,
) -> None:
    evidence_id = session.execute(
        text(
            """
            insert into product.position_entry_score_evidence (
              year, department_code, position_code, agency, bureau,
              position_title, entry_score_min, source_name,
              source_file_sha256, candidate_count
            ) values (
              :score_year, '000001', :position_code,
              '国家税务总局生命周期测试局', '测试司局', '一级行政执法员',
              126, 'position-release-lifecycle-fixture', :source_sha256, 5
            ) returning evidence_id
            """
        ),
        {
            "score_year": exam_year - 1,
            "position_code": f"300110{exam_year}001",
            "source_sha256": marker.encode("utf-8").hex()[:64].ljust(64, "0"),
        },
    ).scalar_one()
    session.execute(
        text(
            """
            insert into product.position_entry_score_links (
              position_id, evidence_id, match_method, confidence
            ) values (
              cast(:position_id as uuid), :evidence_id, 'fixture', 'high'
            )
            """
        ),
        {"position_id": position_id, "evidence_id": evidence_id},
    )


def _seed_user(session: Session, *, marker: str) -> str:
    user_id = str(uuid4())
    session.execute(
        text(
            """
            insert into learner.users (user_id, display_name)
            values (cast(:user_id as uuid), :display_name)
            """
        ),
        {"user_id": user_id, "display_name": f"生命周期学习者-{marker}"},
    )
    session.execute(
        text(
            """
            insert into learner.profiles (
              user_id, profile_version, created_at, updated_at
            ) values (
              cast(:user_id as uuid), 1, transaction_timestamp(),
              transaction_timestamp()
            )
            """
        ),
        {"user_id": user_id},
    )
    return user_id


def _insert_goal(
    session: Session,
    *,
    user_id: str,
    position_id: str,
    rule_set_version: str,
    position_data_version: str,
    created_at: datetime,
    valid_until: datetime,
) -> str:
    snapshot_id = str(uuid4())
    session.execute(
        text(
            """
            insert into learner.eligibility_snapshots (
              snapshot_id, user_id, position_id, learner_profile_version,
              rule_set_version, position_data_version, status, checks,
              missing_fact_keys, evaluated_at
            ) values (
              cast(:snapshot_id as uuid), cast(:user_id as uuid),
              cast(:position_id as uuid), 1, :rule_set_version,
              :position_data_version, 'eligible', '[]'::jsonb, '[]'::jsonb,
              :evaluated_at
            )
            """
        ),
        {
            "snapshot_id": snapshot_id,
            "user_id": user_id,
            "position_id": position_id,
            "rule_set_version": rule_set_version,
            "position_data_version": position_data_version,
            "evaluated_at": created_at,
        },
    )
    goal_version_id = str(uuid4())
    session.execute(
        text(
            """
            insert into learner.position_goal_versions (
              goal_version_id, user_id, position_id, role, status,
              target_score_lower, target_score_recommended, target_score_upper,
              target_score_evidence_level, target_score_sample_count,
              target_score_valid_until, learner_profile_version,
              eligibility_snapshot_id, position_data_version, created_at
            ) values (
              cast(:goal_version_id as uuid), cast(:user_id as uuid),
              cast(:position_id as uuid), 'primary', 'confirmed',
              124, 127, 130, 'direct', 1, :valid_until, 1,
              cast(:snapshot_id as uuid), :position_data_version, :created_at
            )
            """
        ),
        {
            "goal_version_id": goal_version_id,
            "user_id": user_id,
            "position_id": position_id,
            "valid_until": valid_until,
            "snapshot_id": snapshot_id,
            "position_data_version": position_data_version,
            "created_at": created_at,
        },
    )
    return goal_version_id


def test_activation_requires_windows_and_official_source_evidence(
    db_session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    service = PositionRuleReleaseService()
    with db_session_factory.begin() as session:
        release_id, _ = _seed_compiled_release(
            session,
            exam_year=2026,
            rule_set_version="guokao-2026-lifecycle-v1",
            position_data_version="guokao-2026-lifecycle-data-v1",
            windows=None,
            activated_at=ACTIVATED_AT,
        )

    with pytest.raises(RuleReleaseContractError, match="audited primary"):
        with db_session_factory.begin() as session:
            service.activate(
                session,
                release_id=release_id,
                activated_at=ACTIVATED_AT,
            )

    missing_receipt = _primary_window()
    missing_receipt = replace(missing_receipt, official_source_receipt=" ")
    with pytest.raises(RuleReleaseContractError, match="title and receipt"):
        with db_session_factory.begin() as session:
            service.configure_windows(
                session,
                release_id=release_id,
                windows=[missing_receipt],
            )

    reversed_window = _primary_window(
        opens_at=PRIMARY_CLOSE,
        closes_at=PRIMARY_OPEN,
    )
    with pytest.raises(RuleReleaseContractError, match="opensAt"):
        with db_session_factory.begin() as session:
            service.configure_windows(
                session,
                release_id=release_id,
                windows=[reversed_window],
            )

    future_source = replace(
        _primary_window(),
        official_source_published_at=datetime(2099, 1, 1, tzinfo=UTC),
        official_source_received_at=datetime(2099, 1, 2, tzinfo=UTC),
    )
    with db_session_factory.begin() as session:
        service.configure_windows(
            session,
            release_id=release_id,
            windows=[future_source],
        )
    with pytest.raises(RuleReleaseContractError, match="received before"):
        with db_session_factory.begin() as session:
            service.activate(
                session,
                release_id=release_id,
                activated_at=ACTIVATED_AT,
            )
    with pytest.raises(IntegrityError, match="received before"):
        with db_session_factory.begin() as session:
            session.execute(
                text(
                    """
                    update product.position_rule_releases
                    set status = 'active', activated_at = :activated_at
                    where release_id = cast(:release_id as uuid)
                    """
                ),
                {"release_id": release_id, "activated_at": ACTIVATED_AT},
            )

    with db_session_factory.begin() as session:
        service.configure_windows(
            session,
            release_id=release_id,
            windows=[_primary_window()],
        )
        service.activate(
            session,
            release_id=release_id,
            activated_at=ACTIVATED_AT,
        )

    with db_session_factory() as session:
        release = (
            session.execute(
                text(
                    """
                select status, activated_at
                from product.position_rule_releases
                where release_id = cast(:release_id as uuid)
                """
                ),
                {"release_id": release_id},
            )
            .mappings()
            .one()
        )
        source = (
            session.execute(
                text(
                    """
                select official_source_url, official_source_sha256,
                       official_source_receipt
                from product.position_release_windows
                where release_id = cast(:release_id as uuid)
                """
                ),
                {"release_id": release_id},
            )
            .mappings()
            .one()
        )
    assert release["status"] == "active"
    assert release["activated_at"] == ACTIVATED_AT
    assert source["official_source_url"].startswith("https://example.gov.cn/")
    assert len(source["official_source_sha256"]) == 64
    assert source["official_source_receipt"] == "receipt-a-primary"

    manifest_path = tmp_path / "position-release-windows.json"
    manifest_path.write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "kind": "primary",
                        "sequenceNo": 1,
                        "opensAt": PRIMARY_OPEN.isoformat(),
                        "closesAt": PRIMARY_CLOSE.isoformat(),
                        "officialSourceUrl": source["official_source_url"],
                        "officialSourceTitle": "公务员招录公告-a-primary",
                        "officialSourcePublishedAt": (
                            PRIMARY_OPEN - timedelta(days=10)
                        ).isoformat(),
                        "officialSourceReceivedAt": (PRIMARY_OPEN - timedelta(days=9)).isoformat(),
                        "officialSourceSha256": source["official_source_sha256"],
                        "officialSourceReceipt": source["official_source_receipt"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    parsed_manifest = _load_window_manifest(manifest_path)
    assert parsed_manifest == [_primary_window()]


def test_release_window_phase_boundaries_and_gap_fail_closed(
    db_session_factory: sessionmaker[Session],
) -> None:
    repository = QualificationRepository()
    with db_session_factory.begin() as session:
        assert (
            repository.release_lifecycle(
                session,
                now=ACTIVATED_AT,
            ).phase
            == "no_release"
        )
        release_id, _ = _seed_compiled_release(
            session,
            exam_year=2026,
            rule_set_version="guokao-2026-boundaries-v1",
            position_data_version="guokao-2026-boundaries-data-v1",
            windows=[_primary_window(), _supplemental_window()],
            activated_at=ACTIVATED_AT,
        )
        PositionRuleReleaseService().activate(
            session,
            release_id=release_id,
            activated_at=ACTIVATED_AT,
        )

    cases = (
        (PRIMARY_OPEN - timedelta(microseconds=1), "upcoming", None),
        (PRIMARY_OPEN, "open", "primary"),
        (PRIMARY_CLOSE - timedelta(microseconds=1), "open", "primary"),
        (PRIMARY_CLOSE, "gap", None),
        (SUPPLEMENTAL_OPEN - timedelta(microseconds=1), "gap", None),
        (SUPPLEMENTAL_OPEN, "open", "supplemental"),
        (SUPPLEMENTAL_CLOSE - timedelta(microseconds=1), "open", "supplemental"),
        (SUPPLEMENTAL_CLOSE, "closed", None),
    )
    with db_session_factory() as session:
        for checked_at, expected_phase, expected_kind in cases:
            lifecycle = repository.release_lifecycle(session, now=checked_at)
            assert lifecycle.phase == expected_phase
            if expected_phase == "open":
                release = repository.active_release(session, now=checked_at)
                assert release.current_window.kind == expected_kind
            else:
                with pytest.raises(PositionRuleReleaseUnavailable) as captured:
                    repository.active_release(session, now=checked_at)
                assert (
                    captured.value.code
                    == {
                        "upcoming": "position_release_upcoming",
                        "gap": "position_release_between_windows",
                        "closed": "position_release_closed",
                    }[expected_phase]
                )
                assert captured.value.details["phase"] == expected_phase
                assert captured.value.details["examYear"] == 2026
                assert captured.value.details["officialSourceUrl"].startswith("https://example.gov")


def test_closed_release_is_historical_and_excluded_from_current_recommendations(
    db_session_factory: sessionmaker[Session],
) -> None:
    service = PositionRuleReleaseService()
    user_id: str
    old_goal_id: str
    with db_session_factory.begin() as session:
        old_release_id, old_position_id = _seed_compiled_release(
            session,
            exam_year=2026,
            rule_set_version="guokao-2026-history-v1",
            position_data_version="guokao-2026-history-data-v1",
            windows=[_primary_window()],
            activated_at=ACTIVATED_AT,
        )
        service.activate(
            session,
            release_id=old_release_id,
            activated_at=ACTIVATED_AT,
        )
        user_id = _seed_user(session, marker="historical")
        old_goal_id = _insert_goal(
            session,
            user_id=user_id,
            position_id=old_position_id,
            rule_set_version="guokao-2026-history-v1",
            position_data_version="guokao-2026-history-data-v1",
            created_at=PRIMARY_OPEN + timedelta(days=1),
            valid_until=PRIMARY_CLOSE,
        )
        next_open = datetime(2027, 1, 10, tzinfo=UTC)
        next_close = datetime(2027, 1, 20, tzinfo=UTC)
        next_release_id, next_position_id = _seed_compiled_release(
            session,
            exam_year=2027,
            rule_set_version="guokao-2027-current-v1",
            position_data_version="guokao-2027-current-data-v1",
            windows=[
                _primary_window(
                    opens_at=next_open,
                    closes_at=next_close,
                    marker="d-2027-primary",
                )
            ],
            activated_at=datetime(2027, 1, 5, tzinfo=UTC),
        )
        service.activate(
            session,
            release_id=next_release_id,
            activated_at=datetime(2027, 1, 5, tzinfo=UTC),
        )

    current_time = datetime(2027, 1, 12, tzinfo=UTC)
    with db_session_factory() as session:
        result = PositionDiscoveryService().search(
            session,
            user_id=user_id,
            exam_year=None,
            region=None,
            city=None,
            query=None,
            limit=20,
            cursor=None,
            now=current_time,
        )
        historical_goal = (
            session.execute(
                text(
                    """
                select status, position_data_version
                from learner.position_goal_versions
                where goal_version_id = cast(:goal_version_id as uuid)
                """
                ),
                {"goal_version_id": old_goal_id},
            )
            .mappings()
            .one()
        )
    assert result.exam_year == 2027
    assert [item.position_id for item in result.items] == [next_position_id]
    assert result.items[0].is_primary_goal is False
    assert historical_goal["status"] == "confirmed"
    assert historical_goal["position_data_version"] == "guokao-2026-history-data-v1"

    with db_session_factory.begin() as session:
        closed_user_id = _seed_user(session, marker="closed-new-confirmation")
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                _insert_goal(
                    session,
                    user_id=closed_user_id,
                    position_id=old_position_id,
                    rule_set_version="guokao-2026-history-v1",
                    position_data_version="guokao-2026-history-data-v1",
                    created_at=PRIMARY_CLOSE,
                    valid_until=PRIMARY_CLOSE,
                )


def test_supplemental_window_replay_is_stable_and_target_expires_at_close(
    db_session_factory: sessionmaker[Session],
) -> None:
    service = PositionRuleReleaseService()
    checked_at = SUPPLEMENTAL_OPEN + timedelta(days=1)
    with db_session_factory.begin() as session:
        release_id, position_id = _seed_compiled_release(
            session,
            exam_year=2026,
            rule_set_version="guokao-2026-supplemental-v1",
            position_data_version="guokao-2026-supplemental-data-v1",
            windows=[_primary_window(), _supplemental_window()],
            activated_at=ACTIVATED_AT,
        )
        service.activate(
            session,
            release_id=release_id,
            activated_at=ACTIVATED_AT,
        )
        user_id = _seed_user(session, marker="supplemental")
        primary_goal_user_id = _seed_user(
            session,
            marker="primary-window-historical",
        )
        _insert_goal(
            session,
            user_id=primary_goal_user_id,
            position_id=position_id,
            rule_set_version="guokao-2026-supplemental-v1",
            position_data_version="guokao-2026-supplemental-data-v1",
            created_at=PRIMARY_OPEN + timedelta(days=1),
            valid_until=PRIMARY_CLOSE,
        )

    with db_session_factory() as session:
        first = PositionDiscoveryService().search(
            session,
            user_id=user_id,
            exam_year=2026,
            region=None,
            city=None,
            query=None,
            limit=20,
            cursor=None,
            now=checked_at,
        )
        replay = PositionDiscoveryService().search(
            session,
            user_id=user_id,
            exam_year=2026,
            region=None,
            city=None,
            query=None,
            limit=20,
            cursor=None,
            now=checked_at,
        )
        historical_primary = PositionDiscoveryService().search(
            session,
            user_id=primary_goal_user_id,
            exam_year=2026,
            region=None,
            city=None,
            query=None,
            limit=20,
            cursor=None,
            now=checked_at,
        )
        release = QualificationRepository().active_release(
            session,
            exam_year=2026,
            now=checked_at,
        )
    assert first.model_dump(mode="json") == replay.model_dump(mode="json")
    assert release.current_window.kind == "supplemental"
    assert release.current_window.closes_at == SUPPLEMENTAL_CLOSE
    target_score = first.items[0].target_score
    assert target_score is not None
    assert target_score.valid_until == SUPPLEMENTAL_CLOSE
    assert historical_primary.items[0].is_primary_goal is False

    with db_session_factory.begin() as session:
        goal_id = _insert_goal(
            session,
            user_id=user_id,
            position_id=position_id,
            rule_set_version="guokao-2026-supplemental-v1",
            position_data_version="guokao-2026-supplemental-data-v1",
            created_at=checked_at,
            valid_until=SUPPLEMENTAL_CLOSE,
        )
        stored_valid_until = session.execute(
            text(
                """
                select target_score_valid_until
                from learner.position_goal_versions
                where goal_version_id = cast(:goal_version_id as uuid)
                """
            ),
            {"goal_version_id": goal_id},
        ).scalar_one()
    assert stored_valid_until == SUPPLEMENTAL_CLOSE


def test_position_release_window_migration_upgrades_forward_and_matches_artifact_head(
    db_engine_at_revision_77: Engine,
) -> None:
    db_engine = db_engine_at_revision_77
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        db_engine.url.render_as_string(hide_password=False),
    )
    script = ScriptDirectory.from_config(config)
    compatibility = load_artifact_database_compatibility(
        PROJECT_ROOT / "ops" / "release-db-compatibility.env",
        backend_root=BACKEND_ROOT,
    )
    assert script.get_heads() == ["20260807_99"]
    assert compatibility.migration_head == "20260807_99"
    assert compatibility.compatible_revisions == {"20260807_99"}
    assert "position_release_windows" not in inspect(db_engine).get_table_names(schema="product")

    legacy_release_id = str(uuid4())
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  cast(:release_id as uuid), 'guokao', 2026,
                  'legacy-technical-active', 'legacy-position-data',
                  'active', :activated_at
                )
                """
            ),
            {"release_id": legacy_release_id, "activated_at": ACTIVATED_AT},
        )

    command.upgrade(config, "20260730_78")
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("select count(*) from product.position_release_windows")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    """
                select status
                from product.position_rule_releases
                where release_id = cast(:release_id as uuid)
                """
                ),
                {"release_id": legacy_release_id},
            ).scalar_one()
            == "active"
        )

    migration78 = import_module("migrations.versions.20260730_78_position_release_windows")
    with pytest.raises(RuntimeError, match="forward-only"):
        migration78.downgrade()
    assert "position_release_windows" in inspect(db_engine).get_table_names(schema="product")
    command.upgrade(config, "head")
    with db_engine.connect() as connection:
        assert (
            connection.execute(text("select version_num from alembic_version")).scalar_one()
            == "20260807_99"
        )
        assert "position_release_windows" in inspect(db_engine).get_table_names(schema="product")
