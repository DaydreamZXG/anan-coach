from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ilink_channel.repository import ILinkConflict, ILinkRepository
from app.domains.ilink_channel.resolver import TosTransientImageStore
from app.workers.ilink_channel import ILinkWorker


NOW = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)


def _queue(session: Session, *, object_ref: str, state: str = "pending") -> None:
    session.execute(
        text(
            """
            insert into messaging.ilink_object_deletion_queue (
              object_ref, state, attempt_count, created_at, updated_at
            ) values (:object_ref, :state, 0, :now, :now)
            """
        ),
        {"object_ref": object_ref, "state": state, "now": NOW},
    )


def test_deletion_kill_restart_recovers_expired_lease_and_fences_old_token(
    db_session: Session,
) -> None:
    object_ref = "tos://fake-bucket/private/ilink/restart.png"
    _queue(db_session, object_ref=object_ref)
    repository = ILinkRepository(db_session)
    first = repository.claim_object_deletions(limit=1, now=NOW)
    assert len(first) == 1
    assert first[0].lease_expires_at == NOW + timedelta(minutes=5)
    db_session.commit()

    with pytest.raises(ILinkConflict, match="active deletion claim"):
        repository.complete_object_deletion(
            object_ref=object_ref,
            claim_token=first[0].claim_token,
            now=NOW + timedelta(minutes=6),
        )

    second = repository.claim_object_deletions(
        limit=1,
        now=NOW + timedelta(minutes=6),
    )
    assert len(second) == 1
    assert second[0].claim_token != first[0].claim_token

    with pytest.raises(ILinkConflict, match="active deletion claim"):
        repository.complete_object_deletion(
            object_ref=object_ref,
            claim_token=first[0].claim_token,
            now=NOW + timedelta(minutes=6, seconds=1),
        )

    repository.complete_object_deletion(
        object_ref=object_ref,
        claim_token=second[0].claim_token,
        now=NOW + timedelta(minutes=6, seconds=1),
    )
    assert db_session.execute(
        text(
            "select count(*) from messaging.ilink_object_deletion_queue "
            "where object_ref = :object_ref"
        ),
        {"object_ref": object_ref},
    ).scalar_one() == 0


def test_legacy_deleting_row_without_lease_is_reclaimed(
    db_session: Session,
) -> None:
    object_ref = "tos://fake-bucket/private/ilink/legacy.png"
    _queue(db_session, object_ref=object_ref, state="deleting")
    db_session.commit()

    claims = ILinkRepository(db_session).claim_object_deletions(limit=1, now=NOW)

    assert len(claims) == 1
    assert claims[0].object_ref == object_ref
    assert claims[0].claim_token is not None
    assert db_session.execute(
        text(
            "select attempt_count from messaging.ilink_object_deletion_queue "
            "where object_ref = :object_ref"
        ),
        {"object_ref": object_ref},
    ).scalar_one() == 1


def test_concurrent_deletion_claims_do_not_share_an_object(
    db_session_factory: sessionmaker[Session],
) -> None:
    object_ref = "tos://fake-bucket/private/ilink/concurrent.png"
    with db_session_factory.begin() as setup:
        _queue(setup, object_ref=object_ref)

    with db_session_factory() as first_session, db_session_factory() as second_session:
        first = ILinkRepository(first_session).claim_object_deletions(
            limit=1,
            now=NOW,
        )
        second = ILinkRepository(second_session).claim_object_deletions(
            limit=1,
            now=NOW,
        )

        assert len(first) == 1
        assert second == ()


class _Status404(Exception):
    status_code = 404


class _ResponseLostDeleteClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.deleted = False

    def delete_object(self, bucket: str, key: str) -> None:
        self.calls.append((bucket, key))
        if not self.deleted:
            self.deleted = True
            raise TimeoutError("response lost after provider accepted DELETE")
        raise _Status404("object already deleted")


def test_response_loss_then_explicit_404_converges_to_one_logical_completion(
    db_session_factory: sessionmaker[Session],
) -> None:
    object_ref = f"tos://fake-bucket/private/ilink/{uuid4()}.png"
    with db_session_factory.begin() as setup:
        _queue(setup, object_ref=object_ref)

    client = _ResponseLostDeleteClient()
    storage = TosTransientImageStore(
        Settings(
            tos_access_key="fake-access",
            tos_secret_key="fake-secret",
            tos_endpoint="https://tos.example.invalid",
            tos_region="fake-region",
            tos_bucket="fake-bucket",
        ),
        client=client,
    )
    clock_value = [NOW]
    worker = ILinkWorker.__new__(ILinkWorker)
    worker._sessions = db_session_factory
    worker._storage = storage
    worker._clock = lambda: clock_value[0]

    worker._drain_object_deletions()
    with db_session_factory() as check:
        failed = (
            check.execute(
                text(
                    """
                    select state, failure_code, claim_token, lease_expires_at
                    from messaging.ilink_object_deletion_queue
                    where object_ref = :object_ref
                    """
                ),
                {"object_ref": object_ref},
            )
            .mappings()
            .one()
        )
        assert dict(failed) == {
            "state": "failed",
            "failure_code": "transient_delete_failed",
            "claim_token": None,
            "lease_expires_at": None,
        }

    clock_value[0] = NOW + timedelta(seconds=1)
    worker._drain_object_deletions()

    assert client.deleted is True
    assert len(client.calls) == 2
    with db_session_factory() as check:
        assert check.execute(
            text(
                "select count(*) from messaging.ilink_object_deletion_queue "
                "where object_ref = :object_ref"
            ),
            {"object_ref": object_ref},
        ).scalar_one() == 0
