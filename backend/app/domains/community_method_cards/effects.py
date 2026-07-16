from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import math
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EffectPolicyUnavailable(RuntimeError):
    pass


class EffectPolicyRules(BaseModel):
    """Every threshold is required; this module deliberately has no product defaults."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    algorithm: Literal["wilson_composite_v1"]
    ranking_policy_version: str = Field(alias="rankingPolicyVersion", min_length=1)
    minimum_eligible_trials: int = Field(alias="minimumEligibleTrials", gt=0)
    minimum_delayed_verified_trials: int = Field(
        alias="minimumDelayedVerifiedTrials", ge=0
    )
    minimum_helpful_votes: int = Field(alias="minimumHelpfulVotes", ge=0)
    confidence_z: Decimal = Field(alias="confidenceZ", gt=0)
    correctness_weight: Decimal = Field(alias="correctnessWeight", ge=0, le=1)
    helpfulness_weight: Decimal = Field(alias="helpfulnessWeight", ge=0, le=1)
    delayed_verification_weight: Decimal = Field(
        alias="delayedVerificationWeight", ge=0, le=1
    )
    validation_threshold: Decimal = Field(alias="validationThreshold", ge=0, le=1)
    minimum_applicability_overlap: int = Field(
        alias="minimumApplicabilityOverlap", ge=0
    )
    maximum_discovery_results: int = Field(alias="maximumDiscoveryResults", gt=0)
    personalization_weight: Decimal = Field(
        alias="personalizationWeight", ge=0, le=1
    )
    delayed_verification_delay_seconds: int = Field(
        alias="delayedVerificationDelaySeconds", gt=0
    )
    require_baseline: bool = Field(alias="requireBaseline")

    @model_validator(mode="after")
    def validate_weights(self) -> "EffectPolicyRules":
        effect_sum = (
            self.correctness_weight
            + self.helpfulness_weight
            + self.delayed_verification_weight
        )
        if effect_sum != Decimal("1"):
            raise ValueError("effect weights must sum to exactly 1")
        return self


@dataclass(frozen=True)
class PublishedEffectPolicy:
    policy_id: str
    policy_version: str
    rules: EffectPolicyRules
    published_at: datetime


@dataclass(frozen=True)
class QuestionEvidence:
    independent: bool
    new_for_learner: bool
    correct_before: bool | None
    duration_before_ms: int | None
    evidence_refs: tuple[str, ...]


class QuestionEvidenceVerifier(Protocol):
    def verify(
        self,
        *,
        learner_user_id: str,
        question_attempt_id: str,
        question_version_id: str,
        card_version_id: str,
    ) -> QuestionEvidence: ...


@dataclass(frozen=True)
class CanonicalAttemptOutcome:
    """Server-owned result for an immutable attempt/version pair."""

    question_attempt_id: str
    question_version_id: str
    correct_after: bool
    duration_after_ms: int
    evidence_refs: tuple[str, ...]

    def validate_identity(self, *, attempt_id: str, version_id: str) -> None:
        if self.question_attempt_id != attempt_id or self.question_version_id != version_id:
            raise ValueError("canonical_attempt_identity_mismatch")
        if self.duration_after_ms < 0 or not self.evidence_refs:
            raise ValueError("canonical_attempt_evidence_invalid")


class CanonicalAttemptOutcomeVerifier(Protocol):
    def verify(
        self,
        *,
        learner_user_id: str,
        question_attempt_id: str,
        question_version_id: str,
    ) -> CanonicalAttemptOutcome: ...


@dataclass(frozen=True)
class ServerSessionEvidence:
    server_session_kind: Literal[
        "xingce_task_session", "essay_attempt_session", "community_trial_session"
    ]
    server_session_id: str
    session_evidence_sha256: str
    attestation_policy_version: str
    evidence_refs: tuple[str, ...]


class ServerSessionEvidenceResolver(Protocol):
    def resolve(
        self,
        *,
        learner_user_id: str,
        question_attempt_id: str,
        question_version_id: str,
    ) -> ServerSessionEvidence: ...


@dataclass(frozen=True)
class ActorClusterDecision:
    actor_cluster_hash: str
    policy_version: str
    eligible: bool
    reason_code: str | None
    snapshot: dict[str, Any]
    evidence_refs: tuple[str, ...]

    def validate(self) -> None:
        if len(self.actor_cluster_hash) != 64 or not self.policy_version.strip():
            raise ValueError("actor_cluster_decision_invalid")
        if self.eligible == (self.reason_code is not None):
            raise ValueError("actor_cluster_decision_invalid")
        if not self.snapshot or not self.evidence_refs:
            raise ValueError("actor_cluster_decision_invalid")


class ActorClusterDecisionAuthority(Protocol):
    def decide(
        self,
        *,
        author_user_id: str,
        learner_user_id: str,
        session_evidence: ServerSessionEvidence,
        decided_at: datetime,
    ) -> ActorClusterDecision: ...


class ActorClusterVerifier(Protocol):
    def eligible(
        self,
        *,
        author_user_id: str,
        learner_user_id: str,
        device_cluster_hash: str,
    ) -> bool: ...


@dataclass(frozen=True)
class TrialEligibilityDecision:
    eligible: bool
    outcome_code: str
    correct_before: bool | None
    duration_before_ms: int | None
    delayed_verification_due_at: datetime | None
    evidence_refs: tuple[str, ...]


class VersionedTrialEligibility:
    def __init__(
        self,
        *,
        question_verifier: QuestionEvidenceVerifier,
        actor_cluster_verifier: ActorClusterVerifier,
    ) -> None:
        self._questions = question_verifier
        self._clusters = actor_cluster_verifier

    def evaluate(
        self,
        *,
        policy: PublishedEffectPolicy,
        author_user_id: str,
        learner_user_id: str,
        card_version_id: str,
        question_attempt_id: str,
        question_version_id: str,
        device_cluster_hash: str,
        completed_at: datetime,
        actor_cluster_decision: ActorClusterDecision | None = None,
    ) -> TrialEligibilityDecision:
        if author_user_id == learner_user_id:
            return _excluded("author_forbidden")
        cluster_eligible = (
            actor_cluster_decision.eligible
            if actor_cluster_decision is not None
            else self._clusters.eligible(
                author_user_id=author_user_id,
                learner_user_id=learner_user_id,
                device_cluster_hash=device_cluster_hash,
            )
        )
        if not cluster_eligible:
            return _excluded("actor_cluster_excluded")
        evidence = self._questions.verify(
            learner_user_id=learner_user_id,
            question_attempt_id=question_attempt_id,
            question_version_id=question_version_id,
            card_version_id=card_version_id,
        )
        if not evidence.independent:
            return _excluded("question_not_independent", evidence.evidence_refs)
        if not evidence.new_for_learner:
            return _excluded("question_not_new", evidence.evidence_refs)
        if policy.rules.require_baseline and evidence.correct_before is None:
            return _excluded("baseline_missing", evidence.evidence_refs)
        return TrialEligibilityDecision(
            eligible=True,
            outcome_code="eligible",
            correct_before=evidence.correct_before,
            duration_before_ms=evidence.duration_before_ms,
            delayed_verification_due_at=completed_at
            + timedelta(seconds=policy.rules.delayed_verification_delay_seconds),
            evidence_refs=evidence.evidence_refs,
        )


@dataclass(frozen=True)
class TrialSignal:
    correct_before: bool | None
    correct_after: bool
    delayed_verification_outcome: str | None


@dataclass(frozen=True)
class EffectProjection:
    eligible_trial_count: int
    delayed_verified_count: int
    helpful_count: int
    not_helpful_count: int
    conservative_score: Decimal
    risk_flags: tuple[str, ...]
    evidence_cursor: str
    independent_evidence_sufficient: bool
    validated: bool


def calculate_projection(
    *,
    policy: PublishedEffectPolicy,
    trials: tuple[TrialSignal, ...],
    votes: tuple[str, ...],
    evidence_cursor: str,
) -> EffectProjection:
    if not evidence_cursor:
        raise ValueError("evidence_cursor is required")
    eligible_count = len(trials)
    baseline_trials = [item for item in trials if item.correct_before is not None]
    correctness_successes = sum(
        1 for item in baseline_trials if not item.correct_before and item.correct_after
    )
    helpful_count = sum(1 for vote in votes if vote == "helpful")
    not_helpful_count = sum(1 for vote in votes if vote == "not_helpful")
    delayed = [
        item
        for item in trials
        if item.delayed_verification_outcome in {"passed", "failed"}
    ]
    delayed_successes = sum(
        1 for item in delayed if item.delayed_verification_outcome == "passed"
    )
    z = float(policy.rules.confidence_z)
    correctness = _wilson_lower(correctness_successes, len(baseline_trials), z)
    helpfulness = _wilson_lower(helpful_count, len(votes), z)
    delayed_score = _wilson_lower(delayed_successes, len(delayed), z)
    conservative = (
        policy.rules.correctness_weight * Decimal(str(correctness))
        + policy.rules.helpfulness_weight * Decimal(str(helpfulness))
        + policy.rules.delayed_verification_weight * Decimal(str(delayed_score))
    ).quantize(Decimal("0.000001"))
    risks: list[str] = []
    if policy.rules.require_baseline and len(baseline_trials) < eligible_count:
        risks.append("baseline_incomplete")
    if len(votes) < policy.rules.minimum_helpful_votes:
        risks.append("helpful_vote_evidence_insufficient")
    if len(delayed) < policy.rules.minimum_delayed_verified_trials:
        risks.append("delayed_verification_insufficient")
    independent_sufficient = (
        eligible_count >= policy.rules.minimum_eligible_trials
        and len(votes) >= policy.rules.minimum_helpful_votes
        and len(delayed) >= policy.rules.minimum_delayed_verified_trials
        and not (policy.rules.require_baseline and len(baseline_trials) < eligible_count)
    )
    return EffectProjection(
        eligible_trial_count=eligible_count,
        delayed_verified_count=len(delayed),
        helpful_count=helpful_count,
        not_helpful_count=not_helpful_count,
        conservative_score=conservative,
        risk_flags=tuple(risks),
        evidence_cursor=evidence_cursor,
        independent_evidence_sufficient=independent_sufficient,
        validated=independent_sufficient
        and conservative >= policy.rules.validation_threshold,
    )


def personalized_rank_score(
    *,
    conservative_score: Decimal | None,
    card_applicability: tuple[str, ...],
    learner_applicability: tuple[str, ...],
    policy: PublishedEffectPolicy,
) -> Decimal | None:
    overlap = len(set(card_applicability) & set(learner_applicability))
    if overlap < policy.rules.minimum_applicability_overlap:
        return None
    evidence_score = conservative_score or Decimal("0")
    applicability_score = Decimal(overlap) / Decimal(
        max(len(set(learner_applicability)), 1)
    )
    weight = policy.rules.personalization_weight
    return (
        applicability_score * weight + evidence_score * (Decimal("1") - weight)
    ).quantize(Decimal("0.000001"))


def _wilson_lower(successes: int, total: int, z: float) -> float:
    if total <= 0:
        return 0.0
    observed = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    centre = observed + z2 / (2 * total)
    margin = z * math.sqrt((observed * (1 - observed) + z2 / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


def _excluded(
    code: str, evidence_refs: tuple[str, ...] = ()
) -> TrialEligibilityDecision:
    return TrialEligibilityDecision(
        eligible=False,
        outcome_code=code,
        correct_before=None,
        duration_before_ms=None,
        delayed_verification_due_at=None,
        evidence_refs=evidence_refs,
    )
