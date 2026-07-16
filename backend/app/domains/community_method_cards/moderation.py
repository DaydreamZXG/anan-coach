from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from app.domains.community_method_cards.schemas import (
    CardState,
    ReviewCheck,
    ReviewCheckOutcome,
    ReviewDimension,
    RightsDeclaration,
    Track,
)


REQUIRED_REVIEW_DIMENSIONS = (
    ReviewDimension.DUPLICATE,
    ReviewDimension.COPYRIGHT,
    ReviewDimension.SAFETY,
    ReviewDimension.KNOWLEDGE,
    ReviewDimension.ANSWER_CONSISTENCY,
)


class ReviewPolicyUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewSubject:
    card_id: str
    version_id: str
    author_user_id: str
    track: Track
    title: str
    recognition: str
    method_steps: tuple[str, ...]
    applicability: tuple[str, ...]
    exclusions: tuple[str, ...]
    pitfalls: tuple[str, ...]
    rights_declaration: RightsDeclaration
    source_text_sha256: str
    validation_scope: str
    knowledge_node_version_ref: str
    answer_evidence_refs: tuple[str, ...]
    validation_policy_version: str


class DimensionChecker(Protocol):
    def check(self, subject: ReviewSubject) -> ReviewCheck: ...


@dataclass(frozen=True)
class ReviewDecision:
    policy_version: str
    status: CardState
    checks: tuple[ReviewCheck, ...]


class FiveDimensionReviewEngine:
    """Runs the frozen five checks and refuses incomplete or ambiguous results."""

    def __init__(
        self,
        *,
        policy_version: str,
        checkers: Mapping[ReviewDimension, DimensionChecker],
    ) -> None:
        if not policy_version.strip():
            raise ReviewPolicyUnavailable("review_policy_unavailable")
        if set(checkers) != set(REQUIRED_REVIEW_DIMENSIONS):
            raise ReviewPolicyUnavailable("review_policy_incomplete")
        self.policy_version = policy_version
        self._checkers = dict(checkers)

    def evaluate(self, subject: ReviewSubject) -> ReviewDecision:
        checks: list[ReviewCheck] = []
        for dimension in REQUIRED_REVIEW_DIMENSIONS:
            result = self._checkers[dimension].check(subject)
            if result.dimension != dimension:
                raise ReviewPolicyUnavailable("review_dimension_mismatch")
            checks.append(result)
        if any(check.outcome == ReviewCheckOutcome.FAILED for check in checks):
            status = CardState.REJECTED
        elif any(
            check.outcome == ReviewCheckOutcome.NEEDS_REVISION for check in checks
        ):
            status = CardState.REVISION_REQUIRED
        else:
            status = CardState.TRIAL
        return ReviewDecision(
            policy_version=self.policy_version,
            status=status,
            checks=tuple(checks),
        )


@dataclass(frozen=True)
class ReportModerationDecision:
    policy_version: str
    restrict_immediately: bool
    reason_code: str


class ReportModerationPolicy(Protocol):
    @property
    def policy_version(self) -> str: ...

    def evaluate(self, *, category: str, evidence_ref: str) -> ReportModerationDecision: ...


class VersionedFixedReportPolicy:
    """Fixed-category moderation with an explicitly supplied published rule set."""

    def __init__(
        self,
        *,
        policy_version: str,
        immediate_restriction_categories: frozenset[str],
    ) -> None:
        if not policy_version.strip():
            raise ReviewPolicyUnavailable("report_policy_unavailable")
        self._policy_version = policy_version
        self._immediate = immediate_restriction_categories

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def evaluate(self, *, category: str, evidence_ref: str) -> ReportModerationDecision:
        if not evidence_ref.strip():
            raise ValueError("evidence_invalid")
        restricted = category in self._immediate
        return ReportModerationDecision(
            policy_version=self._policy_version,
            restrict_immediately=restricted,
            reason_code=(
                "immediate_restriction_required" if restricted else "manual_review_required"
            ),
        )
