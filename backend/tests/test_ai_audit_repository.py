from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.ai.audit import AIAuditRepository, AIQuotaExceededError


def test_reserve_quota_rejects_zero_limit(db_session: Session) -> None:
    repository = AIAuditRepository(db_session)

    with pytest.raises(AIQuotaExceededError):
        repository.reserve_quota(
            learner_key="learner-zero",
            feature="essay_review",
            limit=0,
        )

    count = db_session.execute(text("select count(*) from ai_audit.usage_daily")).scalar_one()
    assert count == 0


def test_reserve_quota_is_atomic_under_concurrency(
    db_session_factory: sessionmaker[Session],
) -> None:
    learner_key = f"learner-{uuid4()}"

    def attempt_reservation() -> str:
        with db_session_factory() as session:
            try:
                AIAuditRepository(session).reserve_quota(
                    learner_key=learner_key,
                    feature="essay_review",
                    limit=3,
                )
                return "reserved"
            except AIQuotaExceededError:
                return "exceeded"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: attempt_reservation(), range(10)))

    assert results.count("reserved") == 3
    assert results.count("exceeded") == 7

    with db_session_factory() as session:
        row = session.execute(
            text(
                """
                select used_count, limit_count
                from ai_audit.usage_daily
                where learner_key = :learner_key
                  and feature = 'essay_review'
                """
            ),
            {"learner_key": learner_key},
        ).mappings().one()

    assert row["used_count"] == 3
    assert row["limit_count"] == 3


def test_quota_resets_across_usage_dates(db_session: Session) -> None:
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    db_session.execute(
        text(
            """
            insert into ai_audit.usage_daily (
              learner_key,
              feature,
              usage_date,
              used_count,
              limit_count
            ) values (
              'learner-cross-day',
              'essay_review',
              :yesterday,
              1,
              1
            )
            """
        ),
        {"yesterday": yesterday},
    )
    db_session.commit()

    reservation = AIAuditRepository(db_session).reserve_quota(
        learner_key="learner-cross-day",
        feature="essay_review",
        limit=1,
    )

    assert reservation.used == 1
    assert reservation.usage_date == datetime.now(UTC).date()
    count = db_session.execute(
        text(
            """
            select count(*)
            from ai_audit.usage_daily
            where learner_key = 'learner-cross-day'
            """
        )
    ).scalar_one()
    assert count == 2
