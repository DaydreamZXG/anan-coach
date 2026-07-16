from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import secrets
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.auth.apple import AppleIdentityClaims
from app.domains.auth.wechat import WechatIdentityClaims
from app.domains.onboarding.progress_repository import OnboardingProgressRepository
from app.domains.onboarding.v2_schemas import OnboardingProgress

SESSION_TTL_DAYS = 30


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    learner_key: str
    display_name: str | None
    email: str | None


@dataclass(frozen=True)
class CreatedSession:
    access_token: str
    expires_at: datetime
    user: AuthenticatedUser


class AuthAccountUnavailableError(RuntimeError):
    def __init__(self, account_status: str) -> None:
        super().__init__(account_status)
        self.account_status = account_status


@dataclass(frozen=True)
class AccountOverviewRecord:
    user_id: str
    account_status: str
    wechat_linked: bool
    profile_version: int
    created_at: datetime


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    device_id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool


class AuthRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_apple_user(
        self,
        *,
        claims: AppleIdentityClaims,
        display_name: str | None,
        identity_token_hash: str,
    ) -> AuthenticatedUser:
        try:
            existing = self._session.execute(
                text(
                    """
                    select
                      u.user_id::text as user_id,
                      u.display_name,
                      coalesce(ai.email, u.email) as email,
                      u.account_status
                    from learner.apple_identities ai
                    join learner.users u on u.user_id = ai.user_id
                    where ai.apple_sub = :apple_sub
                    """
                ),
                {"apple_sub": claims.apple_sub},
            ).mappings().first()

            if existing is None:
                user_id = str(uuid4())
                self._session.execute(
                    text(
                        """
                        insert into learner.users (
                          user_id,
                          display_name,
                          email,
                          created_at,
                          updated_at
                        ) values (
                          cast(:user_id as uuid),
                          :display_name,
                          :email,
                          now(),
                          now()
                        )
                        """
                    ),
                    {
                        "user_id": user_id,
                        "display_name": display_name,
                        "email": claims.email,
                    },
                )
                self._session.execute(
                    text(
                        """
                        insert into learner.apple_identities (
                          identity_id,
                          user_id,
                          apple_sub,
                          email,
                          email_verified,
                          is_private_email,
                          last_identity_token_hash,
                          created_at,
                          updated_at
                        ) values (
                          cast(:identity_id as uuid),
                          cast(:user_id as uuid),
                          :apple_sub,
                          :email,
                          :email_verified,
                          :is_private_email,
                          :last_identity_token_hash,
                          now(),
                          now()
                        )
                        """
                    ),
                    {
                        "identity_id": str(uuid4()),
                        "user_id": user_id,
                        "apple_sub": claims.apple_sub,
                        "email": claims.email,
                        "email_verified": claims.email_verified,
                        "is_private_email": claims.is_private_email,
                        "last_identity_token_hash": identity_token_hash,
                    },
                )
                self._ensure_profile_row(user_id)
                self._session.commit()
                return AuthenticatedUser(
                    user_id=user_id,
                    learner_key=user_id,
                    display_name=display_name,
                    email=claims.email,
                )

            user_id = str(existing["user_id"])
            self._ensure_account_available(
                user_id=user_id,
                account_status=str(existing["account_status"]),
            )
            resolved_display_name = display_name or existing["display_name"]
            self._session.execute(
                text(
                    """
                    update learner.apple_identities
                    set
                      email = coalesce(:email, email),
                      email_verified = :email_verified,
                      is_private_email = :is_private_email,
                      last_identity_token_hash = :last_identity_token_hash,
                      updated_at = now()
                    where apple_sub = :apple_sub
                    """
                ),
                {
                    "apple_sub": claims.apple_sub,
                    "email": claims.email,
                    "email_verified": claims.email_verified,
                    "is_private_email": claims.is_private_email,
                    "last_identity_token_hash": identity_token_hash,
                },
            )
            if display_name:
                self._session.execute(
                    text(
                        """
                        update learner.users
                        set display_name = :display_name, updated_at = now()
                        where user_id = cast(:user_id as uuid)
                        """
                    ),
                    {"user_id": user_id, "display_name": display_name},
                )
            self._ensure_profile_row(user_id)
            self._session.commit()
            return AuthenticatedUser(
                user_id=user_id,
                learner_key=user_id,
                display_name=resolved_display_name,
                email=claims.email or existing["email"],
            )
        except Exception:
            self._session.rollback()
            raise

    def upsert_wechat_user(
        self,
        *,
        claims: WechatIdentityClaims,
    ) -> AuthenticatedUser:
        try:
            existing = self._session.execute(
                text(
                    """
                    select
                      u.user_id::text as user_id,
                      u.display_name,
                      u.email,
                      u.account_status,
                      u.is_active
                    from learner.wechat_identities wi
                    join learner.users u on u.user_id = wi.user_id
                    where wi.app_id = :app_id
                      and wi.open_id = :open_id
                    """
                ),
                {"app_id": claims.app_id, "open_id": claims.open_id},
            ).mappings().first()

            if existing is None:
                user_id = str(uuid4())
                self._session.execute(
                    text(
                        """
                        insert into learner.users (
                          user_id,
                          display_name,
                          created_at,
                          updated_at
                        ) values (
                          cast(:user_id as uuid),
                          null,
                          now(),
                          now()
                        )
                        """
                    ),
                    {"user_id": user_id},
                )
                self._session.execute(
                    text(
                        """
                        insert into learner.wechat_identities (
                          identity_id,
                          user_id,
                          app_id,
                          open_id,
                          union_id,
                          session_key_hash,
                          last_login_at,
                          created_at,
                          updated_at
                        ) values (
                          cast(:identity_id as uuid),
                          cast(:user_id as uuid),
                          :app_id,
                          :open_id,
                          :union_id,
                          :session_key_hash,
                          now(),
                          now(),
                          now()
                        )
                        """
                    ),
                    {
                        "identity_id": str(uuid4()),
                        "user_id": user_id,
                        "app_id": claims.app_id,
                        "open_id": claims.open_id,
                        "union_id": claims.union_id,
                        "session_key_hash": claims.session_key_hash,
                    },
                )
                self._ensure_profile_row(user_id)
                self._session.commit()
                return AuthenticatedUser(
                    user_id=user_id,
                    learner_key=user_id,
                    display_name=None,
                    email=None,
                )

            user_id = str(existing["user_id"])
            self._ensure_account_available(
                user_id=user_id,
                account_status=str(existing["account_status"]),
            )
            self._session.execute(
                text(
                    """
                    update learner.wechat_identities
                    set
                      union_id = coalesce(:union_id, union_id),
                      session_key_hash = :session_key_hash,
                      last_login_at = now(),
                      updated_at = now()
                    where app_id = :app_id
                      and open_id = :open_id
                    """
                ),
                {
                    "app_id": claims.app_id,
                    "open_id": claims.open_id,
                    "union_id": claims.union_id,
                    "session_key_hash": claims.session_key_hash,
                },
            )
            self._ensure_profile_row(user_id)
            self._session.commit()
            return AuthenticatedUser(
                user_id=user_id,
                learner_key=user_id,
                display_name=existing["display_name"],
                email=existing["email"],
            )
        except Exception:
            self._session.rollback()
            raise

    def create_session(
        self,
        *,
        user: AuthenticatedUser,
        device_id: str,
    ) -> CreatedSession:
        access_token = f"gkd_{secrets.token_urlsafe(48)}"
        expires_at = datetime.now(UTC) + timedelta(days=SESSION_TTL_DAYS)
        self._session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id,
                  user_id,
                  session_token_hash,
                  device_id,
                  expires_at,
                  created_at,
                  last_seen_at
                ) values (
                  cast(:session_id as uuid),
                  cast(:user_id as uuid),
                  :session_token_hash,
                  :device_id,
                  :expires_at,
                  now(),
                  now()
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user.user_id,
                "session_token_hash": _token_hash(access_token),
                "device_id": device_id,
                "expires_at": expires_at,
            },
        )
        self._session.commit()
        return CreatedSession(access_token=access_token, expires_at=expires_at, user=user)

    def get_user_by_access_token(self, access_token: str) -> AuthenticatedUser | None:
        row = self._session.execute(
            text(
                """
                select
                  u.user_id::text as user_id,
                  u.display_name,
                  u.email
                from learner.app_sessions s
                join learner.users u on u.user_id = s.user_id
                where s.session_token_hash = :session_token_hash
                  and s.revoked_at is null
                  and s.expires_at > now()
                  and u.is_active = true
                  and u.account_status = 'active'
                """
            ),
            {"session_token_hash": _token_hash(access_token)},
        ).mappings().first()
        if row is None:
            return None
        self._session.execute(
            text(
                """
                update learner.app_sessions
                set last_seen_at = now()
                where session_token_hash = :session_token_hash
                """
            ),
            {"session_token_hash": _token_hash(access_token)},
        )
        self._session.commit()
        user_id = str(row["user_id"])
        return AuthenticatedUser(
            user_id=user_id,
            learner_key=user_id,
            display_name=row["display_name"],
            email=row["email"],
        )

    def get_active_session(self, *, access_token: str, user_id: str) -> SessionRecord | None:
        """Resolve the exact authenticated app session behind a bearer token."""
        row = self._session.execute(
            text(
                """
                select session_id::text as session_id, device_id, created_at,
                       last_seen_at, expires_at
                from learner.app_sessions
                where session_token_hash = :session_token_hash
                  and user_id = cast(:user_id as uuid)
                  and revoked_at is null
                  and expires_at > now()
                """
            ),
            {"session_token_hash": _token_hash(access_token), "user_id": user_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        return SessionRecord(
            session_id=str(row["session_id"]),
            device_id=str(row["device_id"]),
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            expires_at=row["expires_at"],
            is_current=True,
        )

    def revoke_session(self, access_token: str, *, reason: str = "user_logout") -> None:
        revoked = self._session.execute(
            text(
                """
                update learner.app_sessions
                set revoked_at = now(), revoked_reason = :reason
                where session_token_hash = :session_token_hash
                  and revoked_at is null
                returning user_id::text as user_id, session_id::text as session_id
                """
            ),
            {"session_token_hash": _token_hash(access_token), "reason": reason},
        ).mappings().first()
        if revoked is not None:
            self._append_account_event(
                user_id=str(revoked["user_id"]),
                event_type="session_revoked",
                metadata={
                    "sessionId": str(revoked["session_id"]),
                    "reason": reason,
                },
            )
        self._session.commit()

    def get_account_overview(self, *, user_id: str) -> AccountOverviewRecord:
        row = self._session.execute(
            text(
                """
                select u.user_id::text as user_id, u.account_status, u.created_at,
                       p.profile_version,
                       exists (
                         select 1 from learner.wechat_identities wi
                         where wi.user_id = u.user_id
                       ) as wechat_linked
                from learner.users u
                join learner.profiles p on p.user_id = u.user_id
                where u.user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
        return AccountOverviewRecord(
            user_id=row["user_id"],
            account_status=row["account_status"],
            wechat_linked=bool(row["wechat_linked"]),
            profile_version=int(row["profile_version"]),
            created_at=row["created_at"],
        )

    def list_active_sessions(
        self,
        *,
        user_id: str,
        current_access_token: str,
    ) -> list[SessionRecord]:
        current_hash = _token_hash(current_access_token)
        rows = self._session.execute(
            text(
                """
                select session_id::text as session_id, device_id, created_at,
                       last_seen_at, expires_at,
                       session_token_hash = :current_hash as is_current
                from learner.app_sessions
                where user_id = cast(:user_id as uuid)
                  and revoked_at is null
                  and expires_at > now()
                order by is_current desc, last_seen_at desc, session_id
                """
            ),
            {"user_id": user_id, "current_hash": current_hash},
        ).mappings()
        return [
            SessionRecord(
                session_id=row["session_id"],
                device_id=row["device_id"],
                created_at=row["created_at"],
                last_seen_at=row["last_seen_at"],
                expires_at=row["expires_at"],
                is_current=bool(row["is_current"]),
            )
            for row in rows
        ]

    def revoke_owned_session(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> bool:
        row = self._session.execute(
            text(
                """
                update learner.app_sessions
                set revoked_at = now(), revoked_reason = 'user_revoked_device'
                where session_id = cast(:session_id as uuid)
                  and user_id = cast(:user_id as uuid)
                  and revoked_at is null
                returning session_id
                """
            ),
            {"user_id": user_id, "session_id": session_id},
        ).scalar_one_or_none()
        if row is not None:
            self._append_account_event(
                user_id=user_id,
                event_type="session_revoked",
                metadata={"sessionId": session_id},
            )
        self._session.commit()
        return row is not None

    def revoke_all_sessions(self, *, user_id: str) -> int:
        count = self._session.execute(
            text(
                """
                update learner.app_sessions
                set revoked_at = now(), revoked_reason = 'user_logout_all'
                where user_id = cast(:user_id as uuid)
                  and revoked_at is null
                """
            ),
            {"user_id": user_id},
        ).rowcount
        self._append_account_event(
            user_id=user_id,
            event_type="all_sessions_revoked",
            metadata={"revokedSessionCount": int(count or 0)},
        )
        self._session.commit()
        return int(count or 0)

    def is_onboarding_complete(self, *, user_id: str) -> bool:
        return self.get_onboarding_progress(user_id=user_id).completed

    def get_onboarding_progress(self, *, user_id: str) -> OnboardingProgress:
        progress = OnboardingProgressRepository().get_progress(
            self._session,
            user_id=user_id,
        )
        self._session.commit()
        return progress

    def _ensure_profile_row(self, user_id: str) -> None:
        self._session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id,
                  created_at,
                  updated_at
                ) values (
                  cast(:user_id as uuid),
                  now(),
                  now()
                )
                on conflict (user_id) do nothing
                """
            ),
            {"user_id": user_id},
        )
        self._session.execute(
            text(
                """
                insert into learner.onboarding_progress (
                  user_id, progress_version, qualification_ready,
                  qualification_profile_version, study_preference_ready,
                  study_preference_version, result_ready,
                  result_acknowledged, completed, created_at, updated_at
                ) values (
                  cast(:user_id as uuid), 1, false, null, false, null,
                  false, false, false, now(), now()
                )
                on conflict (user_id) do nothing
                """
            ),
            {"user_id": user_id},
        )

    def _ensure_account_available(
        self,
        *,
        user_id: str,
        account_status: str,
    ) -> None:
        if account_status in {"deletion_pending", "disabled"}:
            raise AuthAccountUnavailableError(account_status)
        if account_status != "recovery_required":
            return
        self._session.execute(
            text(
                """
                update learner.users
                set account_status = 'active', is_active = true,
                    account_status_changed_at = now(), updated_at = now()
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        )
        revoked = self._session.execute(
            text(
                """
                update learner.app_sessions
                set revoked_at = now(), revoked_reason = 'account_recovery'
                where user_id = cast(:user_id as uuid)
                  and revoked_at is null
                """
            ),
            {"user_id": user_id},
        ).rowcount
        self._append_account_event(
            user_id=user_id,
            event_type="account_recovered",
            metadata={"revokedSessionCount": int(revoked or 0)},
        )

    def _append_account_event(
        self,
        *,
        user_id: str,
        event_type: str,
        metadata: dict[str, object],
    ) -> None:
        self._session.execute(
            text(
                """
                insert into learner.account_events (
                  event_id, user_id, event_type, metadata, created_at
                ) values (
                  cast(:event_id as uuid), cast(:user_id as uuid),
                  :event_type, cast(:metadata as jsonb), now()
                )
                """
            ),
            {
                "event_id": str(uuid4()),
                "user_id": user_id,
                "event_type": event_type,
                "metadata": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            },
        )


def _token_hash(access_token: str) -> str:
    return sha256(access_token.encode("utf-8")).hexdigest()


def token_hash_for_tests(access_token: str) -> str:
    return _token_hash(access_token)
