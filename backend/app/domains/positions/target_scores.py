from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import sqrt

from sqlalchemy.orm import Session

from app.domains.positions.repository import PositionRepository
from app.domains.positions.schemas import TargetScoreBasis, TargetScoreRange
from app.domains.positions.service import PositionNotFoundError, PositionService
from app.domains.qualification.repository import (
    PositionRuleRelease,
    PositionRuleReleaseUnavailable,
    QualificationRepository,
)


# Target-score evidence is an annual-release projection. It never receives a
# request-relative sliding expiry: every reader of one release observes the
# same generatedAt and validUntil pair.
TARGET_SCORE_RELEASE_VALIDITY = timedelta(days=365)


class PositionTargetScoreContractError(RuntimeError):
    code = "target_score_contract_error"


class TargetScoreReleaseUnavailableError(PositionTargetScoreContractError):
    code = "position_release_unavailable"


class TargetScoreExpiredError(PositionTargetScoreContractError):
    code = "target_score_basis_expired"


class TargetScoreVersionConflictError(PositionTargetScoreContractError):
    code = "position_data_version_conflict"


class TargetScoreUnavailableError(PositionTargetScoreContractError):
    code = "target_score_unavailable"


@dataclass(frozen=True)
class TargetScoreReleaseContext:
    release: PositionRuleRelease
    generated_at: datetime
    valid_until: datetime


class PositionTargetScoreOwner:
    """Single owner for release-bound target-score ranges."""

    def __init__(
        self,
        qualification: QualificationRepository | None = None,
    ) -> None:
        self._qualification = qualification or QualificationRepository()

    def active_context(
        self,
        session: Session,
        *,
        exam_year: int | None,
        now: datetime | None = None,
    ) -> TargetScoreReleaseContext:
        try:
            release = self._qualification.active_release(
                session,
                exam_code="guokao",
                exam_year=exam_year,
            )
        except PositionRuleReleaseUnavailable as exc:
            raise TargetScoreReleaseUnavailableError(
                "No active guokao position release"
            ) from exc
        return self.context_for_release(release=release, now=now)

    def active_context_for_position(
        self,
        session: Session,
        *,
        position_id: str,
        now: datetime | None = None,
    ) -> TargetScoreReleaseContext:
        try:
            release = self._qualification.active_release_for_position(
                session,
                position_id=position_id,
                exam_code="guokao",
            )
        except LookupError as exc:
            raise PositionNotFoundError(position_id) from exc
        except PositionRuleReleaseUnavailable as exc:
            raise TargetScoreReleaseUnavailableError(
                "No active guokao position release for the requested position"
            ) from exc
        return self.context_for_release(release=release, now=now)

    def context_for_release(
        self,
        *,
        release: PositionRuleRelease,
        now: datetime | None = None,
    ) -> TargetScoreReleaseContext:
        generated_at = _as_utc(release.activated_at)
        checked_at = _as_utc(now or datetime.now(UTC))
        if generated_at > checked_at:
            raise TargetScoreVersionConflictError(
                "The active position release activation time is not yet effective"
            )
        valid_until = generated_at + TARGET_SCORE_RELEASE_VALIDITY
        if checked_at >= valid_until:
            raise TargetScoreExpiredError(
                "The active position release target-score evidence has expired"
            )
        return TargetScoreReleaseContext(
            release=release,
            generated_at=generated_at,
            valid_until=valid_until,
        )

    def ranges_for_positions(
        self,
        session: Session,
        *,
        position_ids: list[str],
        context: TargetScoreReleaseContext,
        expected_rule_set_version: str | None = None,
        expected_position_data_version: str | None = None,
    ) -> dict[str, TargetScoreRange | None]:
        ordered_ids = list(dict.fromkeys(position_ids))
        if not ordered_ids:
            return {}
        self._assert_expected_versions(
            context=context,
            expected_rule_set_version=expected_rule_set_version,
            expected_position_data_version=expected_position_data_version,
        )
        repository = PositionRepository(session)
        position_contexts = repository.get_position_contexts(position_ids=ordered_ids)
        missing = next(
            (position_id for position_id in ordered_ids if position_id not in position_contexts),
            None,
        )
        if missing is not None:
            raise PositionNotFoundError(missing)
        if any(
            int(position_contexts[position_id]["year"]) != context.release.exam_year
            for position_id in ordered_ids
        ):
            raise TargetScoreVersionConflictError(
                "The position does not belong to the active position release"
            )

        bases = PositionService(repository).target_score_bases_for_positions(
            position_ids=ordered_ids
        )
        return {
            position_id: target_score_range_from_basis(
                bases[position_id],
                generated_at=context.generated_at,
                valid_until=context.valid_until,
            )
            if bases[position_id].target_score is not None
            else None
            for position_id in ordered_ids
        }

    def range_for_position(
        self,
        session: Session,
        *,
        position_id: str,
        context: TargetScoreReleaseContext,
        expected_rule_set_version: str | None = None,
        expected_position_data_version: str | None = None,
        require_evidence: bool = False,
    ) -> TargetScoreRange | None:
        result = self.ranges_for_positions(
            session,
            position_ids=[position_id],
            context=context,
            expected_rule_set_version=expected_rule_set_version,
            expected_position_data_version=expected_position_data_version,
        )[position_id]
        if result is None and require_evidence:
            raise TargetScoreUnavailableError(
                "The position does not have enough evidence for a target score"
            )
        return result

    @staticmethod
    def _assert_expected_versions(
        *,
        context: TargetScoreReleaseContext,
        expected_rule_set_version: str | None,
        expected_position_data_version: str | None,
    ) -> None:
        if (
            expected_rule_set_version is not None
            and expected_rule_set_version != context.release.rule_set_version
        ) or (
            expected_position_data_version is not None
            and expected_position_data_version != context.release.position_data_version
        ):
            raise TargetScoreVersionConflictError(
                "The target-score request refers to a superseded position release"
            )


def target_score_range_from_basis(
    basis: TargetScoreBasis,
    *,
    generated_at: datetime,
    valid_until: datetime,
) -> TargetScoreRange:
    if basis.target_score is None or basis.score_sample_count <= 0:
        raise TargetScoreUnavailableError(
            "The position does not have enough evidence for a target score"
        )

    if basis.basis_level == "direct":
        evidence_level = "direct"
        uncertainty = max(1.0, 3.0 / sqrt(basis.score_sample_count))
    elif basis.score_sample_count >= 3 and basis.sample_position_count >= 2:
        evidence_level = "strong"
        uncertainty = max(2.0, 5.0 / sqrt(basis.score_sample_count))
    else:
        evidence_level = "limited"
        uncertainty = 5.0

    if basis.basis_level is None:
        raise TargetScoreUnavailableError(
            "The position target-score basis level is unavailable"
        )
    recommended = round(basis.target_score, 2)
    return TargetScoreRange(
        lower_bound=round(max(0.0, recommended - uncertainty), 2),
        recommended=recommended,
        upper_bound=round(min(300.0, recommended + uncertainty), 2),
        sample_count=basis.score_sample_count,
        basis_level=basis.basis_level,
        evidence_level=evidence_level,
        generated_at=_as_utc(generated_at),
        valid_until=_as_utc(valid_until),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TargetScoreVersionConflictError(
            "The position release timestamp must include a timezone"
        )
    return value.astimezone(UTC)
