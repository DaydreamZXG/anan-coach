from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.auth.apple import AppleIdentityClaims
from app.domains.auth.repository import AuthAccountUnavailableError, AuthRepository


APPLE_CLAIMS = AppleIdentityClaims(
    apple_sub="apple-account-lifecycle-sub",
    email="learner@example.com",
    email_verified=True,
    is_private_email=False,
)


def test_verified_apple_identity_recovers_the_existing_account(
    db_session: Session,
) -> None:
    repository = AuthRepository(db_session)
    created = repository.upsert_apple_user(
        claims=APPLE_CLAIMS,
        display_name="学习者",
        identity_token_hash="first-token-hash",
    )
    old_session = repository.create_session(user=created, device_id="ios-before-recovery")
    db_session.execute(
        text(
            """
            update learner.users
            set account_status = 'recovery_required', is_active = false,
                account_status_changed_at = now()
            where user_id = cast(:user_id as uuid)
            """
        ),
        {"user_id": created.user_id},
    )
    db_session.commit()

    recovered = repository.upsert_apple_user(
        claims=APPLE_CLAIMS,
        display_name=None,
        identity_token_hash="second-token-hash",
    )

    assert recovered.user_id == created.user_id
    assert repository.get_user_by_access_token(old_session.access_token) is None
    state = db_session.execute(
        text(
            """
            select u.account_status, u.is_active, s.revoked_reason, e.event_type
            from learner.users u
            join learner.app_sessions s on s.user_id = u.user_id
            join learner.account_events e on e.user_id = u.user_id
            where u.user_id = cast(:user_id as uuid)
            """
        ),
        {"user_id": created.user_id},
    ).mappings().one()
    assert state["account_status"] == "active"
    assert state["is_active"] is True
    assert state["revoked_reason"] == "account_recovery"
    assert state["event_type"] == "account_recovered"


def test_disabled_apple_identity_cannot_create_a_duplicate_account(
    db_session: Session,
) -> None:
    repository = AuthRepository(db_session)
    created = repository.upsert_apple_user(
        claims=APPLE_CLAIMS,
        display_name=None,
        identity_token_hash="first-token-hash",
    )
    db_session.execute(
        text(
            """
            update learner.users
            set account_status = 'disabled', is_active = false,
                account_status_changed_at = now()
            where user_id = cast(:user_id as uuid)
            """
        ),
        {"user_id": created.user_id},
    )
    db_session.commit()

    try:
        repository.upsert_apple_user(
            claims=APPLE_CLAIMS,
            display_name=None,
            identity_token_hash="second-token-hash",
        )
    except AuthAccountUnavailableError as exc:
        assert exc.account_status == "disabled"
    else:
        raise AssertionError("disabled Apple identity must not be accepted")

    assert db_session.execute(text("select count(*) from learner.users")).scalar_one() == 1
