from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.qualification.rule_releases import (
    PositionReleaseWindowInput,
    PositionRuleReleaseService,
    RuleReleaseContractError,
)


NOW = datetime(2026, 7, 12, 16, tzinfo=UTC)


def _audited_windows() -> list[PositionReleaseWindowInput]:
    return [
        PositionReleaseWindowInput(
            kind="primary",
            sequence_no=1,
            opens_at=datetime(2026, 7, 10, tzinfo=UTC),
            closes_at=datetime(2026, 10, 31, tzinfo=UTC),
            official_source_url="https://example.gov.cn/guokao/2026/announcement",
            official_source_title="2026年度考试录用公务员公告",
            official_source_published_at=datetime(2026, 7, 1, tzinfo=UTC),
            official_source_received_at=datetime(2026, 7, 2, tzinfo=UTC),
            official_source_sha256="a" * 64,
            official_source_receipt="fixture-receipt-primary",
        )
    ]


def _seed_positions(factory: sessionmaker[Session]) -> list[str]:
    rows = [
        {
            "education": "本科及以上",
            "major": "法学、汉语言文学",
            "political": "不限",
            "service": 2,
            "hukou": None,
            "remark": None,
        },
        {
            "education": "本科",
            "major": "法学类",
            "political": "中共党员",
            "service": 0,
            "hukou": "限江西省户籍或生源",
            "remark": "须持有法律职业资格证书A证",
        },
        {
            "education": "不限",
            "major": "不限",
            "political": "不限",
            "service": 0,
            "hukou": "不限",
            "remark": "无特殊要求",
        },
    ]
    ids: list[str] = []
    with factory.begin() as session:
        for index, row in enumerate(rows):
            position_id = str(uuid4())
            ids.append(position_id)
            session.execute(
                text(
                    """
                    insert into product.positions (
                      position_id, source_id, year, position_code, name,
                      department_full, system_code, region, paper_type, headcount,
                      education_min, major_required, political_req,
                      service_min_yrs, hukou_req, remark, raw_json
                    ) values (
                      :position_id, :source_id, 2026, :position_code,
                      '一级行政执法员', '国家税务总局测试局', 'tax', '江西',
                      'administrativeLaw', 2, :education, :major, :political,
                      :service, :hukou, :remark, '{}'::jsonb
                    )
                    """
                ),
                {
                    "position_id": position_id,
                    "source_id": f"rule-source-{index}",
                    "position_code": f"30011000100{index}",
                    **row,
                },
            )
    return ids


def test_compile_release_is_audited_conservative_and_idempotent(
    db_session_factory: sessionmaker[Session],
) -> None:
    position_ids = _seed_positions(db_session_factory)
    service = PositionRuleReleaseService()
    with db_session_factory.begin() as session:
        release_id = service.ensure_draft(
            session,
            exam_code="guokao",
            exam_year=2026,
            rule_set_version="guokao-2026-rules-v1",
            position_data_version="guokao-2026-fixture-v1",
        )
        summary = service.compile_draft(session, release_id=release_id, compiled_at=NOW)

    assert summary.position_count == 3
    assert summary.deterministic_count == 1
    assert summary.needs_confirmation_count == 1
    assert summary.no_conditions_count == 1
    assert summary.requirements_count == 8
    assert summary.manual_review_count == 1

    with db_session_factory.begin() as session:
        repeated = service.compile_draft(session, release_id=release_id, compiled_at=NOW)
    assert repeated == summary

    with db_session_factory() as session:
        audits = (
            session.execute(
                text(
                    """
                select
                  position_id,
                  status,
                  requirements_count,
                  manual_review_count,
                  warnings
                from product.position_rule_compilations
                order by position_id
                """
                )
            )
            .mappings()
            .all()
        )
        requirements = (
            session.execute(
                text(
                    """
                select position_id::text as position_id, fact_key, operator, source_reference
                from product.position_requirements
                order by position_id, fact_key
                """
                )
            )
            .mappings()
            .all()
        )
    assert len(audits) == 3
    assert sum(row["requirements_count"] for row in audits) == len(requirements)
    manual_fields = {row["fact_key"] for row in requirements if row["operator"] == "manual_review"}
    assert manual_fields == {"hukou"}
    assert any(
        row["fact_key"] == "major" and row["operator"] == "contains_any" for row in requirements
    )
    assert all(
        row["source_reference"].startswith("product.position_snapshots:guokao-2026-fixture-v1:")
        for row in requirements
    )
    assert {row["position_id"] for row in requirements}.issubset(set(position_ids))
    catalog_unavailable_count = sum(
        "major_catalog_unavailable" in row["warnings"] for row in audits
    )
    assert catalog_unavailable_count == 0


def test_release_cannot_activate_until_every_position_is_compiled(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_positions(db_session_factory)
    service = PositionRuleReleaseService()
    with db_session_factory.begin() as session:
        release_id = service.ensure_draft(
            session,
            exam_code="guokao",
            exam_year=2026,
            rule_set_version="guokao-2026-rules-v1",
            position_data_version="guokao-2026-fixture-v1",
        )

    with pytest.raises(RuleReleaseContractError, match="Every position"):
        with db_session_factory.begin() as session:
            service.activate(session, release_id=release_id, activated_at=NOW)


def test_activation_is_atomic_and_publishes_release_event(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_positions(db_session_factory)
    service = PositionRuleReleaseService()
    with db_session_factory.begin() as session:
        release_id = service.ensure_draft(
            session,
            exam_code="guokao",
            exam_year=2026,
            rule_set_version="guokao-2026-rules-v1",
            position_data_version="guokao-2026-fixture-v1",
        )
        service.compile_draft(session, release_id=release_id, compiled_at=NOW)
        service.configure_windows(
            session,
            release_id=release_id,
            windows=_audited_windows(),
        )
        service.activate(session, release_id=release_id, activated_at=NOW)

    with db_session_factory() as session:
        release = (
            session.execute(text("select status, activated_at from product.position_rule_releases"))
            .mappings()
            .one()
        )
        outbox = (
            session.execute(
                text(
                    """
                select event_type, aggregate_id, payload
                from platform.outbox_events
                where deduplication_key = :key
                """
                ),
                {"key": f"position-rule-release:{release_id}:activated"},
            )
            .mappings()
            .one()
        )
    assert release["status"] == "active"
    assert release["activated_at"] == NOW
    assert outbox["event_type"] == "position_rule_release.activated"
    assert outbox["aggregate_id"] == release_id


def test_compiled_release_rejects_source_mutation_under_same_version(
    db_session_factory: sessionmaker[Session],
) -> None:
    position_ids = _seed_positions(db_session_factory)
    service = PositionRuleReleaseService()
    with db_session_factory.begin() as session:
        release_id = service.ensure_draft(
            session,
            exam_code="guokao",
            exam_year=2026,
            rule_set_version="guokao-2026-rules-v1",
            position_data_version="guokao-2026-fixture-v1",
        )
        service.compile_draft(session, release_id=release_id, compiled_at=NOW)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update product.positions set major_required = '新闻传播学类' where position_id = :id"
            ),
            {"id": position_ids[0]},
        )

    with pytest.raises(RuleReleaseContractError, match="immutable"):
        with db_session_factory.begin() as session:
            service.compile_draft(session, release_id=release_id, compiled_at=NOW)
