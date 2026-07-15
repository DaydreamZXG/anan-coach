from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.main import create_app


TOKEN = "qualification-v2-token"


def test_v2_profile_eligibility_and_primary_goal_contract(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    position_id = _seed_position_release_requirement_and_scores(db_session_factory)
    client = _client(db_session_factory)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Idempotency-Key": "profile-onboarding-a02",
        "X-Request-ID": "qualification-v2-1",
    }

    profile = client.put(
        "/api/v2/qualification/profile",
        headers=headers,
        json={
            "facts": [
                {"key": "education", "value": "本科"},
                {"key": "major", "value": "法学"},
            ]
        },
    )
    assert profile.status_code == 200
    assert profile.headers["X-Request-ID"] == "qualification-v2-1"
    assert profile.json()["data"]["profileVersion"] == 2
    assert {fact["key"] for fact in profile.json()["data"]["facts"]} == {
        "education",
        "major",
    }

    repeated = client.put(
        "/api/v2/qualification/profile",
        headers=headers,
        json={
            "facts": [
                {"key": "education", "value": "本科"},
                {"key": "major", "value": "法学"},
            ]
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["profileVersion"] == 2

    discovery = client.get(
        "/api/v2/positions",
        headers={"Authorization": f"Bearer {TOKEN}"},
        params={"examYear": 2026, "region": "江西", "limit": 20},
    )
    assert discovery.status_code == 200
    discovery_body = discovery.json()
    assert discovery_body["requestId"]
    assert discovery_body["data"]["learnerProfileVersion"] == 2
    assert discovery_body["data"]["items"][0]["positionId"] == position_id
    assert discovery_body["data"]["items"][0]["eligibilityStatus"] == "eligible"
    list_target_score = discovery_body["data"]["items"][0]["targetScore"]
    assert set(list_target_score) == {
        "lowerBound",
        "recommended",
        "upperBound",
        "sampleCount",
        "basisLevel",
        "evidenceLevel",
        "generatedAt",
        "validUntil",
    }
    assert list_target_score["basisLevel"] == "direct"
    assert list_target_score["sampleCount"] == 2
    assert list_target_score["validUntil"] > list_target_score["generatedAt"]

    detail = client.get(
        f"/api/v2/positions/{position_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert detail.status_code == 200
    detail_body = detail.json()["data"]
    assert detail_body["positionId"] == position_id
    assert detail_body["positionDataVersion"] == "guokao-2026-fixture"
    assert detail_body["targetScore"] == list_target_score

    target_score_response = client.get(
        f"/api/v2/positions/{position_id}/target-score",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert target_score_response.status_code == 200
    target_score_body = target_score_response.json()["data"]
    assert target_score_body["positionDataVersion"] == "guokao-2026-fixture"
    assert target_score_body["targetScore"] == list_target_score

    eligibility = client.post(
        f"/api/v2/positions/{position_id}/eligibility",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert eligibility.status_code == 200
    snapshot = eligibility.json()["data"]
    assert snapshot["evaluation"]["status"] == "eligible"
    assert snapshot["evaluation"]["ruleSetVersion"] == "guokao-2026-v1"
    assert snapshot["evaluation"]["positionDataVersion"] == "guokao-2026-fixture"
    assert snapshot["evaluation"]["checks"][0]["evidenceIds"]

    preview = client.post(
        "/api/v2/position-goals/primary/preview",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "preview-primary-position-1",
        },
        json={
            "positionId": position_id,
            "eligibilitySnapshotId": snapshot["snapshotId"],
        },
    )
    assert preview.status_code == 200
    preview_body = preview.json()["data"]
    assert preview_body["positionId"] == position_id
    assert preview_body["currentGoalVersionId"] is None
    assert preview_body["targetScore"]["recommended"] == 122.77
    assert preview_body["targetScore"] == list_target_score
    assert preview_body["planImpact"]["changeType"] == "goal_selected"
    assert preview_body["planImpact"]["requiresPlanRebuild"] is True

    goal = client.put(
        "/api/v2/position-goals/primary",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "confirm-primary-position-1",
        },
        json={
            "previewId": preview_body["previewId"],
            "changeReason": "确认该岗位为主报目标",
        },
    )
    assert goal.status_code == 200
    goal_body = goal.json()["data"]
    assert goal_body["goal"]["positionId"] == position_id
    assert goal_body["goal"]["targetScore"]["recommended"] == 122.77
    assert goal_body["goal"]["targetScore"]["evidenceLevel"] == "direct"
    assert goal_body["planImpact"]["requiresPlanRebuild"] is True
    assert goal_body["planImpact"]["invalidates"] == [
        "daily_plan",
        "weekly_plan",
        "score_gap",
    ]
    replayed_goal = client.put(
        "/api/v2/position-goals/primary",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "confirm-primary-position-1",
        },
        json={
            "previewId": preview_body["previewId"],
            "changeReason": "确认该岗位为主报目标",
        },
    )
    assert replayed_goal.status_code == 200
    assert (
        replayed_goal.json()["data"]["goal"]["goalVersionId"]
        == goal_body["goal"]["goalVersionId"]
    )
    assert replayed_goal.json()["data"]["planImpact"] == goal_body["planImpact"]

    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from learner.position_goal_versions where user_id = :user_id"
            ),
            {"user_id": user_id},
        ).scalar_one() == 1
        assert session.execute(
            text(
                "select count(*) from platform.outbox_events where event_type = 'position_goal.changed'"
            )
        ).scalar_one() == 1


def test_v2_missing_requirement_or_fact_never_returns_eligible(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory)
    position_id = _seed_position_release_requirement_and_scores(
        db_session_factory,
        include_requirements=False,
    )
    client = _client(db_session_factory)
    client.put(
        "/api/v2/qualification/profile",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "profile-no-rules-1",
        },
        json={"facts": [{"key": "education", "value": "本科"}]},
    )

    response = client.post(
        f"/api/v2/positions/{position_id}/eligibility",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["evaluation"]["status"] == "needs_confirmation"
    assert (
        response.json()["data"]["evaluation"]["checks"][0]["reasonCode"]
        == "requirements_missing"
    )


def test_v2_authoritative_no_condition_position_can_be_eligible(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory)
    position_id = _seed_position_release_requirement_and_scores(
        db_session_factory,
        include_requirements=False,
        include_compilation=True,
    )
    client = _client(db_session_factory)
    response = client.post(
        f"/api/v2/positions/{position_id}/eligibility",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["evaluation"]["status"] == "eligible"
    assert response.json()["data"]["evaluation"]["checks"] == []


def test_v2_rejects_client_identity_and_returns_stable_public_errors(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory)
    client = _client(db_session_factory)
    response = client.put(
        "/api/v2/qualification/profile",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "reject-forged-learner-id",
        },
        json={
            "learnerId": str(uuid4()),
            "facts": [{"key": "education", "value": "本科"}],
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_request"
    assert body["requestId"]


def test_v2_position_list_reports_no_active_release_with_stable_code(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory)

    response = _client(db_session_factory).get(
        "/api/v2/positions",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "position_release_unavailable"


def test_v2_target_score_rejects_expired_release_with_stable_code(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory)
    position_id = _seed_position_release_requirement_and_scores(db_session_factory)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update product.position_rule_releases
                set activated_at = :expired_at
                where status = 'active'
                """
            ),
            {"expired_at": datetime.now(UTC) - timedelta(days=366)},
        )

    response = _client(db_session_factory).get(
        f"/api/v2/positions/{position_id}/target-score",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "target_score_basis_expired"


def test_v2_primary_preview_rejects_snapshot_from_superseded_position_version(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    position_id = _seed_position_release_requirement_and_scores(db_session_factory)
    snapshot_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learner.eligibility_snapshots (
                  snapshot_id, user_id, position_id, learner_profile_version,
                  rule_set_version, position_data_version, status, checks,
                  missing_fact_keys, evaluated_at
                ) values (
                  :snapshot_id, :user_id, :position_id, 1,
                  'guokao-2026-v0', 'guokao-2026-superseded', 'eligible',
                  '[]'::jsonb, '[]'::jsonb, :evaluated_at
                )
                """
            ),
            {
                "snapshot_id": snapshot_id,
                "user_id": user_id,
                "position_id": position_id,
                "evaluated_at": datetime.now(UTC),
            },
        )

    response = _client(db_session_factory).post(
        "/api/v2/position-goals/primary/preview",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "preview-stale-position-release",
        },
        json={"positionId": position_id, "eligibilitySnapshotId": snapshot_id},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "position_data_version_conflict"


def test_v2_primary_confirm_rejects_release_change_after_preview(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    position_id = _seed_position_release_requirement_and_scores(db_session_factory)
    snapshot_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learner.eligibility_snapshots (
                  snapshot_id, user_id, position_id, learner_profile_version,
                  rule_set_version, position_data_version, status, checks,
                  missing_fact_keys, evaluated_at
                ) values (
                  :snapshot_id, :user_id, :position_id, 1,
                  'guokao-2026-v1', 'guokao-2026-fixture', 'eligible',
                  '[]'::jsonb, '[]'::jsonb, :evaluated_at
                )
                """
            ),
            {
                "snapshot_id": snapshot_id,
                "user_id": user_id,
                "position_id": position_id,
                "evaluated_at": datetime.now(UTC),
            },
        )
    client = _client(db_session_factory)
    preview = client.post(
        "/api/v2/position-goals/primary/preview",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "preview-before-release-change",
        },
        json={"positionId": position_id, "eligibilitySnapshotId": snapshot_id},
    )
    assert preview.status_code == 200

    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'retired'
                where status = 'active'
                """
            )
        )
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  :release_id, 'guokao', 2026, 'guokao-2026-v2',
                  'guokao-2026-fixture-v2', 'active', :activated_at
                )
                """
            ),
            {"release_id": str(uuid4()), "activated_at": datetime.now(UTC)},
        )

    response = client.put(
        "/api/v2/position-goals/primary",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Idempotency-Key": "confirm-after-release-change",
        },
        json={
            "previewId": preview.json()["data"]["previewId"],
            "changeReason": "确认该岗位为主报目标",
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "position_data_version_conflict"


def _client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    return TestClient(app)


def _seed_user(factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
            {"id": user_id},
        )
        session.execute(
            text("insert into learner.profiles (user_id, profile_version) values (:id, 1)"),
            {"id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  :session_id, :user_id, :token_hash, 'wechat-test', :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "token_hash": token_hash_for_tests(TOKEN),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
    return user_id


def _seed_position_release_requirement_and_scores(
    factory: sessionmaker[Session],
    *,
    include_requirements: bool = True,
    include_compilation: bool | None = None,
) -> str:
    if include_compilation is None:
        include_compilation = include_requirements
    position_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.positions (
                  position_id, source_id, year, position_code, name, department_full,
                  system_code, region, city, paper_type, headcount, education_min
                ) values (
                  :position_id, :source_id, 2026, '300110001001', '一级行政执法员',
                  '国家税务总局测试局', 'tax', '江西', '赣州市',
                  'administrativeLaw', 2, '本科'
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
                  position_data_version, status, activated_at
                ) values (
                  :release_id, 'guokao', 2026, 'guokao-2026-v1',
                  'guokao-2026-fixture', 'active', :activated_at
                )
                """
            ),
            {"release_id": str(uuid4()), "activated_at": datetime.now(UTC)},
        )
        if include_requirements:
            session.execute(
                text(
                    """
                    insert into product.position_requirements (
                      requirement_id, position_id, rule_set_version,
                      position_data_version, fact_key, operator, expected,
                      source_text, source_reference
                    ) values (
                      :requirement_id, :position_id, 'guokao-2026-v1',
                      'guokao-2026-fixture', 'education', 'one_of',
                      '["本科", "硕士", "博士"]'::jsonb,
                      '本科及以上', 'fixture:300110001001'
                    )
                    """
                ),
                {"requirement_id": str(uuid4()), "position_id": position_id},
            )
        if include_compilation:
            session.execute(
                text(
                    """
                    insert into product.position_rule_compilations (
                      compilation_id, position_id, rule_set_version,
                      position_data_version, compiler_version, source_fingerprint,
                      status, requirements_count, manual_review_count, warnings,
                      compiled_at
                    ) values (
                      :compilation_id, :position_id, 'guokao-2026-v1',
                      'guokao-2026-fixture', 'fixture-v1', :fingerprint,
                      :status, :requirements_count, 0, '[]'::jsonb, :compiled_at
                    )
                    """
                ),
                {
                    "compilation_id": str(uuid4()),
                    "position_id": position_id,
                    "fingerprint": "a" * 64,
                    "status": "deterministic" if include_requirements else "no_conditions",
                    "requirements_count": 1 if include_requirements else 0,
                    "compiled_at": datetime.now(UTC),
                },
            )
        for year, score in ((2023, 119.5), (2024, 121.5)):
            evidence_id = session.execute(
                text(
                    """
                    insert into product.position_entry_score_evidence (
                      year, department_code, position_code, agency, bureau,
                      position_title, entry_score_min, source_name,
                      source_file_sha256, candidate_count
                    ) values (
                      :year, '360000', '300110001001', '国家税务总局测试局',
                      '机关处室', '一级行政执法员', :score, 'fixture',
                      :sha, 5
                    ) returning evidence_id
                    """
                ),
                {"year": year, "score": score, "sha": f"{year:064d}"[-64:]},
            ).scalar_one()
            session.execute(
                text(
                    """
                    insert into product.position_entry_score_links (
                      position_id, evidence_id, match_method, confidence
                    ) values (:position_id, :evidence_id, 'fixture', 'high')
                    """
                ),
                {"position_id": position_id, "evidence_id": evidence_id},
            )
    return position_id
