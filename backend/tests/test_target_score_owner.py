from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domains.positions.schemas import TargetScoreBasis
from app.domains.positions.target_scores import (
    PositionTargetScoreOwner,
    TargetScoreExpiredError,
    TargetScoreReleaseUnavailableError,
    TargetScoreVersionConflictError,
    target_score_range_from_basis,
)
from app.domains.qualification.repository import (
    PositionReleaseLifecycle,
    PositionReleaseWindow,
    PositionRuleRelease,
    PositionRuleReleaseUnavailable,
)
from app.main import create_app


NOW = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
WINDOW_OPENS_AT = NOW - timedelta(days=3)
WINDOW_CLOSES_AT = NOW + timedelta(days=120)


def _release(
    *,
    activated_at: datetime = NOW,
    window_opens_at: datetime = WINDOW_OPENS_AT,
    window_closes_at: datetime = WINDOW_CLOSES_AT,
) -> PositionRuleRelease:
    return PositionRuleRelease(
        release_id="11111111-1111-1111-1111-111111111111",
        exam_code="guokao",
        exam_year=2026,
        rule_set_version="guokao-2026-rules-v1",
        position_data_version="guokao-2026-positions-v1",
        content_digest="a" * 64,
        activated_at=activated_at,
        current_window=PositionReleaseWindow(
            window_id="22222222-2222-2222-2222-222222222222",
            kind="primary",
            sequence_no=1,
            opens_at=window_opens_at,
            closes_at=window_closes_at,
            official_source_url="https://example.gov.cn/guokao/2026/announcement",
            official_source_title="2026年度考试录用公务员公告",
            official_source_published_at=window_opens_at - timedelta(days=2),
            official_source_received_at=window_opens_at - timedelta(days=1),
            official_source_sha256="b" * 64,
            official_source_receipt="target-score-owner-fixture",
        ),
    )


def test_release_timestamps_are_deterministic_and_not_request_sliding() -> None:
    owner = PositionTargetScoreOwner()

    first = owner.context_for_release(release=_release(), now=NOW + timedelta(days=1))
    later = owner.context_for_release(release=_release(), now=NOW + timedelta(days=90))

    assert first.generated_at == NOW
    assert later.generated_at == NOW
    assert first.valid_until == WINDOW_CLOSES_AT
    assert later.valid_until == first.valid_until


def test_expired_release_is_rejected_with_stable_domain_error() -> None:
    owner = PositionTargetScoreOwner()

    with pytest.raises(TargetScoreExpiredError) as raised:
        owner.context_for_release(
            release=_release(
                activated_at=NOW - timedelta(days=365),
                window_opens_at=NOW - timedelta(days=365),
                window_closes_at=NOW,
            ),
            now=NOW,
        )

    assert raised.value.code == "target_score_basis_expired"


def test_no_active_release_is_rejected_with_stable_domain_error() -> None:
    class MissingReleaseRepository:
        def active_release(self, *_: Any, **__: Any) -> PositionRuleRelease:
            raise PositionRuleReleaseUnavailable(
                "missing",
                lifecycle=PositionReleaseLifecycle(
                    phase="no_release",
                    release_id=None,
                    exam_code="guokao",
                    exam_year=2026,
                    rule_set_version=None,
                    position_data_version=None,
                    content_digest=None,
                    activated_at=None,
                    window=None,
                ),
            )

    owner = PositionTargetScoreOwner(qualification=MissingReleaseRepository())  # type: ignore[arg-type]

    with pytest.raises(TargetScoreReleaseUnavailableError) as raised:
        owner.active_context(object(), exam_year=2026, now=NOW)  # type: ignore[arg-type]

    assert raised.value.code == "position_release_unavailable"
    assert raised.value.details == {
        "phase": "no_release",
        "examCode": "guokao",
        "examYear": 2026,
    }


def test_expected_release_version_conflict_is_rejected_before_score_read() -> None:
    owner = PositionTargetScoreOwner()
    context = owner.context_for_release(release=_release(), now=NOW)

    with pytest.raises(TargetScoreVersionConflictError) as raised:
        owner.ranges_for_positions(
            object(),  # type: ignore[arg-type]
            position_ids=["11111111-1111-1111-1111-111111111111"],
            context=context,
            expected_position_data_version="superseded-version",
        )

    assert raised.value.code == "position_data_version_conflict"


def test_range_contract_carries_basis_evidence_and_release_validity() -> None:
    basis = TargetScoreBasis(
        target_score=129,
        basis_level="L2",
        sample_position_count=3,
        score_sample_count=6,
        annual_scores=[],
        drift_per_year=1.2,
        latest_average_score=127,
        safety_margin=2,
    )

    target = target_score_range_from_basis(
        basis,
        generated_at=NOW,
        valid_until=WINDOW_CLOSES_AT,
    )

    assert target.lower_bound == 126.96
    assert target.recommended == 129
    assert target.upper_bound == 131.04
    assert target.sample_count == 6
    assert target.basis_level == "L2"
    assert target.evidence_level == "strong"
    assert target.generated_at == NOW
    assert target.valid_until == WINDOW_CLOSES_AT


def test_openapi_exposes_one_required_target_score_contract_on_all_v2_reads() -> None:
    schema = create_app().openapi()
    target_schema = schema["components"]["schemas"]["TargetScoreRange"]

    assert set(target_schema["required"]) == {
        "lowerBound",
        "recommended",
        "upperBound",
        "sampleCount",
        "basisLevel",
        "evidenceLevel",
        "generatedAt",
        "validUntil",
    }
    assert "/api/v2/positions/{position_id}" in schema["paths"]
    assert "/api/v2/positions/{position_id}/target-score" in schema["paths"]
