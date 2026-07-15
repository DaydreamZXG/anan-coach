from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.main import create_app


def test_review_verdict_records_answer_and_stamps_card(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="verdict-token")
    docket_id, card_id, question_id = _seed_docket_card(
        db_session_factory,
        user_id=user_id,
    )
    client = _make_client(db_session_factory)

    response = client.post(
        f"/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict",
        headers={"Authorization": "Bearer verdict-token"},
        json={"selectedAnswer": "B", "elapsedSeconds": 42},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["docketId"] == docket_id
    assert body["cardId"] == card_id
    assert body["questionId"] == question_id
    assert body["selectedAnswer"] == "B"
    assert body["correct"] is True
    assert body["stampText"] == "准"
    assert body["sourceGrounding"] == "learner.verdicts + product.questions"
    assert "大人" in body["companionLine"]
    assert body["progression"] == {
        "meritDelta": 10,
        "fameDelta": 1,
        "courtRiskDelta": -1,
        "rankChanged": False,
        "oldRankName": None,
        "newRankName": None,
        "lockedPrivileges": [],
        "narrative": "资料分析准字落印，政绩 +10，民心 +1。",
    }
    assert "xpGained" not in body

    with db_session_factory() as session:
        verdict = session.execute(
            text(
                """
                select selected_answer, correct, elapsed_seconds
                from learner.verdicts
                where user_id = cast(:user_id as uuid)
                  and docket_id = cast(:docket_id as uuid)
                  and card_id = cast(:card_id as uuid)
                """
            ),
            {"user_id": user_id, "docket_id": docket_id, "card_id": card_id},
        ).mappings().one()
        rank = session.execute(
            text(
                """
                select xp, fame
                from learner.official_ranks
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
        card_status = session.execute(
            text(
                """
                select status
                from learner.docket_cards
                where card_id = cast(:card_id as uuid)
                """
            ),
            {"card_id": card_id},
        ).scalar_one()
    assert verdict["selected_answer"] == "B"
    assert verdict["correct"] is True
    assert verdict["elapsed_seconds"] == 42
    assert rank["xp"] == 10
    assert rank["fame"] == 51
    assert card_status == "completed"


def test_review_verdict_rejects_card_from_other_user(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="intruder-token")
    owner_id = _seed_user(db_session_factory, token="owner-token")
    docket_id, card_id, _ = _seed_docket_card(db_session_factory, user_id=owner_id)
    client = _make_client(db_session_factory)

    response = client.post(
        f"/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict",
        headers={"Authorization": "Bearer intruder-token"},
        json={"selectedAnswer": "B"},
    )

    assert response.status_code == 404


def _make_client(db_session_factory: sessionmaker[Session]) -> TestClient:
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


def _seed_docket_card(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
) -> tuple[str, str, str]:
    docket_id = str(uuid4())
    card_id = str(uuid4())
    question_id = "verdict_q_01"
    with db_session_factory() as session:
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
                  visual_required
                ) values (
                  :question_id,
                  'objective',
                  'active',
                  '资料分析',
                  '资料分析',
                  '某县粮仓收入同比增长。',
                  'B',
                  false
                )
                on conflict (question_id) do nothing
                """
            ),
            {"question_id": question_id},
        )
        session.execute(
            text(
                """
                insert into learner.dockets (
                  docket_id,
                  user_id,
                  docket_date,
                  status
                ) values (
                  cast(:docket_id as uuid),
                  cast(:user_id as uuid),
                  :docket_date,
                  'active'
                )
                """
            ),
            {
                "docket_id": docket_id,
                "user_id": user_id,
                "docket_date": date.today(),
            },
        )
        session.execute(
            text(
                """
                insert into learner.docket_cards (
                  card_id,
                  docket_id,
                  question_id,
                  type,
                  track,
                  module_name,
                  world_label,
                  title,
                  narrative,
                  interaction,
                  source_grounding,
                  status,
                  sort_order,
                  payload
                ) values (
                  cast(:card_id as uuid),
                  cast(:docket_id as uuid),
                  :question_id,
                  'daily_affair',
                  'xingce',
                  '资料分析',
                  '核算全县钱粮',
                  '州府钱粮急件',
                  '户房递来钱粮簿册，须速判增减盈亏。',
                  'swipe_verdict',
                  'product.questions',
                  'pending',
                  1,
                  '{}'::jsonb
                )
                """
            ),
            {"card_id": card_id, "docket_id": docket_id, "question_id": question_id},
        )
        session.commit()
    return docket_id, card_id, question_id
