from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class AIQuotaExceededError(RuntimeError):
    pass


class AIQuotaRefundError(RuntimeError):
    pass


@dataclass(frozen=True)
class QuotaReservation:
    feature: str
    used: int
    limit: int
    usage_date: date


class AIAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def reserve_quota(
        self,
        *,
        learner_key: str,
        feature: str,
        limit: int,
    ) -> QuotaReservation:
        if limit < 1:
            raise AIQuotaExceededError(feature)
        usage_date = datetime.now(UTC).date()
        row = self._session.execute(
            text(
                """
                insert into ai_audit.usage_daily (
                  learner_key,
                  feature,
                  usage_date,
                  used_count,
                  limit_count,
                  updated_at
                ) values (
                  :learner_key,
                  :feature,
                  :usage_date,
                  1,
                  :limit_count,
                  now()
                )
                on conflict (learner_key, feature, usage_date) do update set
                  used_count = ai_audit.usage_daily.used_count + 1,
                  limit_count = excluded.limit_count,
                  updated_at = now()
                where ai_audit.usage_daily.used_count < ai_audit.usage_daily.limit_count
                returning used_count, limit_count
                """
            ),
            {
                "learner_key": learner_key,
                "feature": feature,
                "usage_date": usage_date,
                "limit_count": limit,
            },
        ).mappings().first()
        if row is None:
            self._session.rollback()
            raise AIQuotaExceededError(feature)
        self._session.commit()
        return QuotaReservation(
            feature=feature,
            used=int(row["used_count"]),
            limit=int(row["limit_count"]),
            usage_date=usage_date,
        )

    def refund_quota(
        self,
        *,
        learner_key: str,
        feature: str,
        usage_date: date,
    ) -> QuotaReservation:
        row = self._session.execute(
            text(
                """
                update ai_audit.usage_daily
                set
                  used_count = greatest(used_count - 1, 0),
                  updated_at = now()
                where learner_key = :learner_key
                  and feature = :feature
                  and usage_date = :usage_date
                  and used_count > 0
                returning used_count, limit_count
                """
            ),
            {
                "learner_key": learner_key,
                "feature": feature,
                "usage_date": usage_date,
            },
        ).mappings().first()
        if row is None:
            self._session.rollback()
            raise AIQuotaRefundError(feature)
        self._session.commit()
        return QuotaReservation(
            feature=feature,
            used=int(row["used_count"]),
            limit=int(row["limit_count"]),
            usage_date=usage_date,
        )

    def log_interaction(
        self,
        *,
        log_id: str,
        learner_key: str,
        feature: str,
        provider: str,
        model: str,
        status: str,
        request_summary: str,
        response_summary: str,
        latency_ms: int,
        metadata: dict[str, Any],
        commit: bool = True,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into ai_audit.interaction_logs (
                  log_id,
                  learner_key,
                  feature,
                  provider,
                  model,
                  status,
                  request_summary,
                  response_summary,
                  latency_ms,
                  metadata,
                  created_at
                ) values (
                  :log_id,
                  :learner_key,
                  :feature,
                  :provider,
                  :model,
                  :status,
                  :request_summary,
                  :response_summary,
                  :latency_ms,
                  cast(:metadata as jsonb),
                  now()
                )
                """
            ),
            {
                "log_id": log_id,
                "learner_key": learner_key,
                "feature": feature,
                "provider": provider,
                "model": model,
                "status": status,
                "request_summary": request_summary,
                "response_summary": response_summary,
                "latency_ms": latency_ms,
                "metadata": json.dumps(metadata, ensure_ascii=False),
            },
        )
        if commit:
            self._session.commit()
