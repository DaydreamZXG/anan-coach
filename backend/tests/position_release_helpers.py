from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


def seed_active_position_release(
    session: Session,
    *,
    activated_at: datetime,
    database_now: datetime,
    rule_set_version: str,
    position_data_version: str,
    exam_code: str = "guokao",
    exam_year: int = 2026,
) -> str:
    """Seed a DB-valid release fixture with one audited primary window."""

    release_id = str(uuid4())
    opens_at = min(activated_at, database_now) - timedelta(days=1)
    closes_at = max(activated_at, database_now) + timedelta(days=30)
    source_url = (
        f"https://example.gov.cn/{exam_code}/{exam_year}/test-position-release/{release_id}"
    )
    source_sha256 = sha256(source_url.encode("utf-8")).hexdigest()

    session.execute(
        text(
            """
            insert into product.position_rule_releases (
              release_id, exam_code, exam_year, rule_set_version,
              position_data_version, status, activated_at
            ) values (
              cast(:release_id as uuid), :exam_code, :exam_year,
              :rule_set_version, :position_data_version, 'draft', null
            )
            """
        ),
        {
            "release_id": release_id,
            "exam_code": exam_code,
            "exam_year": exam_year,
            "rule_set_version": rule_set_version,
            "position_data_version": position_data_version,
        },
    )
    session.execute(
        text(
            """
            insert into product.position_release_windows (
              window_id, release_id, window_kind, sequence_no,
              opens_at, closes_at, official_source_url,
              official_source_title, official_source_published_at,
              official_source_received_at, official_source_sha256,
              official_source_receipt
            ) values (
              gen_random_uuid(), cast(:release_id as uuid), 'primary', 1,
              :opens_at, :closes_at, :source_url, :source_title,
              :published_at, :received_at, :source_sha256, :source_receipt
            )
            """
        ),
        {
            "release_id": release_id,
            "opens_at": opens_at,
            "closes_at": closes_at,
            "source_url": source_url,
            "source_title": f"{exam_year} test position release",
            "published_at": opens_at - timedelta(days=2),
            "received_at": opens_at - timedelta(days=1),
            "source_sha256": source_sha256,
            "source_receipt": f"test-fixture:{release_id}",
        },
    )
    session.execute(
        text(
            """
            update product.position_rule_releases
            set status = 'active', activated_at = :activated_at
            where release_id = cast(:release_id as uuid)
              and status = 'draft'
            """
        ),
        {
            "release_id": release_id,
            "activated_at": activated_at,
        },
    )
    return release_id
