from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.external_ingestion.upload.reservations import (
    PostgresUploadReservationAdapter,
    UploadQuotaExceeded,
    UploadQuotaPolicy,
    UploadReservationConflict,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def adapter(db_session: Session) -> PostgresUploadReservationAdapter:
    return PostgresUploadReservationAdapter(
        session=db_session,
        policy=UploadQuotaPolicy(
            policy_version="upload-quota-policy@v1",
            maximum_pending_bytes_per_user=1_500,
            maximum_stored_bytes_per_user=2_000,
        ),
        clock=lambda: NOW,
    )


def test_reservation_replay_release_reactivation_commit_and_receipts(
    db_session: Session,
) -> None:
    owner = uuid4()
    db_session.execute(
        text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
        {"id": str(owner)},
    )
    reservations = adapter(db_session)
    key = "a" * 64

    first = reservations.reserve(owner_user_id=owner, reservation_key=key, byte_size=1_000)
    replay = reservations.reserve(owner_user_id=owner, reservation_key=key, byte_size=1_000)
    assert replay == first
    assert db_session.execute(
        text("select count(*) from learning.upload_reservation_events")
    ).scalar_one() == 1

    with pytest.raises(UploadQuotaExceeded):
        reservations.reserve(
            owner_user_id=owner, reservation_key="b" * 64, byte_size=600
        )

    reservations.release(reservation_id=first.reservation_id, reason="expired")
    reservations.release(reservation_id=first.reservation_id, reason="expired")
    with pytest.raises(UploadReservationConflict):
        reservations.release(reservation_id=first.reservation_id, reason="malware")

    reactivated = reservations.reserve(
        owner_user_id=owner, reservation_key=key, byte_size=1_000
    )
    artifact_version_id = uuid4()
    reservations.commit(
        reservation_id=reactivated.reservation_id,
        artifact_version_id=artifact_version_id,
    )
    reservations.commit(
        reservation_id=reactivated.reservation_id,
        artifact_version_id=artifact_version_id,
    )

    row = db_session.execute(
        text(
            """
            select state, attempt_no, artifact_version_id
            from learning.upload_reservations
            where reservation_id = :id
            """
        ),
        {"id": first.reservation_id},
    ).mappings().one()
    assert row["state"] == "committed"
    assert row["attempt_no"] == 2
    assert row["artifact_version_id"] == artifact_version_id
    assert db_session.execute(
        text("select count(*) from learning.upload_reservation_events")
    ).scalar_one() == 4

    with pytest.raises(UploadReservationConflict):
        reservations.release(
            reservation_id=first.reservation_id, reason="cannot-release-committed"
        )
    with pytest.raises(UploadReservationConflict):
        reservations.commit(
            reservation_id=first.reservation_id,
            artifact_version_id=uuid4(),
        )
    with pytest.raises(UploadQuotaExceeded):
        reservations.reserve(
            owner_user_id=owner, reservation_key="c" * 64, byte_size=1_100
        )


def test_reservation_key_cannot_cross_owner_or_change_bytes(
    db_session: Session,
) -> None:
    first_owner = uuid4()
    second_owner = uuid4()
    db_session.execute(
        text(
            """
            insert into learner.users (user_id, display_name) values
              (:first, '甲'), (:second, '乙')
            """
        ),
        {"first": str(first_owner), "second": str(second_owner)},
    )
    reservations = adapter(db_session)
    key = "d" * 64
    reservations.reserve(
        owner_user_id=first_owner, reservation_key=key, byte_size=500
    )

    with pytest.raises(UploadReservationConflict):
        reservations.reserve(
            owner_user_id=second_owner, reservation_key=key, byte_size=500
        )
    with pytest.raises(UploadReservationConflict):
        reservations.reserve(
            owner_user_id=first_owner, reservation_key=key, byte_size=501
        )
