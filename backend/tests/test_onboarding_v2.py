from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.deps import get_wechat_identity_exchange
from app.domains.auth.wechat import WechatIdentityClaims
from app.main import create_app


class _WechatExchange:
    def exchange(self, *, code: str) -> WechatIdentityClaims:
        return WechatIdentityClaims(
            app_id="wx-onboarding-contract",
            open_id="onboarding-openid",
            union_id="onboarding-unionid",
            session_key_hash=sha256(code.encode("utf-8")).hexdigest(),
        )


def test_v2_onboarding_progress_is_resumable_idempotent_and_auditable(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_active_release(db_session_factory)
    client = _client(db_session_factory)
    login = client.post(
        "/api/v2/auth/wechat",
        json={"code": "first-login", "deviceId": "wechat-onboarding-device-1"},
    )
    assert login.status_code == 200
    auth = login.json()["data"]
    token = auth["accessToken"]
    user_id = auth["user"]["userId"]
    assert auth["onboardingComplete"] is False
    assert auth["onboardingProgress"]["nextStep"] == "preparation_experience"
    headers = {"Authorization": f"Bearer {token}"}

    experience = _record_preparation_experience(client, headers=headers)
    assert experience["experience"]["recommendedEntry"] == "guided_first_start"
    assert experience["progress"]["progressVersion"] == 2
    assert experience["progress"]["nextStep"] == "qualification"

    facts = client.put(
        "/api/v2/qualification/profile",
        headers={**headers, "Idempotency-Key": "qualification-facts-a02"},
        json={
            "facts": [
                {"key": "education", "value": "本科"},
                {"key": "major", "value": "法学"},
                {"key": "graduation_year", "value": 2026},
                {"key": "fresh_graduate", "value": True},
                {"key": "political_status", "value": "群众"},
                {"key": "work_years", "value": 0},
                {"key": "grassroots_years", "value": 0},
                {"key": "service_project", "value": "无"},
            ]
        },
    )
    assert facts.status_code == 200
    assert facts.json()["data"]["profileVersion"] == 2
    after_fact_save = client.get("/api/v2/onboarding/progress", headers=headers)
    assert after_fact_save.json()["data"]["qualificationReady"] is False

    qualification = client.post(
        "/api/v2/onboarding/qualification/complete",
        headers={**headers, "Idempotency-Key": "qualification-ready-a02"},
        json={
            "expectedProgressVersion": 2,
            "qualificationProfileVersion": 2,
        },
    )
    assert qualification.status_code == 200
    assert qualification.json()["data"]["progressVersion"] == 3
    assert qualification.json()["data"]["nextStep"] == "study_preference"

    missing_position_preference = client.get(
        "/api/v2/onboarding/position-preference",
        headers=headers,
    )
    assert missing_position_preference.status_code == 404
    assert missing_position_preference.json()["code"] == "position_preference_missing"
    position_preference_command = {
        "expectedPreferenceVersion": 0,
        "preferredRegions": ["北京"],
        "acceptsRelocation": True,
        "acceptedPositionLevels": ["municipal", "county_or_below"],
    }
    position_preference = client.put(
        "/api/v2/onboarding/position-preference",
        headers={**headers, "Idempotency-Key": "position-preference-a04"},
        json=position_preference_command,
    )
    assert position_preference.status_code == 200
    assert position_preference.json()["data"]["preferenceVersion"] == 1
    assert position_preference.json()["data"]["preferredRegions"] == ["北京"]
    assert position_preference.json()["data"]["acceptsRelocation"] is True
    position_preference_replay = client.put(
        "/api/v2/onboarding/position-preference",
        headers={**headers, "Idempotency-Key": "position-preference-a04"},
        json=position_preference_command,
    )
    assert position_preference_replay.status_code == 200
    assert position_preference_replay.json()["data"] == position_preference.json()["data"]
    position_preference_changed_replay = client.put(
        "/api/v2/onboarding/position-preference",
        headers={**headers, "Idempotency-Key": "position-preference-a04"},
        json={**position_preference_command, "acceptsRelocation": False},
    )
    assert position_preference_changed_replay.status_code == 409
    assert position_preference_changed_replay.json()["code"] == "idempotency_key_reused"
    stale_position_preference = client.put(
        "/api/v2/onboarding/position-preference",
        headers={**headers, "Idempotency-Key": "position-preference-stale"},
        json={
            **position_preference_command,
            "preferredRegions": ["上海"],
        },
    )
    assert stale_position_preference.status_code == 409
    assert stale_position_preference.json()["code"] == "position_preference_version_conflict"

    resumed = client.post(
        "/api/v2/auth/wechat",
        json={"code": "second-login", "deviceId": "wechat-onboarding-device-2"},
    )
    assert resumed.status_code == 200
    resumed_data = resumed.json()["data"]
    assert resumed_data["user"]["userId"] == user_id
    assert resumed_data["onboardingComplete"] is False
    assert resumed_data["onboardingProgress"]["nextStep"] == "study_preference"
    assert resumed_data["onboardingProgress"]["qualificationProfileVersion"] == 2

    preference_command = {
        "expectedPreferenceVersion": 0,
        "expectedProgressVersion": 3,
        "timezone": "Asia/Shanghai",
        "effectiveFrom": "2026-07-14",
        "weekdayMinutes": 90,
        "weekendMinutes": 180,
        "longBlockMinutes": 120,
        "fragmentMinutes": 25,
        "fixedRestDay": "sunday",
        "mockExamDay": "saturday",
    }
    preference = client.put(
        "/api/v2/onboarding/study-preference",
        headers={**headers, "Idempotency-Key": "study-preference-a05"},
        json=preference_command,
    )
    assert preference.status_code == 200
    preference_data = preference.json()["data"]
    assert preference_data["preference"]["preferenceVersion"] == 1
    assert preference_data["preference"]["temporaryAdjustmentOwner"] == "daily_capacity"
    assert (
        preference_data["preference"]["temporaryAdjustmentEndpoint"]
        == "/api/v2/plans/today/capacity"
    )
    assert preference_data["progress"]["progressVersion"] == 4
    assert preference_data["progress"]["nextStep"] == "result"

    replay = client.put(
        "/api/v2/onboarding/study-preference",
        headers={**headers, "Idempotency-Key": "study-preference-a05"},
        json=preference_command,
    )
    assert replay.status_code == 200
    assert replay.json()["data"] == preference_data
    changed_replay = client.put(
        "/api/v2/onboarding/study-preference",
        headers={**headers, "Idempotency-Key": "study-preference-a05"},
        json={**preference_command, "weekendMinutes": 200},
    )
    assert changed_replay.status_code == 409
    assert changed_replay.json()["code"] == "idempotency_key_reused"

    baseline_missing = client.get(
        "/api/v2/onboarding/ability-baseline",
        headers=headers,
    )
    assert baseline_missing.status_code == 404
    assert baseline_missing.json()["code"] == "ability_baseline_missing"
    unknown_baseline = client.put(
        "/api/v2/onboarding/ability-baseline",
        headers={**headers, "Idempotency-Key": "ability-baseline-unknown-a06"},
        json={
            "expectedBaselineVersion": 0,
            "mode": "unknown",
            "xingceScore": None,
            "shenlunScore": None,
        },
    )
    assert unknown_baseline.status_code == 200
    assert unknown_baseline.json()["data"]["baselineVersion"] == 1
    assert unknown_baseline.json()["data"]["mode"] == "unknown"
    reported_baseline_command = {
        "expectedBaselineVersion": 1,
        "mode": "reported_score",
        "xingceScore": 65,
        "shenlunScore": 60,
    }
    reported_baseline = client.put(
        "/api/v2/onboarding/ability-baseline",
        headers={**headers, "Idempotency-Key": "ability-baseline-reported-a06"},
        json=reported_baseline_command,
    )
    assert reported_baseline.status_code == 200
    reported_baseline_data = reported_baseline.json()["data"]
    assert reported_baseline_data["baselineVersion"] == 2
    assert reported_baseline_data["mode"] == "reported_score"
    assert reported_baseline_data["xingceScore"] == 65
    assert reported_baseline_data["shenlunScore"] == 60
    assert reported_baseline_data["scoreEvidenceId"] is not None
    baseline_replay = client.put(
        "/api/v2/onboarding/ability-baseline",
        headers={**headers, "Idempotency-Key": "ability-baseline-reported-a06"},
        json=reported_baseline_command,
    )
    assert baseline_replay.status_code == 200
    assert baseline_replay.json()["data"] == reported_baseline_data
    changed_baseline_replay = client.put(
        "/api/v2/onboarding/ability-baseline",
        headers={**headers, "Idempotency-Key": "ability-baseline-reported-a06"},
        json={**reported_baseline_command, "xingceScore": 66},
    )
    assert changed_baseline_replay.status_code == 409
    assert changed_baseline_replay.json()["code"] == "idempotency_key_reused"
    current_baseline = client.get(
        "/api/v2/onboarding/ability-baseline",
        headers=headers,
    )
    assert current_baseline.status_code == 200
    assert current_baseline.json()["data"] == reported_baseline_data

    result = client.get("/api/v2/onboarding/result", headers=headers)
    assert result.status_code == 200
    result_data = result.json()["data"]
    assert result_data["status"] == "no_eligible_position"
    assert result_data["reasonCode"] == "no_current_eligible_position"
    assert result_data["scoreGap"] is None
    assert result_data["nextAction"] == "enter_workspace"
    assert result_data["validUntil"] is not None

    completed = client.post(
        "/api/v2/onboarding/complete",
        headers={**headers, "Idempotency-Key": "acknowledge-result-a06"},
        json={"expectedProgressVersion": 4, "acknowledgeResult": True},
    )
    assert completed.status_code == 200
    assert completed.json()["data"]["completed"] is True
    assert completed.json()["data"]["nextStep"] == "workspace"

    final_login = client.post(
        "/api/v2/auth/wechat",
        json={"code": "third-login", "deviceId": "wechat-onboarding-device-3"},
    )
    assert final_login.status_code == 200
    final_auth = final_login.json()["data"]
    assert final_auth["onboardingComplete"] is True
    assert final_auth["onboardingProgress"]["nextStep"] == "workspace"

    with db_session_factory() as session:
        projection = (
            session.execute(
                text(
                    """
                select profile.onboarding_complete, progress.completed,
                       progress.progress_version
                from learner.profiles profile
                join learner.onboarding_progress progress using (user_id)
                where profile.user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        assert projection["onboarding_complete"] is True
        assert projection["completed"] is True
        assert projection["progress_version"] == 5
        assert (
            session.execute(
                text(
                    "select count(*) from learner.onboarding_progress_events "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 4
        )
        assert (
            session.execute(
                text(
                    "select count(*) from learning.weekly_study_preference_versions "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select count(*) from learner.position_preference_versions "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select count(*) "
                    "from learner.ability_baseline_declaration_versions "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 2
        )
        assert (
            session.execute(
                text(
                    "select count(*) from learning.score_evidence "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text(
                    "select count(*) from platform.outbox_events "
                    "where aggregate_id = :user_id and event_type like 'onboarding.%'"
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 6
        )


def test_v2_onboarding_returns_stable_missing_and_version_errors(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _client(db_session_factory)
    login = client.post(
        "/api/v2/auth/wechat",
        json={"code": "login", "deviceId": "wechat-onboarding-errors"},
    ).json()["data"]
    headers = {"Authorization": f"Bearer {login['accessToken']}"}
    with db_session_factory() as session:
        try:
            session.execute(
                text(
                    "update learner.profiles set onboarding_complete = true "
                    "where user_id = cast(:user_id as uuid)"
                ),
                {"user_id": login["user"]["userId"]},
            )
            session.commit()
            raise AssertionError("legacy onboarding projection update unexpectedly succeeded")
        except DBAPIError:
            session.rollback()

    experience_required = client.post(
        "/api/v2/onboarding/qualification/complete",
        headers={**headers, "Idempotency-Key": "qualification-missing-a02"},
        json={
            "expectedProgressVersion": 1,
            "qualificationProfileVersion": 1,
        },
    )
    assert experience_required.status_code == 409
    assert experience_required.json()["code"] == "preparation_experience_required"

    _record_preparation_experience(client, headers=headers)
    missing = client.post(
        "/api/v2/onboarding/qualification/complete",
        headers={**headers, "Idempotency-Key": "qualification-incomplete-a03"},
        json={
            "expectedProgressVersion": 2,
            "qualificationProfileVersion": 1,
        },
    )
    assert missing.status_code == 409
    assert missing.json()["code"] == "onboarding_qualification_incomplete"
    assert "education" in missing.json()["details"]["missingFactKeys"]

    preference_missing = client.get(
        "/api/v2/onboarding/study-preference",
        headers=headers,
    )
    assert preference_missing.status_code == 404
    assert preference_missing.json()["code"] == "study_preference_missing"

    stale_progress = client.put(
        "/api/v2/onboarding/study-preference",
        headers={**headers, "Idempotency-Key": "preference-stale-progress"},
        json={
            "expectedPreferenceVersion": 0,
            "expectedProgressVersion": 8,
            "timezone": "Asia/Shanghai",
            "effectiveFrom": "2026-07-14",
            "weekdayMinutes": 90,
            "weekendMinutes": 180,
            "longBlockMinutes": 120,
            "fragmentMinutes": 25,
            "fixedRestDay": "sunday",
            "mockExamDay": "saturday",
        },
    )
    assert stale_progress.status_code == 409
    assert stale_progress.json()["code"] == "onboarding_progress_version_conflict"

    retired = client.post("/api/v1/onboarding/complete", headers=headers)
    assert retired.status_code == 410
    assert retired.json()["code"] == "onboarding_v1_retired"


def test_qualification_revision_invalidates_only_an_open_milestone(
    db_session_factory: sessionmaker[Session],
) -> None:
    client = _client(db_session_factory)
    login = client.post(
        "/api/v2/auth/wechat",
        json={"code": "login", "deviceId": "wechat-qualification-revision"},
    ).json()["data"]
    headers = {"Authorization": f"Bearer {login['accessToken']}"}
    _record_preparation_experience(client, headers=headers)
    original_facts = [
        {"key": "education", "value": "本科"},
        {"key": "major", "value": "法学"},
        {"key": "graduation_year", "value": 2026},
        {"key": "fresh_graduate", "value": True},
        {"key": "political_status", "value": "群众"},
        {"key": "work_years", "value": 0},
        {"key": "grassroots_years", "value": 0},
        {"key": "service_project", "value": "无"},
    ]
    assert (
        client.put(
            "/api/v2/qualification/profile",
            headers={**headers, "Idempotency-Key": "facts-before-confirmation"},
            json={"facts": original_facts},
        ).status_code
        == 200
    )
    confirmed = client.post(
        "/api/v2/onboarding/qualification/complete",
        headers={**headers, "Idempotency-Key": "qualification-confirmed"},
        json={
            "expectedProgressVersion": 2,
            "qualificationProfileVersion": 2,
        },
    )
    assert confirmed.json()["data"]["qualificationReady"] is True

    revised = client.put(
        "/api/v2/qualification/profile",
        headers={**headers, "Idempotency-Key": "facts-after-confirmation"},
        json={
            "facts": [
                {**fact, "value": "行政管理"} if fact["key"] == "major" else fact
                for fact in original_facts
            ]
        },
    )
    assert revised.status_code == 200
    assert revised.json()["data"]["profileVersion"] == 3
    progress = client.get("/api/v2/onboarding/progress", headers=headers).json()["data"]
    assert progress["progressVersion"] == 4
    assert progress["qualificationReady"] is False
    assert progress["qualificationProfileVersion"] is None
    assert progress["nextStep"] == "qualification"

    replay = client.put(
        "/api/v2/qualification/profile",
        headers={**headers, "Idempotency-Key": "facts-after-confirmation"},
        json={
            "facts": [
                {**fact, "value": "行政管理"} if fact["key"] == "major" else fact
                for fact in original_facts
            ]
        },
    )
    assert replay.status_code == 200
    assert client.get("/api/v2/onboarding/progress", headers=headers).json()["data"] == progress


def test_onboarding_result_requires_parallel_current_scores_before_gap(
    db_session_factory: sessionmaker[Session],
) -> None:
    release_id, release_valid_until = _seed_position_release(
        db_session_factory,
        activate=False,
    )
    client = _client(db_session_factory)
    login = client.post(
        "/api/v2/auth/wechat",
        json={"code": "login", "deviceId": "wechat-onboarding-result"},
    ).json()["data"]
    user_id = login["user"]["userId"]
    headers = {"Authorization": f"Bearer {login['accessToken']}"}
    experience = _record_preparation_experience(
        client,
        headers=headers,
        first_systematic=False,
        external_learning=True,
    )
    assert experience["experience"]["recommendedEntry"] == "import_external_evidence"
    facts = [
        {"key": "education", "value": "本科"},
        {"key": "major", "value": "法学"},
        {"key": "graduation_year", "value": 2026},
        {"key": "fresh_graduate", "value": True},
        {"key": "political_status", "value": "群众"},
        {"key": "work_years", "value": 0},
        {"key": "grassroots_years", "value": 0},
        {"key": "service_project", "value": "无"},
    ]
    client.put(
        "/api/v2/qualification/profile",
        headers={**headers, "Idempotency-Key": "result-facts"},
        json={"facts": facts},
    )
    client.post(
        "/api/v2/onboarding/qualification/complete",
        headers={**headers, "Idempotency-Key": "result-qualification"},
        json={"expectedProgressVersion": 2, "qualificationProfileVersion": 2},
    )
    client.put(
        "/api/v2/onboarding/study-preference",
        headers={**headers, "Idempotency-Key": "result-preference"},
        json={
            "expectedPreferenceVersion": 0,
            "expectedProgressVersion": 3,
            "timezone": "Asia/Shanghai",
            "effectiveFrom": "2026-07-14",
            "weekdayMinutes": 90,
            "weekendMinutes": 180,
            "longBlockMinutes": 120,
            "fragmentMinutes": 25,
            "fixedRestDay": "sunday",
            "mockExamDay": "saturday",
        },
    )
    position_id = _seed_current_goal_and_partial_estimate(
        db_session_factory,
        user_id=user_id,
        release_id=release_id,
        release_valid_until=release_valid_until,
    )

    partial = client.get("/api/v2/onboarding/result", headers=headers)
    assert partial.status_code == 200
    partial_data = partial.json()["data"]
    assert partial_data["status"] == "baseline_required"
    assert partial_data["reasonCode"] == "ability_partial"
    assert partial_data["primaryGoal"]["positionId"] == position_id
    assert partial_data["abilityEstimate"]["xingce"] is not None
    assert partial_data["abilityEstimate"]["shenlun"] is None
    assert partial_data["scoreGap"] is None
    assert partial_data["nextAction"] == "import_prior_evidence"

    _seed_complete_successor_estimate(db_session_factory, user_id=user_id)
    complete = client.get("/api/v2/onboarding/result", headers=headers)
    assert complete.status_code == 200
    complete_data = complete.json()["data"]
    assert complete_data["status"] == "evidence_sufficient"
    assert complete_data["abilityEstimate"]["status"] == "complete"
    assert complete_data["abilityEstimate"]["xingce"] is not None
    assert complete_data["abilityEstimate"]["shenlun"] is not None
    assert complete_data["scoreGap"]["unit"] == "points"
    assert complete_data["scoreGap"]["direction"] == "positive_means_target_above_current"
    assert complete_data["scoreGap"]["lower"] <= complete_data["scoreGap"]["upper"]
    assert complete_data["validUntil"] is not None


def _client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    app.dependency_overrides[get_wechat_identity_exchange] = lambda: _WechatExchange()
    return TestClient(app)


def _record_preparation_experience(
    client: TestClient,
    *,
    headers: dict[str, str],
    first_systematic: bool = True,
    external_learning: bool = False,
    prior_guokao_exam: bool = False,
    retake: bool = False,
) -> dict[str, object]:
    response = client.put(
        "/api/v2/onboarding/preparation-experience",
        headers={**headers, "Idempotency-Key": f"preparation-{uuid4()}"},
        json={
            "expectedExperienceVersion": 0,
            "expectedProgressVersion": 1,
            "firstSystematic": first_systematic,
            "externalLearning": external_learning,
            "priorGuokaoExam": prior_guokao_exam,
            "retake": retake,
            "priorExamYears": [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _seed_active_release(factory: sessionmaker[Session]) -> None:
    _seed_position_release(factory, activate=True)


def _seed_position_release(
    factory: sessionmaker[Session],
    *,
    activate: bool,
) -> tuple[str, datetime]:
    release_id = str(uuid4())
    activated_at = datetime.now(UTC)
    opens_at = activated_at - timedelta(days=1)
    closes_at = activated_at + timedelta(days=30)
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  cast(:release_id as uuid), 'guokao', 2026,
                  'guokao-2026-onboarding', 'guokao-2026-onboarding-data',
                  'draft', null
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
                  'https://example.gov.cn/guokao/onboarding-fixture',
                  '新手引导测试岗位发布窗口', :published_at, :received_at,
                  :source_sha256, 'onboarding-v2-fixture'
                )
                """
            ),
            {
                "release_id": release_id,
                "opens_at": opens_at,
                "closes_at": closes_at,
                "published_at": opens_at - timedelta(days=2),
                "received_at": opens_at - timedelta(days=1),
                "source_sha256": "d" * 64,
            },
        )
        if activate:
            _activate_release(
                session,
                release_id=release_id,
                activated_at=activated_at,
            )
    return release_id, closes_at


def _activate_release(
    session: Session,
    *,
    release_id: str,
    activated_at: datetime,
) -> None:
    session.execute(
        text(
            """
            update product.position_rule_releases
            set status = 'active', activated_at = :activated_at
            where release_id = cast(:release_id as uuid)
            """
        ),
        {"release_id": release_id, "activated_at": activated_at},
    )


def _seed_current_goal_and_partial_estimate(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    release_id: str,
    release_valid_until: datetime,
) -> str:
    position_id = str(uuid4())
    snapshot_id = str(uuid4())
    partial_id = str(uuid4())
    now = datetime.now(UTC)
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.positions (
                  position_id, source_id, year, position_code, name,
                  department_full, system_code, region, city, paper_type,
                  headcount, education_min
                ) values (
                  cast(:position_id as uuid), :source_id, 2026, '300110009999',
                  '一级主任科员以下', '国考测试部门', 'central', '北京', '北京',
                  'subProvincial', 2, '本科'
                )
                """
            ),
            {"position_id": position_id, "source_id": f"source-{position_id}"},
        )
        for year, score in ((2024, 126.0), (2025, 128.0)):
            evidence_id = session.execute(
                text(
                    """
                    insert into product.position_entry_score_evidence (
                      year, department_code, position_code, agency, bureau,
                      position_title, entry_score_min, source_name,
                      source_file_sha256, candidate_count
                    ) values (
                      :year, '000001', '300110009999', '国考测试部门',
                      '测试司局', '一级主任科员以下', :score,
                      'onboarding-result-fixture', :sha, 5
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
                    ) values (
                      cast(:position_id as uuid), :evidence_id, 'fixture', 'high'
                    )
                    """
                ),
                {"position_id": position_id, "evidence_id": evidence_id},
            )
        _activate_release(
            session,
            release_id=release_id,
            activated_at=now,
        )
        session.execute(
            text(
                """
                insert into learner.eligibility_snapshots (
                  snapshot_id, user_id, position_id, learner_profile_version,
                  rule_set_version, position_data_version, status, checks,
                  missing_fact_keys, evaluated_at
                ) values (
                  cast(:snapshot_id as uuid), cast(:user_id as uuid),
                  cast(:position_id as uuid), 2, 'guokao-2026-onboarding',
                  'guokao-2026-onboarding-data', 'eligible', '[]'::jsonb,
                  '[]'::jsonb, :now
                )
                """
            ),
            {
                "snapshot_id": snapshot_id,
                "user_id": user_id,
                "position_id": position_id,
                "now": now,
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
                  126, 128, 130, 'direct', 2, :valid_until, 2,
                  cast(:snapshot_id as uuid), 'guokao-2026-onboarding-data', :now
                )
                """
            ),
            {
                "user_id": user_id,
                "position_id": position_id,
                "snapshot_id": snapshot_id,
                "now": now,
                "valid_until": release_valid_until,
            },
        )
        partial_summary = {
            "evidenceCount": 1,
            "xingceEvidenceCount": 1,
            "shenlunEvidenceCount": 0,
            "sourceCounts": {"self_report": 1},
            "latestEvidenceAt": now.isoformat(),
            "missingComponents": ["shenlun"],
            "conflictComponents": [],
        }
        session.execute(
            text(
                """
                insert into learning.ability_estimate_versions (
                  estimate_version_id, user_id, exam_code, exam_year, status,
                  xingce_lower, xingce_recommended, xingce_upper,
                  confidence, source_evidence_ids, evidence_summary,
                  algorithm_version, input_fingerprint,
                  supersedes_estimate_version_id, generated_at, valid_until
                ) values (
                  cast(:estimate_id as uuid), cast(:user_id as uuid),
                  'guokao', 2026, 'partial', 60, 64, 68, 'low',
                  '[]'::jsonb, cast(:summary as jsonb), 'ability-estimate-v1',
                  :fingerprint, null, :now, :valid_until
                )
                """
            ),
            {
                "estimate_id": partial_id,
                "user_id": user_id,
                "summary": json.dumps(partial_summary, ensure_ascii=False),
                "fingerprint": "1" * 64,
                "now": now,
                "valid_until": now + timedelta(days=30),
            },
        )
    return position_id


def _seed_complete_successor_estimate(
    factory: sessionmaker[Session],
    *,
    user_id: str,
) -> None:
    now = datetime.now(UTC)
    with factory.begin() as session:
        previous_id = session.execute(
            text(
                """
                select estimate_version_id
                from learning.ability_estimate_versions
                where user_id = cast(:user_id as uuid)
                order by generated_at desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        summary = {
            "evidenceCount": 2,
            "xingceEvidenceCount": 1,
            "shenlunEvidenceCount": 1,
            "sourceCounts": {"self_report": 2},
            "latestEvidenceAt": now.isoformat(),
            "missingComponents": [],
            "conflictComponents": [],
        }
        session.execute(
            text(
                """
                insert into learning.ability_estimate_versions (
                  estimate_version_id, user_id, exam_code, exam_year, status,
                  xingce_lower, xingce_recommended, xingce_upper,
                  shenlun_lower, shenlun_recommended, shenlun_upper,
                  total_lower, total_recommended, total_upper,
                  confidence, source_evidence_ids, evidence_summary,
                  algorithm_version, input_fingerprint,
                  supersedes_estimate_version_id, generated_at, valid_until
                ) values (
                  gen_random_uuid(), cast(:user_id as uuid), 'guokao', 2026,
                  'complete', 60, 64, 68, 54, 58, 62, 114, 122, 130,
                  'medium', '[]'::jsonb, cast(:summary as jsonb),
                  'ability-estimate-v1', :fingerprint,
                  cast(:previous_id as uuid), :now, :valid_until
                )
                """
            ),
            {
                "user_id": user_id,
                "summary": json.dumps(summary, ensure_ascii=False),
                "fingerprint": "2" * 64,
                "previous_id": str(previous_id),
                "now": now,
                "valid_until": now + timedelta(days=30),
            },
        )
