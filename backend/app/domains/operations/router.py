from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db_session
from app.domains.operations.support import (
    SupportAuthenticationError,
    SupportConfigurationError,
    SupportJwtVerifier,
    SupportOrderNotFound,
    SupportOrderView,
    SupportPrincipal,
    read_order_for_support,
)


router = APIRouter(tags=["operations"])

POSITION_RELEASE_MAX_AGE = timedelta(days=365)


@lru_cache(maxsize=1)
def _support_verifier() -> SupportJwtVerifier:
    return SupportJwtVerifier(get_settings())


def _support_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> SupportPrincipal:
    if authorization is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        return _support_verifier().verify(authorization)
    except SupportConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="support identity provider is not configured",
        ) from exc
    except SupportAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="support identity is not authorized",
        ) from exc


@router.get("/ready")
def ready(session: Annotated[Session, Depends(get_db_session)]) -> dict[str, str]:
    settings = get_settings()
    session.execute(text("select 1")).scalar_one()
    migration = session.execute(text("select version_num from alembic_version")).scalar_one_or_none()
    required_tables_ready = session.execute(
        text(
            "select to_regclass('commerce.wechat_orders') is not null "
            "and to_regclass('privacy.data_export_requests') is not null "
            "and to_regclass('product.position_rule_releases') is not null"
        )
    ).scalar_one()
    if migration != settings.required_database_revision or not required_tables_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database schema is not at the required release",
        )

    checked_at = datetime.now(UTC)
    release = session.execute(
        text(
            """
            select rule_set_version, position_data_version
            from product.position_rule_releases
            where exam_code = 'guokao'
              and status = 'active'
              and activated_at between :minimum_activated_at and :checked_at
            order by exam_year desc, activated_at desc
            limit 1
            """
        ),
        {
            "minimum_activated_at": checked_at - POSITION_RELEASE_MAX_AGE,
            "checked_at": checked_at,
        },
    ).mappings().one_or_none()
    if release is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="position rule release is not ready",
        )
    return {
        "status": "ready",
        "migration": str(migration),
        "positionRuleRelease": str(release["rule_set_version"]),
        "positionDataVersion": str(release["position_data_version"]),
        "time": checked_at.isoformat(),
    }


@router.get("/internal/metrics", response_class=PlainTextResponse)
def metrics(
    session: Annotated[Session, Depends(get_db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    settings = get_settings()
    expected = f"Bearer {settings.operations_metrics_token}"
    if not settings.operations_metrics_token or authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    row = session.execute(
        text(
            """
            select
              (select count(*) from commerce.wechat_orders where state = 'payment_uncertain') as uncertain_orders,
              (select count(*) from commerce.wechat_orders where state = 'refund_processing' and updated_at < now() - interval '15 minutes') as stale_refunds,
              (select count(*) from platform.background_jobs where status = 'queued') as queued_jobs,
              (select count(*) from platform.background_jobs where status = 'failed') as failed_jobs,
              (select count(*) from privacy.account_deletion_requests where state = 'failed') as failed_deletions,
              (select count(*) from privacy.data_export_requests where state = 'failed') as failed_exports
            """
        )
    ).mappings().one()
    values = {
        "anan_wechat_payment_uncertain_orders": int(row["uncertain_orders"]),
        "anan_wechat_stale_refunds": int(row["stale_refunds"]),
        "anan_background_jobs_queued": int(row["queued_jobs"]),
        "anan_background_jobs_failed": int(row["failed_jobs"]),
        "anan_privacy_deletions_failed": int(row["failed_deletions"]),
        "anan_privacy_exports_failed": int(row["failed_exports"]),
    }
    return "\n".join(f"{key} {value}" for key, value in values.items()) + "\n"


@router.get(
    "/internal/support/commerce/orders/{out_trade_no}",
    response_model=SupportOrderView,
)
def support_commerce_order(
    out_trade_no: str,
    session: Annotated[Session, Depends(get_db_session)],
    principal: Annotated[SupportPrincipal, Depends(_support_principal)],
    ticket_ref: Annotated[str, Header(alias="X-Support-Ticket")],
    access_reason: Annotated[str, Header(alias="X-Support-Reason")],
) -> SupportOrderView:
    try:
        return read_order_for_support(
            session,
            principal=principal,
            out_trade_no=out_trade_no,
            ticket_ref=ticket_ref,
            access_reason=access_reason,
            occurred_at=datetime.now(UTC),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except SupportOrderNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
