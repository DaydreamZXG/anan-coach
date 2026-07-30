from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.companion.repository import CompanionRepository
from app.domains.planning.repository import PlanningRepository
from app.domains.positions.repository import PositionRepository
from app.domains.reporting.repository import ReportingRepository
from app.main import create_app


def test_frozen_position_versions_reject_late_batch_and_snapshot_inserts(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory.begin() as session:
        first_position = _insert_position(
            session,
            source_id="frozen-snapshot-first",
            year=2026,
            position_code="300110820001",
        )
        session.execute(
            text(
                """
                insert into product.position_import_batches (
                  position_data_version, source_sha256, position_count
                ) values ('position-freeze-v1', :sha256, 1)
                """
            ),
            {"sha256": "1" * 64},
        )
        _insert_snapshot(
            session,
            position_id=first_position,
            source_id="frozen-snapshot-first",
            year=2026,
            position_code="300110820001",
            position_data_version="position-freeze-v1",
        )
        frozen_release_id, _ = _insert_release(
            session,
            exam_year=2026,
            rule_set_version="position-freeze-rules-v1",
            position_data_version="position-freeze-v1",
        )
        _activate_release(session, release_id=frozen_release_id)
        stored_digest = session.execute(
            text(
                """
                select content_digest
                from product.position_rule_releases
                where release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": frozen_release_id},
        ).scalar_one()

        late_position = _insert_position(
            session,
            source_id="frozen-snapshot-late",
            year=2026,
            position_code="300110820002",
        )
        with pytest.raises(DBAPIError, match="cannot accept additional"):
            with session.begin_nested():
                _insert_snapshot(
                    session,
                    position_id=late_position,
                    source_id="frozen-snapshot-late",
                    year=2026,
                    position_code="300110820002",
                    position_data_version="position-freeze-v1",
                )

        legacy_position = _insert_position(
            session,
            source_id="frozen-batch-legacy",
            year=2025,
            position_code="300110820003",
        )
        assert legacy_position
        legacy_release_id, _ = _insert_release(
            session,
            exam_year=2025,
            rule_set_version="position-freeze-rules-legacy",
            position_data_version="position-freeze-legacy",
        )
        _activate_release(session, release_id=legacy_release_id)
        with pytest.raises(DBAPIError, match="cannot accept additional"):
            with session.begin_nested():
                session.execute(
                    text(
                        """
                        insert into product.position_import_batches (
                          position_data_version, source_sha256, position_count
                        ) values ('position-freeze-legacy', :sha256, 1)
                        """
                    ),
                    {"sha256": "2" * 64},
                )

        assert (
            session.execute(
                text(
                    """
                select count(*)
                from product.position_snapshots
                where position_data_version = 'position-freeze-v1'
                """
                )
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    """
                select product.compute_position_release_content_digest(
                  cast(:release_id as uuid)
                )
                """
                ),
                {"release_id": frozen_release_id},
            ).scalar_one()
            == stored_digest
        )


def test_frozen_score_batches_and_rows_guard_old_and_new_versions_for_all_dml(
    db_session_factory: sessionmaker[Session],
) -> None:
    immutable_message = "active or retired position score snapshots are immutable"
    with db_session_factory.begin() as session:
        position_id = _insert_position(
            session,
            source_id="frozen-score-position",
            year=2026,
            position_code="300110820051",
        )
        _insert_score_history(
            session,
            position_id=position_id,
            source_id="frozen-score-history",
            position_code="300110820051",
            score_year=2025,
            score=127,
        )
        frozen_release_id, _ = _insert_release(
            session,
            exam_year=2026,
            rule_set_version="frozen-score-rules-v1",
            position_data_version="frozen-score-data-v1",
        )
        _activate_release(session, release_id=frozen_release_id)
        empty_frozen_release_id, _ = _insert_release(
            session,
            exam_year=2099,
            rule_set_version="empty-frozen-score-rules-v1",
            position_data_version="empty-frozen-score-data-v1",
        )
        _activate_release(session, release_id=empty_frozen_release_id)
        _insert_release(
            session,
            exam_year=2027,
            rule_set_version="draft-score-rules-v1",
            position_data_version="draft-score-data-v1",
        )
        session.execute(
            text(
                """
                insert into product.position_score_snapshot_batches (
                  position_data_version, source_sha256, score_evidence_count
                ) values
                  ('draft-score-data-v1', repeat('b', 64), 1),
                  ('draft-score-batch-move-v1', repeat('c', 64), 0)
                """
            )
        )
        session.execute(
            text(
                """
                insert into product.position_score_snapshots (
                  position_data_version, position_id, evidence_kind,
                  evidence_key, score_year, entry_score_min,
                  entry_score_max, source_fingerprint, source_json
                ) values (
                  'draft-score-data-v1', cast(:position_id as uuid),
                  'gwy_history', 'draft-score-evidence', 2025,
                  126, 126, repeat('d', 64),
                  '{"fixture":"draft-score"}'::jsonb
                )
                """
            ),
            {"position_id": position_id},
        )
        assert (
            session.execute(
                text(
                    """
                select count(*)
                from product.position_score_snapshots
                where position_data_version = 'frozen-score-data-v1'
                """
                )
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    """
                select score_evidence_count
                from product.position_score_snapshot_batches
                where position_data_version = 'empty-frozen-score-data-v1'
                """
                )
            ).scalar_one()
            == 0
        )

        batch_mutations = (
            """
            insert into product.position_score_snapshot_batches (
              position_data_version, source_sha256, score_evidence_count
            ) values (
              'empty-frozen-score-data-v1', repeat('e', 64), 0
            )
            """,
            """
            update product.position_score_snapshot_batches
            set source_sha256 = repeat('e', 64)
            where position_data_version = 'empty-frozen-score-data-v1'
            """,
            """
            delete from product.position_score_snapshot_batches
            where position_data_version = 'empty-frozen-score-data-v1'
            """,
            """
            update product.position_score_snapshot_batches
            set position_data_version = 'unbound-draft-score-v1'
            where position_data_version = 'empty-frozen-score-data-v1'
            """,
            """
            update product.position_score_snapshot_batches
            set position_data_version = 'empty-frozen-score-data-v1'
            where position_data_version = 'draft-score-batch-move-v1'
            """,
        )
        for mutation in batch_mutations:
            with pytest.raises(DBAPIError, match=immutable_message):
                with session.begin_nested():
                    session.execute(text(mutation))

        row_mutations = (
            """
            insert into product.position_score_snapshots (
              position_data_version, position_id, evidence_kind,
              evidence_key, score_year, entry_score_min,
              entry_score_max, source_fingerprint, source_json
            ) values (
              'empty-frozen-score-data-v1', cast(:position_id as uuid),
              'gwy_history', 'late-frozen-score-evidence', 2025,
              128, 128, repeat('f', 64),
              '{"fixture":"late-score"}'::jsonb
            )
            """,
            """
            update product.position_score_snapshots
            set entry_score_min = 299
            where position_data_version = 'frozen-score-data-v1'
            """,
            """
            delete from product.position_score_snapshots
            where position_data_version = 'frozen-score-data-v1'
            """,
            """
            update product.position_score_snapshots
            set position_data_version = 'draft-score-data-v1'
            where position_data_version = 'frozen-score-data-v1'
            """,
            """
            update product.position_score_snapshots
            set position_data_version = 'empty-frozen-score-data-v1'
            where position_data_version = 'draft-score-data-v1'
              and evidence_key = 'draft-score-evidence'
            """,
        )
        for mutation in row_mutations:
            with pytest.raises(DBAPIError, match=immutable_message):
                with session.begin_nested():
                    session.execute(text(mutation), {"position_id": position_id})

        counts = dict(
            session.execute(
                text(
                    """
                    select position_data_version, count(*)::integer
                    from product.position_score_snapshots
                    where position_data_version in (
                      'frozen-score-data-v1',
                      'empty-frozen-score-data-v1',
                      'draft-score-data-v1'
                    )
                    group by position_data_version
                    """
                )
            ).all()
        )
        assert counts == {
            "draft-score-data-v1": 1,
            "frozen-score-data-v1": 1,
        }
        assert (
            session.execute(
                text(
                    """
                select count(*)
                from product.position_score_snapshot_batches
                where position_data_version in (
                  'frozen-score-data-v1',
                  'empty-frozen-score-data-v1',
                  'draft-score-data-v1',
                  'draft-score-batch-move-v1'
                )
                """
                )
            ).scalar_one()
            == 4
        )
        assert (
            session.execute(
                text(
                    """
                select count(*)
                from product.position_score_snapshot_batches
                where position_data_version = 'unbound-draft-score-v1'
                """
                )
            ).scalar_one()
            == 0
        )


def test_position_version_exam_identity_and_target_score_cohorts_are_year_isolated(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory.begin() as session:
        _insert_position(
            session,
            source_id="shared-version-2025",
            year=2025,
            position_code="300110820101",
        )
        _insert_position(
            session,
            source_id="shared-version-2026",
            year=2026,
            position_code="300110820102",
        )
        release_2025, _ = _insert_release(
            session,
            exam_year=2025,
            rule_set_version="shared-version-rules-2025",
            position_data_version="shared-position-version",
        )
        _activate_release(session, release_id=release_2025)
        release_2026, _ = _insert_release(
            session,
            exam_year=2026,
            rule_set_version="shared-version-rules-2026",
            position_data_version="shared-position-version",
        )
        with pytest.raises(DBAPIError, match="cannot be reused"):
            with session.begin_nested():
                _activate_release(session, release_id=release_2026)
        assert (
            session.execute(
                text(
                    """
                select status
                from product.position_rule_releases
                where release_id = cast(:release_id as uuid)
                """
                ),
                {"release_id": release_2026},
            ).scalar_one()
            == "draft"
        )

    with db_session_factory.begin() as session:
        target = _insert_position(
            session,
            source_id="year-cohort-target",
            year=2026,
            position_code="300110820201",
            region="江西",
            city="赣州市",
        )
        prior_year = _insert_position(
            session,
            source_id="year-cohort-2025",
            year=2025,
            position_code="300110820202",
            region="江西",
            city="赣州市",
        )
        current_year = _insert_position(
            session,
            source_id="year-cohort-2026",
            year=2026,
            position_code="300110820203",
            region="江西",
            city="赣州市",
        )
        _insert_score_history(
            session,
            position_id=prior_year,
            source_id="year-cohort-score-2025",
            position_code="300110820202",
            score_year=2024,
            score=199,
        )
        _insert_score_history(
            session,
            position_id=current_year,
            source_id="year-cohort-score-2026",
            position_code="300110820203",
            score_year=2024,
            score=129,
        )
        repository = PositionRepository(session)
        single = repository.list_target_score_points(
            position_id=target,
            basis_level="L4",
        )
        batch = repository.list_target_score_points_for_positions(
            position_ids=[target],
            basis_level="L4",
        )[target]

        assert {str(row["position_id"]) for row in single} == {current_year}
        assert {str(row["position_id"]) for row in batch} == {current_year}
        assert all(float(row["entry_score_min"]) < 150 for row in single)


def test_runtime_api_role_has_read_only_position_authority(
    db_engine: Engine,
) -> None:
    migration = import_module("migrations.versions.20260730_82_position_authority_hardening")
    tables = tuple(migration.POSITION_AUTHORITY_TABLES)
    views = tuple(migration.CURRENT_PROJECTION_VIEWS)
    with db_engine.begin() as connection:
        role_existed = bool(
            connection.execute(
                text("select exists (select 1 from pg_roles where rolname = 'gongkao_api')")
            ).scalar_one()
        )
        if not role_existed:
            connection.execute(text("create role gongkao_api nologin"))
        connection.exec_driver_sql("grant usage on schema product, learner to gongkao_api")
        connection.exec_driver_sql(f"grant all privileges on {', '.join(tables)} to gongkao_api")
        connection.exec_driver_sql(f"grant all privileges on {', '.join(views)} to gongkao_api")
        migration.harden_runtime_position_authority(connection)
        privileges: dict[tuple[str, str], set[str]] = {}
        for row in connection.execute(
            text(
                """
                select table_schema, table_name, privilege_type
                from information_schema.role_table_grants
                where grantee = 'gongkao_api'
                  and (
                    (table_schema = 'product' and table_name = any(:tables))
                    or
                    (table_schema = 'learner' and table_name = any(:views))
                  )
                """
            ),
            {
                "tables": [item.split(".", 1)[1] for item in tables],
                "views": [item.split(".", 1)[1] for item in views],
            },
        ).mappings():
            key = (str(row["table_schema"]), str(row["table_name"]))
            privileges.setdefault(key, set()).add(str(row["privilege_type"]))
        for qualified_name in (*tables, *views):
            schema, name = qualified_name.split(".", 1)
            assert privileges[(schema, name)] == {"SELECT"}

    with db_engine.connect() as connection:
        connection.execute(text("set role gongkao_api"))
        assert (
            connection.execute(
                text("select count(*) from product.position_rule_releases")
            ).scalar_one()
            >= 0
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    """
                    update product.position_rule_releases
                    set status = status
                    where false
                    """
                )
            )
        connection.rollback()

    if not role_existed:
        with db_engine.begin() as connection:
            connection.exec_driver_sql(
                f"revoke all privileges on {', '.join(views)} from gongkao_api"
            )
            connection.exec_driver_sql(
                f"revoke all privileges on {', '.join(tables)} from gongkao_api"
            )
            connection.exec_driver_sql(
                "revoke execute on function "
                "product.position_projection_is_current("
                "uuid, text, text, timestamptz) from gongkao_api"
            )
            connection.exec_driver_sql("revoke usage on schema product, learner from gongkao_api")
            connection.execute(text("drop role gongkao_api"))


def test_retired_release_hides_profile_and_current_goal_from_all_live_consumers(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "position-projection-token"
    user_id = _seed_user(db_session_factory, token=token)
    with db_session_factory.begin() as session:
        position_id = _insert_position(
            session,
            source_id="projection-position",
            year=2026,
            position_code="300110820301",
        )
        _insert_score_history(
            session,
            position_id=position_id,
            source_id="projection-score-2024",
            position_code="300110820301",
            score_year=2024,
            score=126,
        )
        _insert_score_history(
            session,
            position_id=position_id,
            source_id="projection-score-2025",
            position_code="300110820301",
            score_year=2025,
            score=128,
        )
        release_id, valid_until = _insert_release(
            session,
            exam_year=2026,
            rule_set_version="projection-rules-v1",
            position_data_version="projection-data-v1",
        )
        _activate_release(session, release_id=release_id)

    client = _client(db_session_factory)
    headers = {"Authorization": f"Bearer {token}"}
    update = client.put(
        "/api/v1/onboarding/profile",
        headers=headers,
        json={
            "education": "本科",
            "major": "法学",
            "freshGraduate": True,
            "politicalStatus": "群众",
            "grassrootsYears": 0,
            "studyMode": "parttime",
            "targetPositionId": position_id,
            "baselineSource": "self_report",
            "baselineXingceScore": 58,
            "baselineShenlunScore": 60,
            "baselineWeakModules": ["资料分析"],
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["targetPositionId"] == position_id
    assert update.json()["targetScore"] is not None

    with db_session_factory.begin() as session:
        profile = (
            session.execute(
                text(
                    """
                select profile_version, target_position_data_version
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert profile["target_position_data_version"] == "projection-data-v1"
        _complete_onboarding_projection(
            session,
            user_id=user_id,
            profile_version=int(profile["profile_version"]),
        )
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
                  cast(:position_id as uuid), :profile_version,
                  'projection-rules-v1', 'projection-data-v1', 'eligible',
                  '[]'::jsonb, '[]'::jsonb, statement_timestamp()
                )
                """
            ),
            {
                "snapshot_id": snapshot_id,
                "user_id": user_id,
                "position_id": position_id,
                "profile_version": int(profile["profile_version"]),
            },
        )
        session.execute(
            text(
                """
                insert into learner.position_goal_versions (
                  goal_version_id, user_id, position_id, role, status,
                  target_score_lower, target_score_recommended,
                  target_score_upper, target_score_evidence_level,
                  target_score_sample_count, target_score_valid_until,
                  learner_profile_version, eligibility_snapshot_id,
                  position_data_version, created_at
                ) values (
                  gen_random_uuid(), cast(:user_id as uuid),
                  cast(:position_id as uuid), 'primary', 'confirmed',
                  125, 128, 131, 'direct', 2, :valid_until,
                  :profile_version, cast(:snapshot_id as uuid),
                  'projection-data-v1', statement_timestamp()
                )
                """
            ),
            {
                "user_id": user_id,
                "position_id": position_id,
                "valid_until": valid_until,
                "profile_version": int(profile["profile_version"]),
                "snapshot_id": snapshot_id,
            },
        )

    assert (
        client.get("/api/v1/onboarding/profile", headers=headers).json()["targetPositionId"]
        == position_id
    )
    memory_before = client.get("/api/v1/learner/memory", headers=headers)
    assert memory_before.status_code == 200
    assert memory_before.json()["profile"]["targetPositionId"] == position_id
    taskstream_before = client.get("/api/v1/taskstream/today", headers=headers)
    assert taskstream_before.status_code == 200, taskstream_before.text
    assert taskstream_before.json()["target"]["scoreGapAvailable"] is True
    feed_before = client.get("/api/v1/feed/today", headers=headers)
    assert feed_before.status_code == 200, feed_before.text
    assert _goal_gap_target(feed_before.json()) is not None
    with db_session_factory() as session:
        assert (
            CompanionRepository(session)
            .get_context(
                user_id=user_id,
                node_id=None,
            )
            .target_score
            is not None
        )
        assert (
            ReportingRepository().current_goal(
                session,
                user_id=user_id,
            )
            is not None
        )
        assert (
            PlanningRepository()
            .goal_and_ability(
                session,
                user_id=user_id,
            )
            .goal_version_id
            is not None
        )

    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'retired'
                where release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": release_id},
        )
        with pytest.raises(DBAPIError, match="current open release"):
            with session.begin_nested():
                session.execute(
                    text(
                        """
                        update learner.profiles
                        set target_score = target_score
                        where user_id = cast(:user_id as uuid)
                        """
                    ),
                    {"user_id": user_id},
                )

    onboarding_after = client.get("/api/v1/onboarding/profile", headers=headers)
    assert onboarding_after.status_code == 200
    assert onboarding_after.json()["targetPositionId"] is None
    assert onboarding_after.json()["targetScore"] is None
    memory_after = client.get("/api/v1/learner/memory", headers=headers)
    assert memory_after.status_code == 200
    assert memory_after.json()["profile"]["targetPositionId"] is None
    assert memory_after.json()["profile"]["targetScore"] is None
    taskstream_after = client.get("/api/v1/taskstream/today", headers=headers)
    assert taskstream_after.status_code == 200
    assert taskstream_after.json()["target"]["scoreGapAvailable"] is False
    feed_after = client.get("/api/v1/feed/today", headers=headers)
    assert feed_after.status_code == 200
    assert _goal_gap_target(feed_after.json()) is None

    with db_session_factory() as session:
        assert (
            CompanionRepository(session)
            .get_context(
                user_id=user_id,
                node_id=None,
            )
            .target_score
            is None
        )
        assert (
            ReportingRepository().current_goal(
                session,
                user_id=user_id,
            )
            is None
        )
        assert (
            PlanningRepository()
            .goal_and_ability(
                session,
                user_id=user_id,
            )
            .goal_version_id
            is None
        )
        canonical = session.execute(
            text(
                """
                select status
                from learner.position_goal_versions
                where user_id = cast(:user_id as uuid)
                  and role = 'primary'
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        assert canonical == "confirmed"
        stored_profile = (
            session.execute(
                text(
                    """
                select target_position_id, target_score,
                       target_position_data_version
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert str(stored_profile["target_position_id"]) == position_id
        assert stored_profile["target_score"] is not None
        assert stored_profile["target_position_data_version"] == "projection-data-v1"


def _client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    return TestClient(app)


def _seed_user(factory: sessionmaker[Session], *, token: str) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (cast(:user_id as uuid), '岗位投影测试用户')
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  gen_random_uuid(), cast(:user_id as uuid), :token_hash,
                  'position-authority-test', :expires_at
                )
                """
            ),
            {
                "user_id": user_id,
                "token_hash": token_hash_for_tests(token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
    return user_id


def _complete_onboarding_projection(
    session: Session,
    *,
    user_id: str,
    profile_version: int,
) -> None:
    session.execute(
        text(
            """
            insert into learning.weekly_study_preference_versions (
              preference_version_id, command_id, user_id, version_no,
              timezone, weekday_minutes, weekend_minutes, long_block_minutes,
              fragment_minutes, regular_rest_weekday, regular_mock_weekday,
              effective_from, source_type, confirmed, input_fingerprint,
              supersedes_preference_version_id
            ) values (
              gen_random_uuid(), :command_id, cast(:user_id as uuid), 1,
              'Asia/Shanghai', 120, 120, 90, 25, 7, 6,
              current_date, 'user_confirmed', true, :fingerprint, null
            )
            """
        ),
        {
            "command_id": f"position-authority-preference:{user_id}",
            "user_id": user_id,
            "fingerprint": "5" * 64,
        },
    )
    session.execute(
        text(
            """
            insert into learner.preparation_experience_versions (
              experience_version_id, command_id, user_id, version_no,
              declaration_status, current_status, first_systematic,
              external_learning, prior_guokao_exam, retake, prior_exam_years,
              source_type, input_fingerprint,
              supersedes_experience_version_id
            ) values (
              gen_random_uuid(), :command_id, cast(:user_id as uuid), 1,
              'declared', 'parttime', true, false, false, false, '[]'::jsonb,
              'user_confirmed', :fingerprint, null
            )
            """
        ),
        {
            "command_id": f"position-authority-experience:{user_id}",
            "user_id": user_id,
            "fingerprint": "6" * 64,
        },
    )
    session.execute(
        text(
            """
            insert into learner.onboarding_progress (
              user_id, progress_version,
              experience_ready, experience_profile_version,
              qualification_ready, qualification_profile_version,
              study_preference_ready, study_preference_version,
              result_ready, result_acknowledged, completed, completed_at
            ) values (
              cast(:user_id as uuid), 1,
              true, 1, true, :profile_version, true, 1,
              true, true, true, statement_timestamp()
            )
            """
        ),
        {"user_id": user_id, "profile_version": profile_version},
    )
    session.execute(
        text(
            """
            update learner.profiles
            set onboarding_complete = true
            where user_id = cast(:user_id as uuid)
            """
        ),
        {"user_id": user_id},
    )


def _insert_position(
    session: Session,
    *,
    source_id: str,
    year: int,
    position_code: str,
    region: str = "北京",
    city: str = "北京市",
) -> str:
    return str(
        session.execute(
            text(
                """
                insert into product.positions (
                  source_id, year, position_code, name, department_full,
                  system_code, region, city, paper_type, headcount,
                  education_min, major_required, political_req,
                  service_min_yrs
                ) values (
                  :source_id, :year, :position_code, '一级行政执法员',
                  :department_full, 'tax', :region, :city,
                  'administrativeLaw', 2, '本科', '法学', '不限', 0
                )
                returning position_id
                """
            ),
            {
                "source_id": source_id,
                "year": year,
                "position_code": position_code,
                "department_full": f"国家税务总局测试局-{source_id}",
                "region": region,
                "city": city,
            },
        ).scalar_one()
    )


def _insert_snapshot(
    session: Session,
    *,
    position_id: str,
    source_id: str,
    year: int,
    position_code: str,
    position_data_version: str,
) -> None:
    session.execute(
        text(
            """
            insert into product.position_snapshots (
              position_id, position_data_version, source_id, year,
              position_code, name, department_full, system_code,
              region, city, paper_type, headcount, education_min,
              major_required, political_req, service_min_yrs,
              has_extra_exam, source_fingerprint
            ) values (
              cast(:position_id as uuid), :position_data_version,
              :source_id, :year, :position_code, '一级行政执法员',
              :department_full, 'tax', '北京', '北京市',
              'administrativeLaw', 2, '本科', '法学', '不限', 0,
              false, :source_fingerprint
            )
            """
        ),
        {
            "position_id": position_id,
            "position_data_version": position_data_version,
            "source_id": source_id,
            "year": year,
            "position_code": position_code,
            "department_full": f"国家税务总局测试局-{source_id}",
            "source_fingerprint": "3" * 64,
        },
    )


def _insert_score_history(
    session: Session,
    *,
    position_id: str,
    source_id: str,
    position_code: str,
    score_year: int,
    score: float,
) -> None:
    history_id = session.execute(
        text(
            """
            insert into product.position_score_histories (
              source_id, position_code, year, entry_score_min,
              entry_score_max, data_source
            ) values (
              :source_id, :position_code, :score_year, :score, :score,
              'position-authority-fixture'
            )
            returning score_history_id
            """
        ),
        {
            "source_id": source_id,
            "position_code": position_code,
            "score_year": score_year,
            "score": score,
        },
    ).scalar_one()
    session.execute(
        text(
            """
            insert into product.position_score_links (
              position_id, score_history_id, match_method, confidence
            ) values (
              cast(:position_id as uuid), cast(:history_id as uuid),
              'fixture', 'direct'
            )
            """
        ),
        {"position_id": position_id, "history_id": str(history_id)},
    )


def _insert_release(
    session: Session,
    *,
    exam_year: int,
    rule_set_version: str,
    position_data_version: str,
) -> tuple[str, datetime]:
    release_id = str(uuid4())
    now = datetime.now(UTC)
    closes_at = now + timedelta(days=30)
    session.execute(
        text(
            """
            insert into product.position_rule_releases (
              release_id, exam_code, exam_year, rule_set_version,
              position_data_version, status
            ) values (
              cast(:release_id as uuid), 'guokao', :exam_year,
              :rule_set_version, :position_data_version, 'draft'
            )
            """
        ),
        {
            "release_id": release_id,
            "exam_year": exam_year,
            "rule_set_version": rule_set_version,
            "position_data_version": position_data_version,
        },
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
              'https://example.gov.cn/position-authority',
              '岗位权威强化测试公告', :published_at, :received_at,
              :source_sha256, 'position-authority-fixture'
            )
            """
        ),
        {
            "release_id": release_id,
            "opens_at": now - timedelta(days=1),
            "closes_at": closes_at,
            "published_at": now - timedelta(days=3),
            "received_at": now - timedelta(days=2),
            "source_sha256": "4" * 64,
        },
    )
    return release_id, closes_at


def _activate_release(session: Session, *, release_id: str) -> None:
    session.execute(
        text(
            """
            update product.position_rule_releases
            set status = 'active', activated_at = statement_timestamp()
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": release_id},
    )


def _goal_gap_target(payload: dict[str, object]) -> object:
    cards = payload["cards"]
    assert isinstance(cards, list)
    goal_gap = next(
        card for card in cards if isinstance(card, dict) and card.get("type") == "goal_gap"
    )
    goal_payload = goal_gap["payload"]
    assert isinstance(goal_payload, dict)
    return goal_payload.get("targetScore")
