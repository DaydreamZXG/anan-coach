from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.onboarding.service import PLACEMENT_PLAN, PLACEMENT_TASK_ID
from app.domains.qualification.rule_releases import (
    PositionReleaseWindowInput,
    PositionRuleReleaseService,
)
from app.main import create_app


def test_onboarding_profile_derives_target_score_from_real_position_score(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="onboard-token")
    position_id = _seed_position_with_entry_score(db_session_factory)
    _seed_open_position_release(db_session_factory)
    client = _make_db_client(db_session_factory)

    response = client.put(
        "/api/v1/onboarding/profile",
        headers={"Authorization": "Bearer onboard-token"},
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
            "baselineWeakModules": ["资料分析"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dailyMinutes"] == 120
    assert body["targetPositionId"] == position_id
    assert body["targetScore"] == 122.77
    assert body["baselineSource"] == "self_report"
    assert body["baselineWeakModules"] == ["资料分析"]

    complete = client.post(
        "/api/v1/onboarding/complete",
        headers={"Authorization": "Bearer onboard-token"},
    )
    assert complete.status_code == 410
    assert complete.json()["code"] == "onboarding_v1_retired"

    with db_session_factory() as session:
        row = (
            session.execute(
                text(
                    """
                select education, major, baseline_source, onboarding_complete
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
    assert row["education"] == "本科"
    assert row["major"] == "法学"
    assert row["baseline_source"] == "self_report"
    assert row["onboarding_complete"] is False


def test_release_unbound_position_search_is_retired(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="position-token")
    _seed_position_with_entry_score(db_session_factory)
    _seed_unmatched_position(db_session_factory)
    client = _make_db_client(db_session_factory)

    response = client.get(
        "/api/v1/positions/search",
        headers={"Authorization": "Bearer position-token"},
        params={
            "education": "本科",
            "major": "法学",
            "politicalStatus": "群众",
            "grassrootsYears": 0,
            "freshGraduate": "true",
            "year": 2026,
        },
    )

    assert response.status_code == 410
    assert "release-unbound" in response.json()["detail"].lower()


def test_release_unbound_position_target_score_is_retired(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="cohort-token")
    position_id = _seed_position_with_l2_cohort_scores(db_session_factory)
    client = _make_db_client(db_session_factory)

    response = client.get(
        f"/api/v1/positions/{position_id}/target-score",
        headers={"Authorization": "Bearer cohort-token"},
    )

    assert response.status_code == 410
    detail = client.get(
        f"/api/v1/positions/{position_id}",
        headers={"Authorization": "Bearer cohort-token"},
    )
    assert detail.status_code == 410


def test_onboarding_keeps_user_target_score_when_position_cohort_is_insufficient(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="manual-target-token")
    position_id = _seed_position_without_scores(db_session_factory)
    _seed_open_position_release(db_session_factory)
    client = _make_db_client(db_session_factory)

    response = client.put(
        "/api/v1/onboarding/profile",
        headers={"Authorization": "Bearer manual-target-token"},
        json={
            "education": "本科",
            "major": "法学",
            "freshGraduate": True,
            "politicalStatus": "群众",
            "grassrootsYears": 0,
            "targetPositionId": position_id,
            "targetScore": 130,
            "baselineSource": "none",
        },
    )

    assert response.status_code == 200
    assert response.json()["targetScore"] == 130


def test_onboarding_rejects_release_unbound_target_position_write(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="unbound-target-token")
    position_id = _seed_position_with_entry_score(db_session_factory)

    response = _make_db_client(db_session_factory).put(
        "/api/v1/onboarding/profile",
        headers={"Authorization": "Bearer unbound-target-token"},
        json={
            "education": "本科",
            "major": "法学",
            "freshGraduate": True,
            "politicalStatus": "群众",
            "grassrootsYears": 0,
            "targetPositionId": position_id,
            "baselineSource": "none",
        },
    )

    assert response.status_code == 409
    assert "active" in response.json()["detail"].lower()


def test_placement_test_uses_real_questions_and_persists_baseline(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="placement-token")
    _seed_placement_questions(db_session_factory)
    client = _make_db_client(db_session_factory)

    task = client.get(
        "/api/v1/onboarding/placement-test",
        headers={"Authorization": "Bearer placement-token"},
    )
    assert task.status_code == 200
    task_body = task.json()
    assert task_body["questionCount"] == 30
    assert task_body["durationMinutes"] == 35

    answers = [
        {
            "questionId": question["questionId"],
            "selectedAnswer": "A",
        }
        for question in task_body["questions"]
    ]
    submit = client.post(
        "/api/v1/onboarding/placement-test/answers",
        headers={"Authorization": "Bearer placement-token"},
        json={"answers": answers},
    )

    assert submit.status_code == 200
    body = submit.json()
    assert body["taskId"] == PLACEMENT_TASK_ID
    assert body["baselineSource"] == "placement_test"
    assert body["estimatedXingceScore"] == 100.0
    assert len(body["moduleResults"]) == 6

    with db_session_factory() as session:
        profile = (
            session.execute(
                text(
                    """
                select baseline_source, baseline_xingce_score
                from learner.profiles
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        attempts = (
            session.execute(
                text(
                    """
                select count(*) as count
                from learner.placement_attempts
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
    assert profile["baseline_source"] == "placement_test"
    assert float(profile["baseline_xingce_score"]) == 100.0
    assert int(attempts["count"]) == 1


def _make_db_client(db_session_factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def _seed_open_position_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with db_session_factory.begin() as session:
        service = PositionRuleReleaseService()
        release_id = service.ensure_draft(
            session,
            exam_code="guokao",
            exam_year=2026,
            rule_set_version="onboarding-position-rules-v1",
            position_data_version="onboarding-position-data-v1",
        )
        service.compile_draft(session, release_id=release_id, compiled_at=now)
        service.configure_windows(
            session,
            release_id=release_id,
            windows=[
                PositionReleaseWindowInput(
                    kind="primary",
                    sequence_no=1,
                    opens_at=now - timedelta(days=1),
                    closes_at=now + timedelta(days=1),
                    official_source_url=("https://example.gov.cn/onboarding-position-release"),
                    official_source_title="建档岗位测试公告",
                    official_source_published_at=now - timedelta(days=3),
                    official_source_received_at=now - timedelta(days=2),
                    official_source_sha256="b" * 64,
                    official_source_receipt="onboarding-position-release-fixture",
                )
            ],
        )
        service.activate(session, release_id=release_id, activated_at=now)


def _seed_user(db_session_factory: sessionmaker[Session], *, token: str) -> str:
    user_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (cast(:user_id as uuid), '学习者')
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id,
                  user_id,
                  session_token_hash,
                  device_id,
                  expires_at
                ) values (
                  cast(:session_id as uuid),
                  cast(:user_id as uuid),
                  :session_token_hash,
                  'ios-test',
                  :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "session_token_hash": token_hash_for_tests(token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        session.commit()
    return user_id


def _seed_position_with_entry_score(db_session_factory: sessionmaker[Session]) -> str:
    with db_session_factory() as session:
        position_id = session.execute(
            text(
                """
                insert into product.positions (
                  source_id,
                  year,
                  position_code,
                  name,
                  department_full,
                  system_code,
                  region,
                  city,
                  paper_type,
                  headcount,
                  education_min,
                  major_required,
                  political_req,
                  service_min_yrs
                ) values (
                  'pos-target',
                  2026,
                  '300110001001',
                  '一级行政执法员',
                  '国家税务总局测试局',
                  'tax',
                  '北京',
                  '北京市',
                  'administrativeLaw',
                  2,
                  '本科',
                  '法学',
                  '不限',
                  0
                )
                returning position_id
                """
            )
        ).scalar_one()
        _insert_entry_score_link(
            session,
            position_id=str(position_id),
            year=2023,
            department_code="130000",
            position_code="300110001001",
            score=119.5,
        )
        _insert_entry_score_link(
            session,
            position_id=str(position_id),
            year=2024,
            department_code="130000",
            position_code="300110001001",
            score=121.5,
        )
        session.commit()
        return str(position_id)


def _seed_position_with_l2_cohort_scores(
    db_session_factory: sessionmaker[Session],
) -> str:
    with db_session_factory() as session:
        target_id = _insert_position(
            session,
            source_id="pos-l2-target",
            year=2026,
            position_code="300110360001",
            department_full="国家税务总局兴国县税务局",
            region="江西",
            city="赣州市",
            major_required="工商管理",
        )
        cohort = [
            ("pos-l2-yudu", "300110360101", "国家税务总局于都县税务局", 2023, 126.0),
            ("pos-l2-ningdu", "300110360102", "国家税务总局宁都县税务局", 2024, 128.0),
            ("pos-l2-ruijin", "300110360103", "国家税务总局瑞金市税务局", 2025, 131.0),
        ]
        for source_id, position_code, department_full, score_year, score in cohort:
            position_id = _insert_position(
                session,
                source_id=source_id,
                year=2026,
                position_code=position_code,
                department_full=department_full,
                region="江西",
                city="赣州市",
                major_required="工商管理",
            )
            _insert_entry_score_link(
                session,
                position_id=position_id,
                year=score_year,
                department_code="360700",
                position_code=position_code,
                score=score,
            )
        other_major_id = _insert_position(
            session,
            source_id="pos-l2-other-major-score",
            year=2026,
            position_code="300110360199",
            department_full="国家税务总局赣州市税务局",
            region="江西",
            city="赣州市",
            major_required="计算机类",
        )
        _insert_entry_score_link(
            session,
            position_id=other_major_id,
            year=2025,
            department_code="360700",
            position_code="300110360199",
            score=180.0,
        )
        session.commit()
        return target_id


def _seed_position_without_scores(db_session_factory: sessionmaker[Session]) -> str:
    with db_session_factory() as session:
        position_id = _insert_position(
            session,
            source_id="pos-no-score",
            year=2026,
            position_code="300110009999",
            department_full="国家税务总局无分数局",
            region="北京",
            city="北京市",
            major_required="法学",
        )
        session.commit()
        return position_id


def _insert_position(
    session: Session,
    *,
    source_id: str,
    year: int,
    position_code: str,
    department_full: str,
    region: str,
    city: str,
    major_required: str,
) -> str:
    position_id = session.execute(
        text(
            """
            insert into product.positions (
              source_id,
              year,
              position_code,
              name,
              department_full,
              system_code,
              region,
              city,
              paper_type,
              headcount,
              education_min,
              major_required,
              political_req,
              service_min_yrs
            ) values (
              :source_id,
              :year,
              :position_code,
              '一级行政执法员',
              :department_full,
              'tax',
              :region,
              :city,
              'administrativeLaw',
              2,
              '本科',
              :major_required,
              '不限',
              0
            )
            returning position_id
            """
        ),
        {
            "source_id": source_id,
            "year": year,
            "position_code": position_code,
            "department_full": department_full,
            "region": region,
            "city": city,
            "major_required": major_required,
        },
    ).scalar_one()
    return str(position_id)


def _insert_entry_score_link(
    session: Session,
    *,
    position_id: str,
    year: int,
    department_code: str,
    position_code: str,
    score: float,
) -> None:
    evidence_id = session.execute(
        text(
            """
            insert into product.position_entry_score_evidence (
              year,
              department_code,
              position_code,
              agency,
              bureau,
              position_title,
              entry_score_min,
              source_name,
              source_file_sha256,
              candidate_count
            ) values (
              :year,
              :department_code,
              :position_code,
              '国家税务总局测试局',
              '机关处室',
              '一级行政执法员',
              :score,
              'fixture',
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
              5
            )
            returning evidence_id
            """
        ),
        {
            "year": year,
            "department_code": department_code,
            "position_code": position_code,
            "score": score,
        },
    ).scalar_one()
    session.execute(
        text(
            """
            insert into product.position_entry_score_links (
              position_id,
              evidence_id,
              match_method,
              confidence
            ) values (
              cast(:position_id as uuid),
              cast(:evidence_id as uuid),
              'fixture',
              'high'
            )
            """
        ),
        {"position_id": position_id, "evidence_id": str(evidence_id)},
    )


def _seed_unmatched_position(db_session_factory: sessionmaker[Session]) -> None:
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into product.positions (
                  source_id,
                  year,
                  position_code,
                  name,
                  department_full,
                  system_code,
                  region,
                  paper_type,
                  headcount,
                  education_min,
                  major_required,
                  political_req,
                  service_min_yrs
                ) values (
                  'pos-other-major',
                  2026,
                  '300110001002',
                  '一级行政执法员',
                  '国家税务总局测试局2',
                  'tax',
                  '北京',
                  'administrativeLaw',
                  1,
                  '本科',
                  '经济学',
                  '不限',
                  0
                )
                """
            )
        )
        session.commit()


def _seed_placement_questions(db_session_factory: sessionmaker[Session]) -> None:
    modules = [(module, count) for module, count, _weight in PLACEMENT_PLAN]
    with db_session_factory() as session:
        for module_name, count in modules:
            for index in range(count):
                question_id = f"placement-{module_name}-{index}"
                session.execute(
                    text(
                        """
                        insert into product.questions (
                          question_id,
                          domain,
                          status,
                          module_name,
                          question_type,
                          stem,
                          answer,
                          visual_required,
                          needs_multimodal,
                          shared_context_required,
                          latest_year,
                          occurrence_count
                        ) values (
                          :question_id,
                          'objective',
                          'active',
                          :module_name,
                          '单选题',
                          :stem,
                          'A',
                          false,
                          false,
                          false,
                          2026,
                          1
                        )
                        """
                    ),
                    {
                        "question_id": question_id,
                        "module_name": module_name,
                        "stem": f"{module_name} 第 {index + 1} 题",
                    },
                )
                session.execute(
                    text(
                        """
                        insert into product.question_options (
                          question_id,
                          option_index,
                          label,
                          option_text
                        ) values
                          (:question_id, 1, 'A', '正确项'),
                          (:question_id, 2, 'B', '干扰项')
                        """
                    ),
                    {"question_id": question_id},
                )
        session.commit()
