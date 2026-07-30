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


def test_position_decision_release_unbound_recommendations_are_retired(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="decision-token")
    _seed_position_with_score(
        db_session_factory,
        source_id="decision-safe",
        position_code="300110100001",
        department_full="国家税务总局稳妥县税务局",
        headcount=3,
        scores=[(2023, 108.0), (2024, 110.0), (2025, 111.0)],
    )
    _seed_position_with_score(
        db_session_factory,
        source_id="decision-balanced",
        position_code="300110100002",
        department_full="国家税务总局稳妥市税务局",
        headcount=2,
        scores=[(2023, 116.0), (2024, 118.0), (2025, 120.0)],
    )
    _seed_position_with_score(
        db_session_factory,
        source_id="decision-stretch",
        position_code="300110100003",
        department_full="国家税务总局热门市税务局",
        headcount=1,
        scores=[(2023, 126.0), (2024, 130.0), (2025, 133.0)],
        has_extra_exam=True,
    )
    _seed_unmatched_position(db_session_factory)
    client = _make_db_client(db_session_factory)

    response = client.get(
        "/api/v1/position-decisions/recommendations",
        headers={"Authorization": "Bearer decision-token"},
        params={
            "education": "本科",
            "major": "法学",
            "politicalStatus": "群众",
            "grassrootsYears": 0,
            "freshGraduate": "true",
            "year": 2026,
            "currentScore": 115,
            "riskPreference": "balanced",
        },
    )

    assert response.status_code == 410
    assert "release-unbound" in response.json()["detail"].lower()


def test_position_decision_prediction_cannot_bypass_release_lifecycle(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="prediction-token")
    _seed_position_with_score(
        db_session_factory,
        source_id="decision-predicted-target",
        position_code="300110200001",
        department_full="国家税务总局预测县税务局",
        headcount=2,
        scores=[(2025, 100.0)],
    )
    _seed_position_with_score(
        db_session_factory,
        source_id="decision-predicted-peer-a",
        position_code="300110200002",
        department_full="国家税务总局样本一县税务局",
        headcount=2,
        scores=[(2025, 119.0)],
    )
    _seed_position_with_score(
        db_session_factory,
        source_id="decision-predicted-peer-b",
        position_code="300110200003",
        department_full="国家税务总局样本二县税务局",
        headcount=2,
        scores=[(2025, 121.0)],
    )
    client = _make_db_client(db_session_factory)

    response = client.get(
        "/api/v1/position-decisions/recommendations",
        headers={"Authorization": "Bearer prediction-token"},
        params={
            "education": "本科",
            "major": "法学",
            "politicalStatus": "群众",
            "grassrootsYears": 0,
            "year": 2026,
            "currentScore": 115,
            "riskPreference": "balanced",
        },
    )

    assert response.status_code == 410


def test_position_decision_requires_authenticated_user(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_position_with_score(
        db_session_factory,
        source_id="decision-auth",
        position_code="300110100099",
        department_full="国家税务总局认证县税务局",
        headcount=1,
        scores=[(2025, 110.0)],
    )
    client = _make_db_client(db_session_factory)

    response = client.get(
        "/api/v1/position-decisions/recommendations",
        params={
            "education": "本科",
            "major": "法学",
            "politicalStatus": "群众",
            "grassrootsYears": 0,
            "currentScore": 115,
        },
    )

    assert response.status_code == 401


def _make_db_client(db_session_factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


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


def _seed_position_with_score(
    db_session_factory: sessionmaker[Session],
    *,
    source_id: str,
    position_code: str,
    department_full: str,
    headcount: int,
    scores: list[tuple[int, float]],
    has_extra_exam: bool = False,
) -> str:
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
                  service_min_yrs,
                  has_extra_exam
                ) values (
                  :source_id,
                  2026,
                  :position_code,
                  '一级行政执法员',
                  :department_full,
                  'tax',
                  '江西',
                  '赣州市',
                  'administrativeLaw',
                  :headcount,
                  '本科',
                  '法学',
                  '不限',
                  0,
                  :has_extra_exam
                )
                returning position_id
                """
            ),
            {
                "source_id": source_id,
                "position_code": position_code,
                "department_full": department_full,
                "headcount": headcount,
                "has_extra_exam": has_extra_exam,
            },
        ).scalar_one()
        for year, score in scores:
            _insert_entry_score_link(
                session,
                position_id=str(position_id),
                year=year,
                department_code="360700",
                position_code=position_code,
                score=score,
            )
        session.commit()
        return str(position_id)


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
                  city,
                  paper_type,
                  headcount,
                  education_min,
                  major_required,
                  political_req,
                  service_min_yrs
                ) values (
                  'decision-other-major',
                  2026,
                  '300110199999',
                  '一级行政执法员',
                  '国家税务总局其他市税务局',
                  'tax',
                  '江西',
                  '赣州市',
                  'administrativeLaw',
                  5,
                  '本科',
                  '经济学',
                  '不限',
                  0
                )
                """
            )
        )
        session.commit()


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
              'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
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
