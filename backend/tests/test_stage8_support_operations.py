from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.operations.support import (
    SupportAuthenticationError,
    SupportJwtVerifier,
    SupportPrincipal,
    read_order_for_support,
)


def test_support_jwt_requires_verified_commerce_role() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    now = datetime.now(UTC)
    settings = Settings(
        support_jwt_jwks_url="https://identity.example.test/jwks.json",
        support_jwt_issuer="https://identity.example.test/",
        support_jwt_audience="gongkao-dazi-support",
    )
    verifier = SupportJwtVerifier(
        settings,
        signing_key_resolver=lambda _: public_key,
    )
    token = jwt.encode(
        {
            "sub": "support-agent-17",
            "roles": ["commerce_support"],
            "iss": settings.support_jwt_issuer,
            "aud": settings.support_jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )
    principal = verifier.verify(f"Bearer {token}")
    assert principal.subject == "support-agent-17"
    assert principal.roles == ("commerce_support",)

    unauthorized = jwt.encode(
        {
            "sub": "analyst-4",
            "roles": ["analytics"],
            "iss": settings.support_jwt_issuer,
            "aud": settings.support_jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
    )
    with pytest.raises(SupportAuthenticationError, match="role is required"):
        verifier.verify(f"Bearer {unauthorized}")


def test_support_order_view_is_minimal_and_every_read_is_append_only_audited(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    user_id = str(uuid4())
    order_id = uuid4()
    out_trade_no = f"S8{uuid4().hex[:20].upper()}"
    db_session.execute(
        text(
            """
            insert into learner.users (user_id, display_name, email)
            values (cast(:user_id as uuid), '不应暴露', 'private@example.test')
            """
        ),
        {"user_id": user_id},
    )
    db_session.execute(
        text(
            """
            insert into commerce.wechat_orders (
              order_id, command_key, request_fingerprint, user_id,
              offer_version_id, out_trade_no, payer_open_id, currency,
              amount_minor, state, terms_snapshot, terms_snapshot_sha256,
              prepay_id, provider_transaction_id, provider_trade_state,
              refunded_minor, expires_at, paid_at, updated_at, created_at
            ) values (
              cast(:order_id as uuid), 'support-order-command-0001',
              repeat('a', 64), cast(:user_id as uuid),
              '35000000-0000-0000-0000-000000000001', :out_trade_no,
              'openid-private-value', 'CNY', 6900, 'paid',
              jsonb_build_object('privateTerm', '不应暴露'), repeat('b', 64),
              'prepay-private', 'transaction-private', 'SUCCESS', 0,
              :expires_at, :paid_at, :paid_at, :paid_at
            )
            """
        ),
        {
            "order_id": str(order_id),
            "user_id": user_id,
            "out_trade_no": out_trade_no,
            "expires_at": now + timedelta(minutes=30),
            "paid_at": now,
        },
    )
    db_session.commit()

    view = read_order_for_support(
        db_session,
        principal=SupportPrincipal(
            subject="support-agent-17", roles=("commerce_support",)
        ),
        out_trade_no=out_trade_no,
        ticket_ref="CS-20260717-001",
        access_reason="用户询问微信支付到账状态",
        occurred_at=now,
    )
    payload = view.model_dump(by_alias=True)
    assert payload["outTradeNo"] == out_trade_no
    assert payload["state"] == "paid"
    assert "userId" not in payload
    assert "payerOpenId" not in payload
    assert "termsSnapshot" not in payload
    audit = db_session.execute(
        text("select * from privacy.support_access_audit_events")
    ).mappings().one()
    assert audit["actor_subject"] == "support-agent-17"
    assert audit["ticket_ref"] == "CS-20260717-001"
    assert out_trade_no not in audit["target_ref_sha256"]

    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(
            text(
                "update privacy.support_access_audit_events "
                "set access_reason = 'tampered'"
            )
        )
    db_session.rollback()
