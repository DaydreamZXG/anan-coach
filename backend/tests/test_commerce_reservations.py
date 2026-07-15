from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.domains.commerce.repository import (
    CommerceQuotaExceededError,
    CommerceQuotaRefundError,
    CommerceRepository,
)
from app.domains.commerce.service import CommerceService


NOW = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)


def test_reservation_key_replay_only_consumes_quota_once(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    with db_session_factory() as session:
        service = _service(session)
        first = service.reserve_feature_usage(
            user_id=user_id,
            feature="essay_review",
            reservation_key=f"essay-review:{user_id}:execution-1",
            business_key="execution-1",
        )
        replay = service.reserve_feature_usage(
            user_id=user_id,
            feature="essay_review",
            reservation_key=f"essay-review:{user_id}:execution-1",
            business_key="execution-1",
        )

        assert replay.reservation_id == first.reservation_id
        assert replay.status == "reserved"
        assert replay.used == 1
        assert _usage(session, user_id) == 1
        assert session.execute(
            text(
                "select count(*) from commerce.feature_usage_reservations "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 1
        assert session.execute(
            text(
                "select count(*) from commerce.entitlement_audit_logs "
                "where user_id = cast(:user_id as uuid) "
                "and action = 'quota_reserved'"
            ),
            {"user_id": user_id},
        ).scalar_one() == 1


def test_concurrent_reservation_replay_only_increments_once(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    barrier = Barrier(2)

    def reserve() -> str:
        with db_session_factory() as session:
            barrier.wait()
            reservation = _service(session).reserve_feature_usage(
                user_id=user_id,
                feature="essay_review",
                reservation_key=f"essay-review:{user_id}:concurrent-execution",
                business_key="concurrent-execution",
            )
            return str(reservation.reservation_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        reservation_ids = list(executor.map(lambda _: reserve(), range(2)))

    assert len(set(reservation_ids)) == 1
    with db_session_factory() as session:
        assert _usage(session, user_id) == 1
        assert session.execute(
            text(
                "select count(*) from commerce.feature_usage_reservations "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 1


def test_quota_denial_audit_commits_without_partial_reservation(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    with db_session_factory() as session:
        service = _service(session)
        _reserve(service, user_id, "execution-allowed")

        with pytest.raises(CommerceQuotaExceededError):
            service.reserve_feature_usage(
                user_id=user_id,
                feature="essay_review",
                reservation_key=f"essay-review:{user_id}:execution-denied",
                business_key="execution-denied",
                commit=False,
            )
        # This is the route-level denial contract: commit this same transaction.
        # No reservation/job/outbox write exists when the quota gate returns no row.
        session.commit()

        assert _usage(session, user_id) == 1
        assert session.execute(
            text(
                "select count(*) from commerce.feature_usage_reservations "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 1
        assert session.execute(
            text(
                "select count(*) from commerce.entitlement_audit_logs "
                "where user_id = cast(:user_id as uuid) "
                "and action = 'quota_denied' and allowed = false"
            ),
            {"user_id": user_id},
        ).scalar_one() == 1


def test_refund_is_idempotent_and_only_releases_its_own_reservation(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    _grant_pro(db_session_factory, user_id)
    with db_session_factory() as session:
        service = _service(session)
        first = _reserve(service, user_id, "execution-1")
        second = _reserve(service, user_id, "execution-2")

        refunded = service.refund_feature_usage(reservation=first)
        replay = service.refund_feature_usage(reservation=first)

        assert refunded.reservation_id == replay.reservation_id
        assert replay.status == "refunded"
        assert replay.refunded_at is not None
        assert _usage(session, user_id) == 1
        rows = session.execute(
            text(
                "select reservation_key, status "
                "from commerce.feature_usage_reservations "
                "where user_id = cast(:user_id as uuid) "
                "order by reservation_key"
            ),
            {"user_id": user_id},
        ).mappings().all()
        assert [row["status"] for row in rows] == ["refunded", "reserved"]
        assert second.status == "reserved"
        assert session.execute(
            text(
                "select count(*) from commerce.entitlement_audit_logs "
                "where user_id = cast(:user_id as uuid) "
                "and action = 'quota_refunded'"
            ),
            {"user_id": user_id},
        ).scalar_one() == 1
        with pytest.raises(DBAPIError, match="terminal feature usage reservation"):
            session.execute(
                text(
                    "update commerce.feature_usage_reservations "
                    "set status = 'reserved', refunded_at = null "
                    "where reservation_id = cast(:reservation_id as uuid)"
                ),
                {"reservation_id": str(first.reservation_id)},
            )
        session.rollback()
        with pytest.raises(DBAPIError, match="terminal feature usage reservation"):
            session.execute(
                text(
                    "update commerce.feature_usage_reservations "
                    "set updated_at = now() "
                    "where reservation_id = cast(:reservation_id as uuid)"
                ),
                {"reservation_id": str(first.reservation_id)},
            )
        session.rollback()
        with pytest.raises(DBAPIError, match="reservation history cannot be deleted"):
            session.execute(
                text(
                    "delete from commerce.feature_usage_reservations "
                    "where reservation_id = cast(:reservation_id as uuid)"
                ),
                {"reservation_id": str(first.reservation_id)},
            )
        session.rollback()


def test_consumption_is_idempotent_and_cannot_be_refunded(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    with db_session_factory() as session:
        service = _service(session)
        reservation = _reserve(service, user_id, "execution-consumed")

        consumed = service.consume_feature_usage(reservation=reservation)
        replay = service.consume_feature_usage(reservation=reservation)

        assert consumed.reservation_id == replay.reservation_id
        assert replay.status == "consumed"
        assert replay.consumed_at is not None
        assert _usage(session, user_id) == 1
        with pytest.raises(CommerceQuotaRefundError, match="cannot be refunded"):
            service.refund_feature_usage(reservation=reservation)
        assert _usage(session, user_id) == 1


def _service(session: Session) -> CommerceService:
    return CommerceService(
        repository=CommerceRepository(session),
        app_store_verifier=cast(Any, object()),
        now=lambda: NOW,
    )


def _reserve(
    service: CommerceService,
    user_id: str,
    business_key: str,
):
    return service.reserve_feature_usage(
        user_id=user_id,
        feature="essay_review",
        reservation_key=f"essay-review:{user_id}:{business_key}",
        business_key=business_key,
    )


def _usage(session: Session, user_id: str) -> int:
    return int(
        session.execute(
            text(
                "select used_count from commerce.entitlement_usage_daily "
                "where user_id = cast(:user_id as uuid) "
                "and feature = 'essay_review'"
            ),
            {"user_id": user_id},
        ).scalar_one()
    )


def _seed_user(db_session_factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '申论学习者')"
            ),
            {"user_id": user_id},
        )
        session.commit()
    return user_id


def _grant_pro(
    db_session_factory: sessionmaker[Session],
    user_id: str,
) -> None:
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into commerce.user_entitlements (
                  entitlement_id, user_id, tier, source,
                  subscription_start, subscription_end, product_ids, features
                ) values (
                  cast(:entitlement_id as uuid), cast(:user_id as uuid),
                  'pro', 'test', now(), '2099-01-01T00:00:00Z', '[]', '{}'
                )
                """
            ),
            {"entitlement_id": str(uuid4()), "user_id": user_id},
        )
        session.commit()
