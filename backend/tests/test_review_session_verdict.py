from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.progression.service import ProgressionService
from app.domains.rank_system.repository import RankRepository
from app.domains.rank_system.service import RankService
from app.domains.review_session.repository import ReviewSessionRepository
from app.domains.review_session.schemas import ReviewVerdictRequest
from app.domains.review_session.service import ReviewSessionService
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
        verdict = (
            session.execute(
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
            )
            .mappings()
            .one()
        )
        rank = (
            session.execute(
                text(
                    """
                select xp, fame
                from learner.official_ranks
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
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


def test_explicit_review_verdict_command_replays_once_and_rejects_drift(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="explicit-command-token")
    docket_id, card_id, _ = _seed_docket_card(
        db_session_factory,
        user_id=user_id,
    )
    client = _make_client(db_session_factory)
    url = f"/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict"
    headers = {
        "Authorization": "Bearer explicit-command-token",
        "Idempotency-Key": "review-verdict-command-001",
    }
    body = {"selectedAnswer": "B", "elapsedSeconds": 42}

    first = client.post(url, headers=headers, json=body)
    replay = client.post(url, headers=headers, json=body)
    elapsed_drift = client.post(
        url,
        headers=headers,
        json={"selectedAnswer": "B", "elapsedSeconds": 43},
    )
    terminal_retry = client.post(
        url,
        headers={
            **headers,
            "Idempotency-Key": "review-verdict-command-002",
        },
        json=body,
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert elapsed_drift.status_code == 409
    assert elapsed_drift.json()["detail"] == (
        "Idempotency key was already used with a different request"
    )
    assert terminal_retry.status_code == 409
    assert terminal_retry.json()["detail"] == ("Review card verdict is already completed")

    state = _review_command_state(db_session_factory, user_id=user_id)
    assert state == {
        "command_count": 1,
        "command_ids": ["review-verdict:explicit:review-verdict-command-001"],
        "verdict_count": 1,
        "xp_event_count": 1,
        "xp": 10,
        "fame": 51,
        "consecutive_days": 1,
        "last_active_date": date.today(),
    }


def test_legacy_review_verdict_replay_ignores_elapsed_and_new_card_is_new_terminal(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="legacy-command-token")
    docket_id, card_id, _ = _seed_docket_card(
        db_session_factory,
        user_id=user_id,
    )
    second_card_id = _seed_additional_card(
        db_session_factory,
        docket_id=docket_id,
    )
    client = _make_client(db_session_factory)
    headers = {"Authorization": "Bearer legacy-command-token"}
    url = f"/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict"

    first = client.post(
        url,
        headers=headers,
        json={"selectedAnswer": "B", "elapsedSeconds": 10},
    )
    replay = client.post(
        url,
        headers=headers,
        json={"selectedAnswer": "B", "elapsedSeconds": 99},
    )
    next_card = client.post(
        f"/api/v1/review-sessions/{docket_id}/cards/{second_card_id}/verdict",
        headers=headers,
        json={"selectedAnswer": "B", "elapsedSeconds": 12},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert next_card.status_code == 200
    assert next_card.json()["verdictId"] != first.json()["verdictId"]
    state = _review_command_state(db_session_factory, user_id=user_id)
    assert state == {
        "command_count": 2,
        "command_ids": sorted(
            [
                f"review-verdict:legacy:{card_id}",
                f"review-verdict:legacy:{second_card_id}",
            ]
        ),
        "verdict_count": 2,
        "xp_event_count": 2,
        "xp": 20,
        "fame": 52,
        "consecutive_days": 1,
        "last_active_date": date.today(),
    }


def test_promoting_review_verdict_replay_emits_one_promotion(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="promotion-command-token")
    docket_id, card_id, _ = _seed_docket_card(
        db_session_factory,
        user_id=user_id,
    )
    _seed_rank(db_session_factory, user_id=user_id, xp=495)
    client = _make_client(db_session_factory)
    url = f"/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict"
    headers = {
        "Authorization": "Bearer promotion-command-token",
        "Idempotency-Key": "review-verdict-promotion-001",
    }
    body = {"selectedAnswer": "B", "elapsedSeconds": 42}

    first = client.post(url, headers=headers, json=body)
    replay = client.post(url, headers=headers, json=body)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["progression"]["rankChanged"] is True
    with db_session_factory() as session:
        rank = (
            session.execute(
                text(
                    """
                    select xp, rank_code, rank_level
                    from learner.official_ranks
                    where user_id = cast(:user_id as uuid)
                    """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        event_counts = dict(
            session.execute(
                text(
                    """
                    select event_type, count(*)::integer as event_count
                    from learner.rank_events
                    where user_id = cast(:user_id as uuid)
                    group by event_type
                    """
                ),
                {"user_id": user_id},
            ).all()
        )
    state = _review_command_state(db_session_factory, user_id=user_id)
    assert dict(rank) == {
        "xp": 505,
        "rank_code": "eight_pin_xiancheng",
        "rank_level": 8,
    }
    assert event_counts == {"promotion": 1, "xp_gain": 1}
    assert state["command_count"] == 1
    assert state["verdict_count"] == 1


def test_concurrent_identical_review_verdict_command_converges_once(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="concurrent-command-token")
    docket_id, card_id, _ = _seed_docket_card(
        db_session_factory,
        user_id=user_id,
    )
    url = f"/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict"
    headers = {
        "Authorization": "Bearer concurrent-command-token",
        "Idempotency-Key": "review-verdict-concurrent-001",
    }

    def submit() -> tuple[int, dict[str, object]]:
        response = _make_client(db_session_factory).post(
            url,
            headers=headers,
            json={"selectedAnswer": "B", "elapsedSeconds": 42},
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: submit(), range(8)))

    assert {status_code for status_code, _ in results} == {200}
    assert len({body["verdictId"] for _, body in results}) == 1
    state = _review_command_state(db_session_factory, user_id=user_id)
    assert state["command_count"] == 1
    assert state["verdict_count"] == 1
    assert state["xp_event_count"] == 1
    assert state["xp"] == 10


def test_completed_review_verdict_without_receipt_is_not_replayed_or_rewarded(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="historical-verdict-token")
    docket_id, card_id, question_id = _seed_docket_card(
        db_session_factory,
        user_id=user_id,
    )
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.verdicts (
                  verdict_id, user_id, docket_id, card_id, question_id,
                  selected_answer, correct, elapsed_seconds, created_at
                ) values (
                  cast(:verdict_id as uuid), cast(:user_id as uuid),
                  cast(:docket_id as uuid), cast(:card_id as uuid),
                  :question_id, 'B', true, 20, now()
                )
                """
            ),
            {
                "verdict_id": str(uuid4()),
                "user_id": user_id,
                "docket_id": docket_id,
                "card_id": card_id,
                "question_id": question_id,
            },
        )
        session.execute(
            text(
                """
                update learner.docket_cards
                set status = 'completed', updated_at = now()
                where card_id = cast(:card_id as uuid)
                """
            ),
            {"card_id": card_id},
        )
        session.commit()

    response = _make_client(db_session_factory).post(
        f"/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict",
        headers={
            "Authorization": "Bearer historical-verdict-token",
            "Idempotency-Key": "historical-review-verdict-001",
        },
        json={"selectedAnswer": "B", "elapsedSeconds": 21},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Review card verdict is already completed"
    state = _review_command_state(db_session_factory, user_id=user_id)
    assert state["command_count"] == 0
    assert state["verdict_count"] == 1
    assert state["xp_event_count"] == 0
    assert state["xp"] is None


@pytest.mark.parametrize(
    ("failure_point", "initial_xp"),
    (
        ("after_verdict", 0),
        ("after_xp", 0),
        ("after_active_date", 0),
        ("before_promotion", 495),
        ("before_receipt", 495),
    ),
)
def test_review_verdict_durable_step_failure_rolls_back_every_fact(
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
    initial_xp: int,
) -> None:
    user_id = _seed_user(db_session_factory, token=f"failure-{failure_point}")
    docket_id, card_id, _ = _seed_docket_card(
        db_session_factory,
        user_id=user_id,
    )
    _seed_rank(db_session_factory, user_id=user_id, xp=initial_xp)

    with db_session_factory() as session:
        review_repository = ReviewSessionRepository(session)
        rank_repository = RankRepository(session, auto_commit=False)
        progression_service = ProgressionService(
            rank_service=RankService(repository=rank_repository)
        )
        service = ReviewSessionService(
            repository=review_repository,
            progression_service=progression_service,
        )

        def fail(**_: object) -> None:
            raise RuntimeError(f"injected failure {failure_point}")

        if failure_point == "after_verdict":
            monkeypatch.setattr(progression_service, "record_verdict", fail)
        elif failure_point == "after_xp":
            monkeypatch.setattr(rank_repository, "touch_active_date", fail)
        elif failure_point == "after_active_date":
            monkeypatch.setattr(rank_repository, "log_event", fail)
        elif failure_point == "before_promotion":
            monkeypatch.setattr(rank_repository, "update_rank", fail)
        else:
            monkeypatch.setattr(review_repository, "record_command", fail)

        with pytest.raises(
            RuntimeError,
            match=f"injected failure {failure_point}",
        ):
            service.submit_verdict(
                user_id=user_id,
                docket_id=docket_id,
                card_id=card_id,
                request=ReviewVerdictRequest(
                    selectedAnswer="B",
                    elapsedSeconds=42,
                ),
                idempotency_key=f"failure-{failure_point}-001",
            )

    with db_session_factory() as session:
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
        rank = (
            session.execute(
                text(
                    """
                select
                  xp, fame, rank_code, rank_level,
                  consecutive_days, last_active_date
                from learner.official_ranks
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
    state = _review_command_state(db_session_factory, user_id=user_id)
    assert card_status == "pending"
    assert state["command_count"] == 0
    assert state["verdict_count"] == 0
    assert state["xp_event_count"] == 0
    assert dict(rank) == {
        "xp": initial_xp,
        "fame": 50,
        "rank_code": "nine_pin_xianling",
        "rank_level": 9,
        "consecutive_days": 0,
        "last_active_date": None,
    }


def test_response_loss_after_commit_replays_original_review_verdict(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="response-loss-token")
    docket_id, card_id, _ = _seed_docket_card(
        db_session_factory,
        user_id=user_id,
    )
    client = _make_client(db_session_factory)
    url = f"/api/v1/review-sessions/{docket_id}/cards/{card_id}/verdict"
    headers = {
        "Authorization": "Bearer response-loss-token",
        "Idempotency-Key": "review-response-lost-001",
    }
    request = {"selectedAnswer": "B", "elapsedSeconds": 18}

    committed_but_unobserved = client.post(url, headers=headers, json=request)
    assert committed_but_unobserved.status_code == 200
    recovered = client.post(url, headers=headers, json=request)

    assert recovered.status_code == 200
    assert recovered.json() == committed_but_unobserved.json()
    state = _review_command_state(db_session_factory, user_id=user_id)
    assert state["command_count"] == 1
    assert state["verdict_count"] == 1
    assert state["xp_event_count"] == 1
    assert state["xp"] == 10


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


def _seed_additional_card(
    db_session_factory: sessionmaker[Session],
    *,
    docket_id: str,
) -> str:
    card_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.docket_cards (
                  card_id, docket_id, question_id, type, track, module_name,
                  world_label, title, narrative, interaction, source_grounding,
                  status, sort_order, payload
                ) values (
                  cast(:card_id as uuid), cast(:docket_id as uuid),
                  'verdict_q_01', 'daily_affair', 'xingce', '资料分析',
                  '复核全县钱粮', '复核钱粮簿册', '另起一封新卷。',
                  'swipe_verdict', 'product.questions', 'pending', 2, '{}'::jsonb
                )
                """
            ),
            {"card_id": card_id, "docket_id": docket_id},
        )
        session.commit()
    return card_id


def _seed_rank(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
    xp: int,
) -> None:
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.official_ranks (user_id, xp)
                values (cast(:user_id as uuid), :xp)
                """
            ),
            {"user_id": user_id, "xp": xp},
        )
        session.commit()


def _review_command_state(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
) -> dict[str, object]:
    with db_session_factory() as session:
        command_ids = list(
            session.execute(
                text(
                    """
                    select command_id
                    from learning.xingce_commands
                    where user_id = cast(:user_id as uuid)
                      and command_type = 'review_verdict'
                    order by command_id
                    """
                ),
                {"user_id": user_id},
            ).scalars()
        )
        verdict_count = session.execute(
            text(
                """
                select count(*)
                from learner.verdicts
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        xp_event_count = session.execute(
            text(
                """
                select count(*)
                from learner.rank_events
                where user_id = cast(:user_id as uuid)
                  and event_type = 'xp_gain'
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
        rank = (
            session.execute(
                text(
                    """
                select xp, fame, consecutive_days, last_active_date
                from learner.official_ranks
                where user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .first()
        )
    return {
        "command_count": len(command_ids),
        "command_ids": command_ids,
        "verdict_count": verdict_count,
        "xp_event_count": xp_event_count,
        "xp": rank["xp"] if rank is not None else None,
        "fame": rank["fame"] if rank is not None else None,
        "consecutive_days": rank["consecutive_days"] if rank is not None else None,
        "last_active_date": rank["last_active_date"] if rank is not None else None,
    }
