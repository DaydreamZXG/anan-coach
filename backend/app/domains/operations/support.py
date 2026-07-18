from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Protocol
from uuid import uuid4

import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings


class SupportConfigurationError(RuntimeError):
    pass


class SupportAuthenticationError(RuntimeError):
    pass


class SupportOrderNotFound(LookupError):
    pass


class SigningKeyResolver(Protocol):
    def __call__(self, token: str) -> Any: ...


@dataclass(frozen=True)
class SupportPrincipal:
    subject: str
    roles: tuple[str, ...]


class SupportRefundView(BaseModel):
    out_refund_no: str = Field(serialization_alias="outRefundNo")
    refund_kind: str = Field(serialization_alias="refundKind")
    amount_minor: int = Field(serialization_alias="amountMinor")
    state: str
    requested_at: datetime = Field(serialization_alias="requestedAt")
    processed_at: datetime | None = Field(serialization_alias="processedAt")


class SupportOrderView(BaseModel):
    out_trade_no: str = Field(serialization_alias="outTradeNo")
    state: str
    currency: str
    amount_minor: int = Field(serialization_alias="amountMinor")
    refunded_minor: int = Field(serialization_alias="refundedMinor")
    paid_at: datetime | None = Field(serialization_alias="paidAt")
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
    refunds: list[SupportRefundView]


class SupportJwtVerifier:
    def __init__(
        self,
        settings: Settings,
        *,
        signing_key_resolver: SigningKeyResolver | None = None,
    ) -> None:
        required = {
            "SUPPORT_JWT_JWKS_URL": settings.support_jwt_jwks_url,
            "SUPPORT_JWT_ISSUER": settings.support_jwt_issuer,
            "SUPPORT_JWT_AUDIENCE": settings.support_jwt_audience,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise SupportConfigurationError(
                f"support identity configuration is incomplete: {', '.join(missing)}"
            )
        self._issuer = settings.support_jwt_issuer
        self._audience = settings.support_jwt_audience
        if signing_key_resolver is None:
            client = PyJWKClient(settings.support_jwt_jwks_url, cache_keys=True)
            signing_key_resolver = lambda token: client.get_signing_key_from_jwt(
                token
            ).key
        self._resolve_signing_key = signing_key_resolver

    def verify(self, authorization: str) -> SupportPrincipal:
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not token.strip():
            raise SupportAuthenticationError("support bearer token is required")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise SupportAuthenticationError("support token must use RS256")
            payload = jwt.decode(
                token,
                key=self._resolve_signing_key(token),
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except SupportAuthenticationError:
            raise
        except Exception as exc:
            raise SupportAuthenticationError("support token verification failed") from exc
        subject = payload.get("sub")
        roles = payload.get("roles")
        if not isinstance(subject, str) or not subject.strip():
            raise SupportAuthenticationError("support token subject is invalid")
        if not isinstance(roles, list) or not all(
            isinstance(role, str) for role in roles
        ):
            raise SupportAuthenticationError("support token roles are invalid")
        normalized_roles = tuple(sorted(set(roles)))
        if "commerce_support" not in normalized_roles:
            raise SupportAuthenticationError("commerce support role is required")
        return SupportPrincipal(subject=subject, roles=normalized_roles)


def read_order_for_support(
    session: Session,
    *,
    principal: SupportPrincipal,
    out_trade_no: str,
    ticket_ref: str,
    access_reason: str,
    occurred_at: datetime,
) -> SupportOrderView:
    if not 8 <= len(out_trade_no) <= 32:
        raise ValueError("merchant order number must be 8 to 32 characters")
    normalized_ticket = ticket_ref.strip()
    normalized_reason = access_reason.strip()
    if not 4 <= len(normalized_ticket) <= 120:
        raise ValueError("support ticket reference must be 4 to 120 characters")
    if not 8 <= len(normalized_reason) <= 500:
        raise ValueError("support access reason must be 8 to 500 characters")
    row = session.execute(
        text(
            """
            select out_trade_no, state, currency, amount_minor, refunded_minor,
                   paid_at, created_at, updated_at
            from commerce.wechat_orders
            where out_trade_no = :out_trade_no
            """
        ),
        {"out_trade_no": out_trade_no},
    ).mappings().one_or_none()
    if row is None:
        raise SupportOrderNotFound("commerce order")
    refunds = session.execute(
        text(
            """
            select refund.out_refund_no, refund.refund_kind,
                   refund.amount_minor, refund.state,
                   refund.requested_at, refund.processed_at
            from commerce.wechat_refunds refund
            join commerce.wechat_orders orders on orders.order_id = refund.order_id
            where orders.out_trade_no = :out_trade_no
            order by refund.requested_at, refund.refund_id
            """
        ),
        {"out_trade_no": out_trade_no},
    ).mappings().all()
    response_fields = [
        "outTradeNo",
        "state",
        "currency",
        "amountMinor",
        "refundedMinor",
        "paidAt",
        "createdAt",
        "updatedAt",
        "refunds",
    ]
    session.execute(
        text(
            """
            insert into privacy.support_access_audit_events (
              access_event_id, actor_subject, ticket_ref, access_reason,
              action, target_ref_sha256, response_fields, occurred_at
            ) values (
              cast(:event_id as uuid), :actor_subject, :ticket_ref,
              :access_reason, 'commerce_order_status_read', :target_ref_sha256,
              cast(:response_fields as jsonb), :occurred_at
            )
            """
        ),
        {
            "event_id": str(uuid4()),
            "actor_subject": principal.subject,
            "ticket_ref": normalized_ticket,
            "access_reason": normalized_reason,
            "target_ref_sha256": sha256(out_trade_no.encode("utf-8")).hexdigest(),
            "response_fields": json.dumps(response_fields),
            "occurred_at": occurred_at,
        },
    )
    session.commit()
    return SupportOrderView(
        **dict(row),
        refunds=[SupportRefundView(**dict(item)) for item in refunds],
    )
