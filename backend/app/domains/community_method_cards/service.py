from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from app.domains.community_method_cards.candidate_provider import (
    CandidateContext,
    CandidateProvider,
    CandidateProviderError,
)
from app.domains.community_method_cards.effects import (
    ActorClusterDecisionAuthority,
    CanonicalAttemptOutcomeVerifier,
    EffectPolicyRules,
    EffectPolicyUnavailable,
    PublishedEffectPolicy,
    ServerSessionEvidenceResolver,
    TrialSignal,
    VersionedTrialEligibility,
    calculate_projection,
    personalized_rank_score,
)
from app.domains.community_method_cards.moderation import (
    FiveDimensionReviewEngine,
    ReportModerationPolicy,
    ReviewSubject,
)
from app.domains.community_method_cards.schemas import (
    CandidateConfirm,
    CandidateStatus,
    CardState,
    CardSubmit,
    CardVersionView,
    ConfirmReceipt,
    ContributionsResponse,
    DelayedVerificationReceipt,
    DelayedVerificationRecord,
    DiscoveryCard,
    DiscoveryRequest,
    DiscoveryResponse,
    DraftCreate,
    DraftReceipt,
    ExposureCreate,
    ExposureReceipt,
    FeedbackReceipt,
    FeedbackRecord,
    ReportCreate,
    ReportReceipt,
    ReportDecisionReceipt,
    RetireCard,
    RetireReceipt,
    ReviewCheck,
    ReviewDimension,
    ReviewOutcome,
    RightsDeclaration,
    StructureCandidate,
    Track,
    TrialComplete,
    TrialReceipt,
    PublicCardDetail,
    MilestoneQualified,
    Workspace,
)


class CommunityMethodCardError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceBinding:
    binding_kind: str
    artifact_version_id: str | None
    learning_media_artifact_version_id: str | None
    reflection_version_id: str | None
    extraction_policy_version: str | None
    source_span_refs: tuple[str, ...]
    canonical_text_sha256: str


class SourceBindingAuthority(Protocol):
    """Resolves an owned immutable source version; raw artifact ids are insufficient."""

    def resolve(
        self,
        *,
        owner_user_id: str,
        source_type: str,
        artifact_version_id: str | None,
        reflection_version_id: str | None,
    ) -> SourceBinding: ...


@dataclass(frozen=True)
class ValidationBinding:
    validation_scope: str
    knowledge_node_version_ref: str
    canonical_question_id: str | None
    canonical_content_version_id: str | None
    canonical_task_version_ref: str | None
    answer_evidence_refs: tuple[str, ...]
    validation_policy_version: str
    general_method_outcome: str | None = None


class ValidationBindingAuthority(Protocol):
    def resolve(
        self,
        *,
        owner_user_id: str,
        card_id: str,
        version_id: str,
        track: Track,
    ) -> ValidationBinding: ...


class CanonicalReportEvidenceAuthority(Protocol):
    def resolve(
        self,
        *,
        reporter_user_id: str,
        card_version_id: str,
        evidence_locator: str,
    ) -> tuple[str, ...]: ...


class MilestoneSink(Protocol):
    """Runs in the card transaction so reward evaluation cannot miss a milestone."""

    def append(self, event: MilestoneQualified) -> None: ...


class CommunityMethodCardService:
    def __init__(
        self,
        repository: Any,
        *,
        candidate_provider: CandidateProvider,
        review_engine: FiveDimensionReviewEngine,
        trial_eligibility: VersionedTrialEligibility,
        report_policy: ReportModerationPolicy,
        source_binding_authority: SourceBindingAuthority,
        validation_binding_authority: ValidationBindingAuthority,
        canonical_attempt_verifier: CanonicalAttemptOutcomeVerifier,
        session_evidence_resolver: ServerSessionEvidenceResolver,
        actor_cluster_authority: ActorClusterDecisionAuthority,
        report_evidence_authority: CanonicalReportEvidenceAuthority,
        milestone_sink: MilestoneSink,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._candidate_provider = candidate_provider
        self._review_engine = review_engine
        self._trial_eligibility = trial_eligibility
        self._report_policy = report_policy
        self._source_binding_authority = source_binding_authority
        self._validation_binding_authority = validation_binding_authority
        self._canonical_attempt_verifier = canonical_attempt_verifier
        self._session_evidence_resolver = session_evidence_resolver
        self._actor_cluster_authority = actor_cluster_authority
        self._report_evidence_authority = report_evidence_authority
        self._milestone_sink = milestone_sink
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_draft(self, *, author_user_id: str, command: DraftCreate) -> DraftReceipt:
        source_hash = _sha256(command.source_text)
        source_binding = self._resolve_source_binding(
            author_user_id=author_user_id,
            command=command,
            direct_text_sha256=source_hash,
        )
        card_id = _stable_id("draft-card", author_user_id, command.idempotency_key)
        version_id = _stable_id("draft-source", card_id)
        self._repository.lock(f"draft:{author_user_id}:{command.idempotency_key}")
        try:
            existing = self._repository.card(card_id, for_update=True)
            if existing is not None:
                version = self._required_version(version_id)
                self._assert_draft_replay(existing, version, author_user_id, command, source_hash)
                candidate = self._candidate_for_version(
                    author_user_id=author_user_id,
                    card_id=card_id,
                    version=version,
                    track=Track(_get(existing, "track")),
                )
                self._repository.commit()
                return DraftReceipt(
                    cardId=card_id,
                    versionId=version_id,
                    candidateStatus=_get(candidate, "status"),
                )
            created_at = self._clock()
            self._repository.insert_draft(
                card_id=card_id,
                version_id=version_id,
                author_user_id=author_user_id,
                track=command.track.value,
                source_type=command.source_type.value,
                source_artifact_id=command.source_artifact_id,
                source_text=command.source_text,
                source_text_sha256=source_hash,
                applicability=command.applicability,
                created_at=created_at,
            )
            self._repository.insert_source_binding(
                source_binding_id=_stable_id("source-binding", version_id),
                card_id=card_id,
                version_id=version_id,
                owner_user_id=author_user_id,
                binding_kind=source_binding.binding_kind,
                source_role="primary",
                artifact_version_id=source_binding.artifact_version_id,
                learning_media_artifact_version_id=(
                    source_binding.learning_media_artifact_version_id
                ),
                reflection_version_id=source_binding.reflection_version_id,
                direct_text_sha256=(
                    source_hash if source_binding.binding_kind == "direct_text" else None
                ),
                extraction_policy_version=source_binding.extraction_policy_version,
                source_span_refs=list(source_binding.source_span_refs),
                bound_at=created_at,
            )
            version = self._required_version(version_id)
            candidate = self._candidate_for_version(
                author_user_id=author_user_id,
                card_id=card_id,
                version=version,
                track=command.track,
            )
            self._repository.commit()
            return DraftReceipt(
                cardId=card_id,
                versionId=version_id,
                candidateStatus=_get(candidate, "status"),
            )
        except Exception:
            self._repository.rollback()
            raise

    def generate_candidate(
        self, *, author_user_id: str, card_id: str
    ) -> StructureCandidate:
        self._repository.lock(f"card:{card_id}")
        try:
            card = self._owned_card(card_id, author_user_id, for_update=True)
            if _get(card, "state") not in {
                CardState.PRIVATE_DRAFT.value,
                CardState.REVISION_REQUIRED.value,
            }:
                raise CommunityMethodCardError("state_conflict")
            version = self._required_version(str(_get(card, "head_version_id")))
            candidate = self._candidate_for_version(
                author_user_id=author_user_id,
                card_id=card_id,
                version=version,
                track=Track(_get(card, "track")),
            )
            self._repository.commit()
            return _candidate_schema(candidate)
        except Exception:
            self._repository.rollback()
            raise

    def confirm_candidate(
        self, *, author_user_id: str, command: CandidateConfirm
    ) -> ConfirmReceipt:
        self._repository.lock(f"card:{command.card_id}")
        version_id = _stable_id("confirmed-version", command.candidate_id)
        try:
            card = self._owned_card(command.card_id, author_user_id, for_update=True)
            replay = self._repository.confirmed_version_by_id(version_id)
            if replay is not None:
                self._assert_confirmation_replay(replay, command)
                self._repository.commit()
                return _confirm_receipt(replay)
            if _get(card, "state") not in {
                CardState.PRIVATE_DRAFT.value,
                CardState.REVISION_REQUIRED.value,
            }:
                raise CommunityMethodCardError("state_conflict")
            if str(_get(card, "head_version_id")) != command.base_version_id:
                raise CommunityMethodCardError("candidate_stale")
            candidate = self._repository.candidate(command.candidate_id)
            if candidate is None or str(_get(candidate, "author_user_id")) != author_user_id:
                raise CommunityMethodCardError("candidate_not_owned")
            if str(_get(candidate, "card_id")) != command.card_id:
                raise CommunityMethodCardError("candidate_not_owned")
            if str(_get(candidate, "source_version_id")) != command.base_version_id:
                raise CommunityMethodCardError("candidate_stale")
            if _get(candidate, "status") != CandidateStatus.READY.value:
                raise CommunityMethodCardError("candidate_unavailable")
            base = self._required_version(command.base_version_id)
            confirmed_at = self._clock()
            version = self._repository.insert_confirmed_version(
                version_id=version_id,
                card_id=command.card_id,
                version_no=int(_get(base, "version_no")) + 1,
                author_user_id=author_user_id,
                base_version_id=command.base_version_id,
                source_type=_get(base, "source_type"),
                source_artifact_id=_optional_str(_get(base, "source_artifact_id")),
                source_text_sha256=_get(base, "source_text_sha256"),
                private_source_text=_get(base, "private_source_text"),
                title=command.title,
                recognition=command.recognition,
                method_steps=command.method_steps,
                applicability=command.applicability,
                exclusions=command.exclusions,
                pitfalls=command.pitfalls,
                rights_declaration=command.rights_declaration.value,
                confirmed_at=confirmed_at,
            )
            self._repository.copy_source_binding(
                source_version_id=command.base_version_id,
                target_version_id=version_id,
                target_card_id=command.card_id,
                target_owner_user_id=author_user_id,
                source_binding_id=_stable_id("source-binding", version_id),
                bound_at=confirmed_at,
            )
            moved = self._repository.transition_card(
                card_id=command.card_id,
                expected_state_version=int(_get(card, "state_version")),
                expected_head_version_id=command.base_version_id,
                state=CardState.PRIVATE_DRAFT.value,
                head_version_id=version_id,
                updated_at=confirmed_at,
            )
            if moved is None:
                raise CommunityMethodCardError("candidate_stale")
            self._repository.commit()
            return _confirm_receipt(version)
        except Exception:
            self._repository.rollback()
            raise

    def submit_for_review(
        self, *, author_user_id: str, command: CardSubmit
    ) -> ReviewOutcome:
        self._repository.lock(f"card:{command.card_id}")
        try:
            card = self._owned_card(command.card_id, author_user_id, for_update=True)
            existing = self._repository.review_by_version(command.version_id)
            if existing is not None:
                if _get(existing, "request_key") != command.idempotency_key:
                    raise CommunityMethodCardError("version_already_reviewed")
                result = self._review_outcome(existing)
                self._repository.commit()
                return result
            if _get(card, "state") != CardState.PRIVATE_DRAFT.value:
                raise CommunityMethodCardError("state_conflict")
            if str(_get(card, "head_version_id")) != command.version_id:
                raise CommunityMethodCardError("version_not_confirmed")
            version = self._required_version(command.version_id)
            if _get(version, "author_confirmed_at") is None:
                raise CommunityMethodCardError("version_not_confirmed")
            if _get(version, "rights_declaration") == RightsDeclaration.PRIVATE_THIRD_PARTY.value:
                raise CommunityMethodCardError("rights_prohibit_publication")
            if self._repository.source_binding(command.version_id) is None:
                raise CommunityMethodCardError("source_binding_missing")
            policy_resource = self._published_policy_resource("automated_review")
            if self._review_engine.policy_version != _get(
                policy_resource, "policy_version"
            ):
                raise CommunityMethodCardError("review_policy_mismatch")
            validation = self._ensure_validation_binding(
                card=card, version=version, bound_at=self._clock()
            )
            subject = _review_subject(card, version, validation)
            decision = self._review_engine.evaluate(subject)
            created_at = self._clock()
            review_id = _stable_id("review", command.version_id)
            fingerprint = _fingerprint(
                {
                    "versionId": command.version_id,
                    "policyVersion": decision.policy_version,
                }
            )
            review = self._repository.insert_review(
                review_id=review_id,
                card_id=command.card_id,
                version_id=command.version_id,
                policy_version=decision.policy_version,
                status=decision.status.value,
                request_key=command.idempotency_key,
                request_fingerprint=fingerprint,
                checks=[
                    {
                        "dimension": item.dimension.value,
                        "outcome": item.outcome.value,
                        "reason_code": item.reason_code,
                        "evidence_refs": item.evidence_refs,
                    }
                    for item in decision.checks
                ],
                created_at=created_at,
            )
            moved = self._repository.transition_card(
                card_id=command.card_id,
                expected_state_version=int(_get(card, "state_version")),
                expected_head_version_id=command.version_id,
                state=decision.status.value,
                head_version_id=command.version_id,
                updated_at=created_at,
            )
            if moved is None:
                raise CommunityMethodCardError("state_conflict")
            if decision.status == CardState.TRIAL:
                self._append_milestone(
                    card=card,
                    version_id=command.version_id,
                    milestone="automated_review_passed",
                    evidence_refs=[f"community-review:{review_id}"],
                    occurred_at=created_at,
                )
            self._repository.commit()
            return self._review_outcome(review, checks=list(decision.checks))
        except Exception:
            self._repository.rollback()
            raise

    def record_exposure(
        self, *, learner_user_id: str, command: ExposureCreate
    ) -> ExposureReceipt:
        self._repository.lock(
            f"exposure:{command.card_version_id}:{learner_user_id}"
        )
        try:
            version = self._required_version(command.card_version_id)
            card = self._repository.card(str(_get(version, "card_id")), for_update=True)
            if card is None:
                raise CommunityMethodCardError("not_found")
            if str(_get(card, "author_user_id")) == learner_user_id:
                raise CommunityMethodCardError("author_forbidden")
            if str(_get(card, "head_version_id")) != command.card_version_id:
                raise CommunityMethodCardError("version_expired")
            if _get(card, "state") not in {
                CardState.TRIAL.value,
                CardState.COMMUNITY_VALIDATED.value,
            }:
                raise CommunityMethodCardError("state_conflict")
            policy = self._published_effect_policy()
            if command.ranking_policy_version != policy.rules.ranking_policy_version:
                raise CommunityMethodCardError("ranking_policy_expired")
            score = personalized_rank_score(
                conservative_score=self._projection_score(
                    command.card_version_id, policy.policy_id
                ),
                card_applicability=tuple(_get(version, "applicability")),
                learner_applicability=tuple(command.applicability_snapshot),
                policy=policy,
            )
            if score is None:
                raise CommunityMethodCardError("applicability_mismatch")
            existing = self._repository.exposure_for_learner(
                version_id=command.card_version_id,
                learner_user_id=learner_user_id,
            )
            if existing is not None:
                self._repository.commit()
                return _exposure_receipt(existing)
            opted_in_at = self._clock()
            exposure_id = _stable_id(
                "exposure", command.card_version_id, learner_user_id
            )
            exposure = self._repository.insert_exposure(
                exposure_id=exposure_id,
                card_id=str(_get(version, "card_id")),
                version_id=command.card_version_id,
                learner_user_id=learner_user_id,
                applicability_snapshot=command.applicability_snapshot,
                ranking_policy_version=policy.rules.ranking_policy_version,
                opted_in_at=opted_in_at,
            )
            self._repository.commit()
            return _exposure_receipt(exposure)
        except Exception:
            self._repository.rollback()
            raise

    def complete_trial(
        self, *, learner_user_id: str, command: TrialComplete
    ) -> TrialReceipt:
        self._repository.lock(f"trial:{command.exposure_id}")
        try:
            exposure = self._repository.exposure(command.exposure_id)
            if exposure is None:
                raise CommunityMethodCardError("exposure_missing")
            if str(_get(exposure, "learner_user_id")) != learner_user_id:
                raise CommunityMethodCardError("exposure_missing")
            if str(_get(exposure, "author_user_id")) == learner_user_id:
                raise CommunityMethodCardError("author_forbidden")
            if str(_get(exposure, "head_version_id")) != str(
                _get(exposure, "version_id")
            ):
                raise CommunityMethodCardError("version_expired")
            if _get(exposure, "state") not in {
                CardState.TRIAL.value,
                CardState.COMMUNITY_VALIDATED.value,
            }:
                raise CommunityMethodCardError("state_conflict")
            existing = self._repository.trial_by_attempt(
                version_id=str(_get(exposure, "version_id")),
                learner_user_id=learner_user_id,
                question_attempt_id=command.question_attempt_id,
            )
            if existing is not None:
                self._assert_trial_replay(existing, command)
                self._repository.commit()
                return _trial_receipt(existing)
            completed_at = self._clock()
            policy = self._published_effect_policy()
            outcome = self._canonical_attempt_verifier.verify(
                learner_user_id=learner_user_id,
                question_attempt_id=command.question_attempt_id,
                question_version_id=command.question_version_id,
            )
            try:
                outcome.validate_identity(
                    attempt_id=command.question_attempt_id,
                    version_id=command.question_version_id,
                )
            except ValueError as exc:
                raise CommunityMethodCardError("canonical_attempt_invalid") from exc
            session_evidence = self._session_evidence_resolver.resolve(
                learner_user_id=learner_user_id,
                question_attempt_id=command.question_attempt_id,
                question_version_id=command.question_version_id,
            )
            if (
                len(session_evidence.session_evidence_sha256) != 64
                or not session_evidence.evidence_refs
            ):
                raise CommunityMethodCardError("session_evidence_invalid")
            cluster = self._actor_cluster_authority.decide(
                author_user_id=str(_get(exposure, "author_user_id")),
                learner_user_id=learner_user_id,
                session_evidence=session_evidence,
                decided_at=completed_at,
            )
            try:
                cluster.validate()
            except ValueError as exc:
                raise CommunityMethodCardError("actor_cluster_decision_invalid") from exc
            eligibility = self._trial_eligibility.evaluate(
                policy=policy,
                author_user_id=str(_get(exposure, "author_user_id")),
                learner_user_id=learner_user_id,
                card_version_id=str(_get(exposure, "version_id")),
                question_attempt_id=command.question_attempt_id,
                question_version_id=command.question_version_id,
                device_cluster_hash=cluster.actor_cluster_hash,
                completed_at=completed_at,
                actor_cluster_decision=cluster,
            )
            trial_id = _stable_id(
                "trial",
                str(_get(exposure, "version_id")),
                learner_user_id,
                command.question_attempt_id,
            )
            trial = self._repository.insert_trial(
                values={
                    "trial_id": trial_id,
                    "exposure_id": command.exposure_id,
                    "card_id": str(_get(exposure, "card_id")),
                    "version_id": str(_get(exposure, "version_id")),
                    "learner_user_id": learner_user_id,
                    "question_attempt_id": command.question_attempt_id,
                    "question_version_id": command.question_version_id,
                    "correct_before": eligibility.correct_before,
                    "correct_after": outcome.correct_after,
                    "duration_before_ms": eligibility.duration_before_ms,
                    "duration_after_ms": outcome.duration_after_ms,
                    "eligibility_status": "eligible" if eligibility.eligible else "excluded",
                    "exclusion_reason": None if eligibility.eligible else eligibility.outcome_code,
                    "device_cluster_hash": cluster.actor_cluster_hash,
                    "delayed_verification_due_at": eligibility.delayed_verification_due_at,
                    "completed_at": completed_at,
                }
            )
            session_evidence_id = _stable_id("trial-session-evidence", trial_id)
            self._repository.insert_trial_session_evidence(
                session_evidence_id=session_evidence_id,
                trial_id=trial_id,
                card_id=str(_get(exposure, "card_id")),
                version_id=str(_get(exposure, "version_id")),
                learner_user_id=learner_user_id,
                server_session_kind=session_evidence.server_session_kind,
                server_session_id=session_evidence.server_session_id,
                session_evidence_sha256=session_evidence.session_evidence_sha256,
                attestation_policy_version=session_evidence.attestation_policy_version,
                evidence_refs=list(session_evidence.evidence_refs),
                recorded_at=completed_at,
            )
            decision_refs = list(
                dict.fromkeys(
                    (
                        *session_evidence.evidence_refs,
                        *cluster.evidence_refs,
                        *eligibility.evidence_refs,
                    )
                )
            )
            self._repository.insert_eligibility_decision(
                eligibility_decision_id=_stable_id("eligibility-decision", trial_id),
                session_evidence_id=session_evidence_id,
                trial_id=trial_id,
                card_id=str(_get(exposure, "card_id")),
                version_id=str(_get(exposure, "version_id")),
                learner_user_id=learner_user_id,
                actor_cluster_hash=cluster.actor_cluster_hash,
                cluster_policy_version=cluster.policy_version,
                decision="eligible" if eligibility.eligible else "excluded",
                reason_code=None if eligibility.eligible else eligibility.outcome_code,
                decision_snapshot={
                    "actorCluster": cluster.snapshot,
                    "canonicalAttemptEvidenceRefs": list(outcome.evidence_refs),
                    "questionVersionId": command.question_version_id,
                },
                evidence_refs=decision_refs or list(outcome.evidence_refs),
                decided_at=completed_at,
            )
            if eligibility.eligible:
                self._refresh_projection(
                    card_id=str(_get(exposure, "card_id")),
                    version_id=str(_get(exposure, "version_id")),
                    policy=policy,
                    now=completed_at,
                )
            self._repository.commit()
            return _trial_receipt(trial)
        except Exception:
            self._repository.rollback()
            raise

    def record_feedback(
        self, *, learner_user_id: str, command: FeedbackRecord
    ) -> FeedbackReceipt:
        self._repository.lock(f"feedback:{command.trial_id}")
        try:
            trial = self._repository.trial(command.trial_id)
            if trial is None or str(_get(trial, "learner_user_id")) != learner_user_id:
                raise CommunityMethodCardError("trial_not_eligible")
            if str(_get(trial, "author_user_id")) == learner_user_id:
                raise CommunityMethodCardError("author_forbidden")
            if _get(trial, "eligibility_status") != "eligible":
                raise CommunityMethodCardError("trial_not_eligible")
            existing = self._repository.vote_for_version(
                version_id=str(_get(trial, "version_id")),
                voter_user_id=learner_user_id,
            )
            if existing is not None:
                if (
                    _get(existing, "vote") != command.vote
                    or _get(existing, "reason_code") != command.reason_code
                    or str(_get(existing, "trial_id")) != command.trial_id
                ):
                    raise CommunityMethodCardError("duplicate_vote")
                self._repository.commit()
                return FeedbackReceipt(voteId=str(_get(existing, "vote_id")), recorded=True)
            created_at = self._clock()
            vote_id = _stable_id(
                "vote", str(_get(trial, "version_id")), learner_user_id
            )
            vote = self._repository.insert_vote(
                vote_id=vote_id,
                trial_id=command.trial_id,
                card_id=str(_get(trial, "card_id")),
                version_id=str(_get(trial, "version_id")),
                voter_user_id=learner_user_id,
                vote=command.vote,
                reason_code=command.reason_code,
                created_at=created_at,
            )
            policy = self._published_effect_policy()
            self._refresh_projection(
                card_id=str(_get(trial, "card_id")),
                version_id=str(_get(trial, "version_id")),
                policy=policy,
                now=created_at,
            )
            self._repository.commit()
            return FeedbackReceipt(voteId=str(_get(vote, "vote_id")), recorded=True)
        except Exception:
            self._repository.rollback()
            raise

    def record_delayed_verification(
        self, *, learner_user_id: str, command: DelayedVerificationRecord
    ) -> DelayedVerificationReceipt:
        self._repository.lock(f"delayed-verification:{command.trial_id}")
        try:
            trial = self._repository.trial(command.trial_id)
            if trial is None or str(_get(trial, "learner_user_id")) != learner_user_id:
                raise CommunityMethodCardError("trial_not_eligible")
            if _get(trial, "eligibility_status") != "eligible":
                raise CommunityMethodCardError("trial_not_eligible")
            due_at = _get(trial, "delayed_verification_due_at")
            completed_at = self._clock()
            if due_at is None or completed_at < due_at:
                raise CommunityMethodCardError("delayed_verification_not_due")
            existing = self._repository.delayed_verification(command.trial_id)
            if existing is not None:
                self._assert_delayed_replay(existing, command)
                self._repository.commit()
                return DelayedVerificationReceipt(
                    verificationId=str(_get(existing, "verification_id")), recorded=True
                )
            if command.question_attempt_id == _get(trial, "question_attempt_id"):
                raise CommunityMethodCardError("delayed_attempt_not_independent")
            outcome = self._canonical_attempt_verifier.verify(
                learner_user_id=learner_user_id,
                question_attempt_id=command.question_attempt_id,
                question_version_id=command.question_version_id,
            )
            try:
                outcome.validate_identity(
                    attempt_id=command.question_attempt_id,
                    version_id=command.question_version_id,
                )
            except ValueError as exc:
                raise CommunityMethodCardError("canonical_attempt_invalid") from exc
            verification_id = _stable_id("delayed-verification", command.trial_id)
            verification = self._repository.insert_delayed_verification(
                verification_id=verification_id,
                trial_id=command.trial_id,
                question_attempt_id=command.question_attempt_id,
                question_version_id=command.question_version_id,
                correct_after=outcome.correct_after,
                duration_after_ms=outcome.duration_after_ms,
                outcome="passed" if outcome.correct_after else "failed",
                evidence_refs=list(outcome.evidence_refs),
                completed_at=completed_at,
            )
            policy = self._published_effect_policy()
            self._refresh_projection(
                card_id=str(_get(trial, "card_id")),
                version_id=str(_get(trial, "version_id")),
                policy=policy,
                now=completed_at,
            )
            self._repository.commit()
            return DelayedVerificationReceipt(
                verificationId=str(_get(verification, "verification_id")), recorded=True
            )
        except Exception:
            self._repository.rollback()
            raise

    def report(
        self, *, reporter_user_id: str, command: ReportCreate
    ) -> ReportReceipt:
        self._repository.lock(f"report:{command.card_version_id}")
        try:
            version = self._required_version(command.card_version_id)
            card_id = str(_get(version, "card_id"))
            card = self._repository.card(card_id, for_update=True)
            if card is None:
                raise CommunityMethodCardError("not_found")
            if str(_get(card, "author_user_id")) == reporter_user_id:
                raise CommunityMethodCardError("author_forbidden")
            if _get(card, "state") not in {
                CardState.TRIAL.value,
                CardState.COMMUNITY_VALIDATED.value,
                CardState.RESTRICTED.value,
            }:
                raise CommunityMethodCardError("state_conflict")
            evidence_refs = self._report_evidence_authority.resolve(
                reporter_user_id=reporter_user_id,
                card_version_id=command.card_version_id,
                evidence_locator=command.evidence_ref,
            )
            if not evidence_refs:
                raise CommunityMethodCardError("report_evidence_invalid")
            evidence_hash = _fingerprint(
                {"locator": command.evidence_ref, "evidenceRefs": evidence_refs}
            )
            existing = self._repository.report_by_evidence(
                version_id=command.card_version_id,
                reporter_user_id=reporter_user_id,
                evidence_sha256=evidence_hash,
            )
            if existing is not None:
                self._repository.commit()
                return ReportReceipt(
                    reportId=str(_get(existing, "report_id")),
                    cardStatus=CardState(_get(card, "state")),
                    decision="pending",
                )
            created_at = self._clock()
            report_id = _stable_id(
                "report", command.card_version_id, reporter_user_id, evidence_hash
            )
            report = self._repository.insert_report(
                values={
                    "report_id": report_id,
                    "card_id": card_id,
                    "version_id": command.card_version_id,
                    "reporter_user_id": reporter_user_id,
                    "category": command.category,
                    "evidence_ref": command.evidence_ref,
                    "evidence_sha256": evidence_hash,
                    "decision": "pending",
                    "created_at": created_at,
                }
            )
            self._repository.commit()
            return ReportReceipt(
                reportId=str(_get(report, "report_id")),
                cardStatus=CardState(_get(card, "state")),
                decision="pending",
            )
        except Exception:
            self._repository.rollback()
            raise

    def decide_report(
        self, *, report_id: str, actor_ref: str
    ) -> ReportDecisionReceipt:
        """Apply an independent, published decision after report intake commits pending."""
        self._repository.lock(f"report-decision:{report_id}")
        try:
            report = self._repository.report(report_id, for_update=True)
            if report is None:
                raise CommunityMethodCardError("report_not_found")
            existing = self._repository.latest_report_decision(report_id)
            card = self._repository.card(str(_get(report, "card_id")), for_update=True)
            if card is None:
                raise CommunityMethodCardError("not_found")
            policy_resource = self._published_policy_resource("report_decision")
            if self._report_policy.policy_version != _get(
                policy_resource, "policy_version"
            ):
                raise CommunityMethodCardError("report_policy_mismatch")
            if existing is not None:
                self._repository.commit()
                return _report_decision_receipt(
                    existing, card=card, policy_version=_get(policy_resource, "policy_version")
                )
            moderation = self._report_policy.evaluate(
                category=str(_get(report, "category")),
                evidence_ref=str(_get(report, "evidence_ref")),
            )
            canonical_evidence_refs = self._report_evidence_authority.resolve(
                reporter_user_id=str(_get(report, "reporter_user_id")),
                card_version_id=str(_get(report, "version_id")),
                evidence_locator=str(_get(report, "evidence_ref")),
            )
            if not canonical_evidence_refs:
                raise CommunityMethodCardError("report_evidence_invalid")
            decided_at = self._clock()
            decision = "confirmed" if moderation.restrict_immediately else "rejected"
            reporter_user_id = str(_get(report, "reporter_user_id"))
            decision_id = _stable_id("report-decision", report_id, "1")
            row = self._repository.insert_report_decision(
                report_decision_id=decision_id,
                report_id=report_id,
                card_id=str(_get(report, "card_id")),
                version_id=str(_get(report, "version_id")),
                reporter_user_id=reporter_user_id,
                decision_version=1,
                supersedes_report_decision_id=None,
                decision=decision,
                reason_code=moderation.reason_code,
                policy_resource_id=str(_get(policy_resource, "policy_resource_id")),
                reporter_is_beneficiary=decision == "confirmed",
                beneficiary_user_id=(reporter_user_id if decision == "confirmed" else None),
                evidence_refs=[*canonical_evidence_refs, f"community-report:{report_id}"],
                actor_ref=actor_ref,
                decided_at=decided_at,
            )
            if decision == "confirmed" and _get(card, "state") in {
                CardState.TRIAL.value,
                CardState.COMMUNITY_VALIDATED.value,
            }:
                moved = self._repository.transition_card(
                    card_id=str(_get(card, "card_id")),
                    expected_state_version=int(_get(card, "state_version")),
                    expected_head_version_id=str(_get(card, "head_version_id")),
                    state=CardState.RESTRICTED.value,
                    head_version_id=str(_get(card, "head_version_id")),
                    updated_at=decided_at,
                )
                if moved is None:
                    raise CommunityMethodCardError("state_conflict")
                card = moved
                self._append_milestone(
                    card=card,
                    version_id=str(_get(report, "version_id")),
                    milestone="report_confirmed",
                    evidence_refs=[f"community-report-decision:{decision_id}"],
                    occurred_at=decided_at,
                    beneficiary_user_id=reporter_user_id,
                )
            self._repository.commit()
            return _report_decision_receipt(
                row, card=card, policy_version=_get(policy_resource, "policy_version")
            )
        except Exception:
            self._repository.rollback()
            raise

    def retire(
        self, *, actor_user_id: str, command: RetireCard, moderator: bool = False
    ) -> RetireReceipt:
        self._repository.lock(f"card:{command.card_id}")
        try:
            card = self._repository.card(command.card_id, for_update=True)
            if card is None:
                raise CommunityMethodCardError("not_found")
            if not moderator and str(_get(card, "author_user_id")) != actor_user_id:
                raise CommunityMethodCardError("not_owner")
            if str(_get(card, "head_version_id")) != command.head_version_id:
                raise CommunityMethodCardError("head_conflict")
            if _get(card, "state") == CardState.RETIRED.value:
                self._repository.commit()
                return RetireReceipt(cardId=command.card_id)
            if _get(card, "state") not in {
                CardState.TRIAL.value,
                CardState.COMMUNITY_VALIDATED.value,
                CardState.RESTRICTED.value,
            }:
                raise CommunityMethodCardError("state_conflict")
            moved = self._repository.transition_card(
                card_id=command.card_id,
                expected_state_version=int(_get(card, "state_version")),
                expected_head_version_id=command.head_version_id,
                state=CardState.RETIRED.value,
                head_version_id=command.head_version_id,
                updated_at=self._clock(),
            )
            if moved is None:
                raise CommunityMethodCardError("head_conflict")
            self._repository.commit()
            return RetireReceipt(cardId=command.card_id)
        except Exception:
            self._repository.rollback()
            raise

    def workspace(self, *, author_user_id: str, card_id: str) -> Workspace:
        card = self._owned_card(card_id, author_user_id)
        rows = self._repository.versions(card_id)
        head_id = str(_get(card, "head_version_id"))
        candidate = self._latest_candidate(card_id)
        review = self._repository.review_by_version(head_id)
        return Workspace(
            cardId=card_id,
            status=_get(card, "state"),
            headVersionId=head_id,
            versionIds=[str(_get(row, "version_id")) for row in rows],
            candidateStatus=(
                _get(candidate, "status") if candidate is not None else CandidateStatus.PROCESSING
            ),
            reviewStatus=_get(review, "status") if review is not None else "not_submitted",
            versions=[_version_view(row) for row in rows],
        )

    def public_detail(self, *, card_version_id: str) -> PublicCardDetail:
        """Return the allow-listed public projection; private provenance never enters it."""
        policy = self._published_effect_policy()
        row = self._repository.public_detail(
            version_id=card_version_id, policy_id=policy.policy_id
        )
        if row is None or _get(row, "state") not in {
            CardState.TRIAL.value,
            CardState.COMMUNITY_VALIDATED.value,
        }:
            raise CommunityMethodCardError("not_found")
        if str(_get(row, "head_version_id")) != card_version_id:
            raise CommunityMethodCardError("not_found")
        return PublicCardDetail(
            cardVersionId=card_version_id,
            title=_get(row, "title"),
            recognition=_get(row, "recognition"),
            methodSteps=list(_get(row, "method_steps")),
            applicability=list(_get(row, "applicability")),
            exclusions=list(_get(row, "exclusions")),
            pitfalls=list(_get(row, "pitfalls")),
            status=_get(row, "state"),
            evidenceCount=int(_get(row, "eligible_trial_count") or 0),
            delayedVerifiedCount=int(_get(row, "delayed_verified_count") or 0),
            conservativeScore=_optional_decimal(_get(row, "conservative_score")),
            riskFlags=list(_get(row, "risk_flags") or []),
            policyVersion=policy.policy_version,
        )

    def discover(
        self, *, learner_user_id: str, query: DiscoveryRequest
    ) -> DiscoveryResponse:
        if query.cursor is not None:
            raise CommunityMethodCardError("cursor_expired")
        policy = self._published_effect_policy()
        ranked: list[tuple[Decimal, DiscoveryCard]] = []
        for row in self._repository.discovery_rows(
            track=query.track.value, learner_user_id=learner_user_id
        ):
            score = personalized_rank_score(
                conservative_score=_optional_decimal(_get(row, "conservative_score")),
                card_applicability=tuple(_get(row, "applicability")),
                learner_applicability=tuple(query.applicability),
                policy=policy,
            )
            if score is None:
                continue
            ranked.append(
                (
                    score,
                    DiscoveryCard(
                        cardVersionId=str(_get(row, "version_id")),
                        title=_get(row, "title"),
                        recognition=_get(row, "recognition"),
                        methodSteps=list(_get(row, "method_steps")),
                        applicability=list(_get(row, "applicability")),
                        exclusions=list(_get(row, "exclusions")),
                        pitfalls=list(_get(row, "pitfalls")),
                        conservativeScore=_optional_decimal(
                            _get(row, "conservative_score")
                        ),
                        evidenceCount=int(_get(row, "eligible_trial_count") or 0),
                        riskFlags=list(_get(row, "risk_flags") or []),
                        evidencePolicyVersion=policy.policy_version,
                        isAuthor=False,
                    ),
                )
            )
        ranked.sort(key=lambda item: (item[0], item[1].card_version_id), reverse=True)
        cards = [
            card for _, card in ranked[: policy.rules.maximum_discovery_results]
        ]
        return DiscoveryResponse(
            cardVersionIds=[card.card_version_id for card in cards],
            rankingPolicyVersion=policy.rules.ranking_policy_version,
            cards=cards,
        )

    def contributions(self, *, author_user_id: str) -> ContributionsResponse:
        return ContributionsResponse(
            cardIds=self._repository.authored_card_ids(author_user_id),
            rewardEntryIds=self._repository.reward_entry_ids(author_user_id),
        )

    def _candidate_for_version(
        self,
        *,
        author_user_id: str,
        card_id: str,
        version: Mapping[str, Any],
        track: Track,
    ) -> Mapping[str, Any]:
        policy_resource = self._published_policy_resource("candidate_generation")
        if (
            self._candidate_provider.prompt_version
            != _get(policy_resource, "prompt_version")
            or self._candidate_provider.model_policy_version
            != _get(policy_resource, "policy_version")
        ):
            raise CommunityMethodCardError("candidate_policy_mismatch")
        fingerprint = _fingerprint(
            {
                "authorId": author_user_id,
                "sourceVersionId": str(_get(version, "version_id")),
                "sourceHash": _get(version, "source_text_sha256"),
                "promptVersion": self._candidate_provider.prompt_version,
                "modelPolicyVersion": self._candidate_provider.model_policy_version,
                "policyResourceId": str(_get(policy_resource, "policy_resource_id")),
            }
        )
        existing = self._repository.candidate_by_fingerprint(
            author_user_id=author_user_id, input_fingerprint=fingerprint
        )
        if existing is not None:
            return existing
        created_at = self._clock()
        payload: dict[str, Any] | None = None
        source_refs: list[str] = []
        failure_code: str | None = None
        try:
            content = self._candidate_provider.generate(
                CandidateContext(
                    card_id=card_id,
                    source_version_id=str(_get(version, "version_id")),
                    source_text=_get(version, "private_source_text"),
                    source_hash=_get(version, "source_text_sha256"),
                    track=track,
                    applicability=tuple(_get(version, "applicability")),
                )
            )
            content.validate()
            payload = {
                "title": content.title,
                "recognition": content.recognition,
                "methodSteps": list(content.method_steps),
            }
            source_refs = list(content.source_span_refs)
            status = CandidateStatus.READY.value
        except CandidateProviderError as exc:
            status = CandidateStatus.FAILED.value
            failure_code = exc.code
        candidate_id = _stable_id("candidate", author_user_id, fingerprint)
        return self._repository.insert_candidate(
            candidate_id=candidate_id,
            card_id=card_id,
            source_version_id=str(_get(version, "version_id")),
            author_user_id=author_user_id,
            prompt_version=self._candidate_provider.prompt_version,
            model_policy_version=self._candidate_provider.model_policy_version,
            status=status,
            candidate_payload=payload,
            source_span_refs=source_refs,
            input_fingerprint=fingerprint,
            failure_code=failure_code,
            created_at=created_at,
            completed_at=self._clock(),
        )

    def _owned_card(
        self, card_id: str, author_user_id: str, *, for_update: bool = False
    ) -> Mapping[str, Any]:
        card = self._repository.card(card_id, for_update=for_update)
        if card is None:
            raise CommunityMethodCardError("not_found")
        if str(_get(card, "author_user_id")) != author_user_id:
            raise CommunityMethodCardError("not_owner")
        return card

    def _required_version(self, version_id: str) -> Mapping[str, Any]:
        version = self._repository.version(version_id)
        if version is None:
            raise CommunityMethodCardError("not_found")
        return version

    def _resolve_source_binding(
        self,
        *,
        author_user_id: str,
        command: DraftCreate,
        direct_text_sha256: str,
    ) -> SourceBinding:
        if command.source_type.value == "text":
            return SourceBinding(
                binding_kind="direct_text",
                artifact_version_id=None,
                learning_media_artifact_version_id=None,
                reflection_version_id=None,
                extraction_policy_version=None,
                source_span_refs=(f"direct-text:{direct_text_sha256}",),
                canonical_text_sha256=direct_text_sha256,
            )
        binding = self._source_binding_authority.resolve(
            owner_user_id=author_user_id,
            source_type=command.source_type.value,
            artifact_version_id=command.source_artifact_id,
            reflection_version_id=command.reflection_version_id,
        )
        expected_kind = {
            "reflection": "xingce_reflection_version",
            "voice_transcript": "learning_media_reflection_version",
            "handwriting_extraction": "source_artifact_version",
        }[command.source_type.value]
        expected_artifact_version_id = (
            command.source_artifact_id
            if expected_kind == "source_artifact_version"
            else None
        )
        expected_media_artifact_version_id = (
            command.source_artifact_id
            if expected_kind == "learning_media_reflection_version"
            else None
        )
        if (
            binding.binding_kind != expected_kind
            or binding.artifact_version_id != expected_artifact_version_id
            or binding.learning_media_artifact_version_id
            != expected_media_artifact_version_id
            or binding.reflection_version_id != command.reflection_version_id
            or not binding.source_span_refs
            or binding.canonical_text_sha256 != direct_text_sha256
            or (
                expected_kind == "source_artifact_version"
                and not binding.extraction_policy_version
            )
            or (
                expected_kind != "source_artifact_version"
                and binding.extraction_policy_version is not None
            )
        ):
            raise CommunityMethodCardError("source_binding_invalid")
        return binding

    def _published_effect_policy(self) -> PublishedEffectPolicy:
        row = self._repository.published_effect_policy()
        if row is None:
            raise EffectPolicyUnavailable("policy_unavailable")
        try:
            rules = EffectPolicyRules.model_validate(_get(row, "rules"))
        except Exception as exc:
            raise EffectPolicyUnavailable("policy_invalid") from exc
        published_at = _get(row, "published_at")
        if published_at is None:
            raise EffectPolicyUnavailable("policy_invalid")
        return PublishedEffectPolicy(
            policy_id=str(_get(row, "policy_id")),
            policy_version=_get(row, "policy_version"),
            rules=rules,
            published_at=published_at,
        )

    def _published_policy_resource(self, operation: str) -> Mapping[str, Any]:
        row = self._repository.published_policy_resource(operation)
        if row is None:
            raise CommunityMethodCardError(f"{operation}_policy_unavailable")
        rules = _get(row, "rules")
        if not isinstance(rules, Mapping) or not rules:
            raise CommunityMethodCardError(f"{operation}_policy_invalid")
        if _fingerprint(rules) != _get(row, "rules_sha256"):
            raise CommunityMethodCardError(f"{operation}_policy_invalid")
        return row

    def _ensure_validation_binding(
        self,
        *,
        card: Mapping[str, Any],
        version: Mapping[str, Any],
        bound_at: datetime,
    ) -> Mapping[str, Any]:
        version_id = str(_get(version, "version_id"))
        existing = self._repository.validation_binding(version_id)
        if existing is not None:
            return existing
        resolved = self._validation_binding_authority.resolve(
            owner_user_id=str(_get(card, "author_user_id")),
            card_id=str(_get(card, "card_id")),
            version_id=version_id,
            track=Track(_get(card, "track")),
        )
        return self._repository.insert_validation_binding(
            validation_binding_id=_stable_id("validation-binding", version_id),
            card_id=str(_get(card, "card_id")),
            version_id=version_id,
            owner_user_id=str(_get(card, "author_user_id")),
            track=str(_get(card, "track")),
            validation_scope=resolved.validation_scope,
            knowledge_node_version_ref=resolved.knowledge_node_version_ref,
            canonical_question_id=resolved.canonical_question_id,
            canonical_content_version_id=resolved.canonical_content_version_id,
            canonical_task_version_ref=resolved.canonical_task_version_ref,
            answer_evidence_refs=list(resolved.answer_evidence_refs),
            validation_policy_version=resolved.validation_policy_version,
            general_method_outcome=resolved.general_method_outcome,
            bound_at=bound_at,
        )

    def _projection_score(self, version_id: str, policy_id: str) -> Decimal | None:
        projection = self._repository.projection(
            version_id=version_id, policy_id=policy_id
        )
        if projection is None:
            return None
        return _optional_decimal(_get(projection, "conservative_score"))

    def _refresh_projection(
        self,
        *,
        card_id: str,
        version_id: str,
        policy: PublishedEffectPolicy,
        now: datetime,
    ) -> None:
        raw_trials, votes = self._repository.effect_signals(version_id)
        trials = tuple(
            TrialSignal(
                correct_before=_get(row, "correct_before"),
                correct_after=bool(_get(row, "correct_after")),
                delayed_verification_outcome=_get(
                    row, "delayed_verification_outcome"
                ),
            )
            for row in raw_trials
        )
        cursor = _fingerprint(
            {
                "versionId": version_id,
                "policyVersion": policy.policy_version,
                "trials": [dict(row) for row in raw_trials],
                "votes": votes,
            }
        )
        projection = calculate_projection(
            policy=policy,
            trials=trials,
            votes=tuple(votes),
            evidence_cursor=cursor,
        )
        self._repository.upsert_projection(
            card_id=card_id,
            version_id=version_id,
            policy_id=policy.policy_id,
            eligible_trial_count=projection.eligible_trial_count,
            delayed_verified_count=projection.delayed_verified_count,
            helpful_count=projection.helpful_count,
            not_helpful_count=projection.not_helpful_count,
            conservative_score=projection.conservative_score,
            risk_flags=list(projection.risk_flags),
            evidence_cursor=projection.evidence_cursor,
            updated_at=now,
        )
        card = self._repository.card(card_id, for_update=True)
        if card is None or str(_get(card, "head_version_id")) != version_id:
            return
        if projection.independent_evidence_sufficient:
            self._append_milestone(
                card=card,
                version_id=version_id,
                milestone="independent_evidence_sufficient",
                evidence_refs=[f"community-effect:{version_id}:{cursor}"],
                occurred_at=now,
            )
        if projection.validated:
            self._append_milestone(
                card=card,
                version_id=version_id,
                milestone="conservative_effectiveness_reached",
                evidence_refs=[f"community-effect:{version_id}:{cursor}"],
                occurred_at=now,
            )
            if _get(card, "state") == CardState.TRIAL.value:
                moved = self._repository.transition_card(
                    card_id=card_id,
                    expected_state_version=int(_get(card, "state_version")),
                    expected_head_version_id=version_id,
                    state=CardState.COMMUNITY_VALIDATED.value,
                    head_version_id=version_id,
                    updated_at=now,
                )
                if moved is None:
                    raise CommunityMethodCardError("state_conflict")

    def _append_milestone(
        self,
        *,
        card: Mapping[str, Any],
        version_id: str,
        milestone: str,
        evidence_refs: list[str],
        occurred_at: datetime,
        beneficiary_user_id: str | None = None,
    ) -> None:
        event_id = _stable_id("milestone", version_id, milestone)
        beneficiary = beneficiary_user_id or str(_get(card, "author_user_id"))
        inserted = self._repository.insert_milestone(
            event_id=event_id,
            author_user_id=beneficiary,
            card_id=str(_get(card, "card_id")),
            version_id=version_id,
            milestone=milestone,
            evidence_refs=evidence_refs,
            occurred_at=occurred_at,
        )
        if inserted:
            self._milestone_sink.append(
                MilestoneQualified(
                    eventId=event_id,
                    authorId=beneficiary,
                    cardId=str(_get(card, "card_id")),
                    versionId=version_id,
                    milestone=milestone,
                    evidenceRefs=evidence_refs,
                    occurredAt=occurred_at,
                )
            )

    def _review_outcome(
        self,
        review: Mapping[str, Any],
        *,
        checks: list[ReviewCheck] | None = None,
    ) -> ReviewOutcome:
        if checks is None:
            check_rows = self._repository.review_checks(str(_get(review, "review_id")))
            by_dimension = {
                ReviewDimension(_get(row, "dimension")): ReviewCheck(
                    dimension=_get(row, "dimension"),
                    outcome=_get(row, "outcome"),
                    reasonCode=_get(row, "reason_code"),
                    evidenceRefs=list(_get(row, "evidence_refs")),
                )
                for row in check_rows
            }
            checks = [
                by_dimension[dimension]
                for dimension in (
                    ReviewDimension.DUPLICATE,
                    ReviewDimension.COPYRIGHT,
                    ReviewDimension.SAFETY,
                    ReviewDimension.KNOWLEDGE,
                    ReviewDimension.ANSWER_CONSISTENCY,
                )
            ]
        return ReviewOutcome(
            cardId=str(_get(review, "card_id")),
            versionId=str(_get(review, "version_id")),
            status=_get(review, "status"),
            checkCodes=[check.code for check in checks],
            reviewId=str(_get(review, "review_id")),
            policyVersion=_get(review, "policy_version"),
        )

    def _latest_candidate(self, card_id: str) -> Mapping[str, Any] | None:
        versions = self._repository.versions(card_id)
        if not versions:
            return None
        head = versions[-1]
        policy_resource = self._published_policy_resource("candidate_generation")
        fingerprint = _fingerprint(
            {
                "authorId": str(_get(head, "author_user_id")),
                "sourceVersionId": str(_get(head, "version_id")),
                "sourceHash": _get(head, "source_text_sha256"),
                "promptVersion": self._candidate_provider.prompt_version,
                "modelPolicyVersion": self._candidate_provider.model_policy_version,
                "policyResourceId": str(_get(policy_resource, "policy_resource_id")),
            }
        )
        return self._repository.candidate_by_fingerprint(
            author_user_id=str(_get(head, "author_user_id")),
            input_fingerprint=fingerprint,
        )

    @staticmethod
    def _assert_draft_replay(
        card: Mapping[str, Any],
        version: Mapping[str, Any],
        author_user_id: str,
        command: DraftCreate,
        source_hash: str,
    ) -> None:
        if (
            str(_get(card, "author_user_id")) != author_user_id
            or _get(card, "track") != command.track.value
            or _get(version, "source_type") != command.source_type.value
            or _get(version, "source_text_sha256") != source_hash
            or list(_get(version, "applicability")) != command.applicability
            or _optional_str(_get(version, "source_artifact_id"))
            != command.source_artifact_id
        ):
            raise CommunityMethodCardError("idempotency_conflict")

    @staticmethod
    def _assert_confirmation_replay(
        version: Mapping[str, Any], command: CandidateConfirm
    ) -> None:
        expected = {
            "card_id": command.card_id,
            "base_version_id": command.base_version_id,
            "title": command.title,
            "recognition": command.recognition,
            "method_steps": command.method_steps,
            "applicability": command.applicability,
            "exclusions": command.exclusions,
            "pitfalls": command.pitfalls,
            "rights_declaration": command.rights_declaration.value,
        }
        for key, value in expected.items():
            actual = _get(version, key)
            if key.endswith("_id") and actual is not None:
                actual = str(actual)
            if actual != value:
                raise CommunityMethodCardError("idempotency_conflict")

    @staticmethod
    def _assert_trial_replay(
        trial: Mapping[str, Any], command: TrialComplete
    ) -> None:
        if _get(trial, "question_version_id") != command.question_version_id:
            raise CommunityMethodCardError("idempotency_conflict")

    @staticmethod
    def _assert_delayed_replay(
        verification: Mapping[str, Any], command: DelayedVerificationRecord
    ) -> None:
        if (
            _get(verification, "question_attempt_id") != command.question_attempt_id
            or _get(verification, "question_version_id") != command.question_version_id
        ):
            raise CommunityMethodCardError("idempotency_conflict")


def _candidate_schema(row: Mapping[str, Any]) -> StructureCandidate:
    payload = _get(row, "candidate_payload") or {}
    return StructureCandidate(
        candidateId=str(_get(row, "candidate_id")),
        baseVersionId=str(_get(row, "source_version_id")),
        title=payload.get("title", ""),
        recognition=payload.get("recognition", ""),
        methodSteps=payload.get("methodSteps", []),
        sourceSpanRefs=list(_get(row, "source_span_refs")),
        status=_get(row, "status"),
        failureCode=_get(row, "failure_code"),
    )


def _confirm_receipt(row: Mapping[str, Any]) -> ConfirmReceipt:
    return ConfirmReceipt(
        cardId=str(_get(row, "card_id")),
        versionId=str(_get(row, "version_id")),
        versionNumber=int(_get(row, "version_no")),
        authorConfirmedAt=_get(row, "author_confirmed_at"),
    )


def _exposure_receipt(row: Mapping[str, Any]) -> ExposureReceipt:
    return ExposureReceipt(
        exposureId=str(_get(row, "exposure_id")),
        cardId=str(_get(row, "card_id")),
        versionId=str(_get(row, "version_id")),
        optedInAt=_get(row, "opted_in_at"),
    )


def _trial_receipt(row: Mapping[str, Any]) -> TrialReceipt:
    eligible = _get(row, "eligibility_status") == "eligible"
    return TrialReceipt(
        trialId=str(_get(row, "trial_id")),
        feedbackEligible=eligible,
        outcomeCode="eligible" if eligible else _get(row, "exclusion_reason"),
        delayedVerificationDueAt=_get(row, "delayed_verification_due_at"),
    )


def _report_decision_receipt(
    row: Mapping[str, Any], *, card: Mapping[str, Any], policy_version: str
) -> ReportDecisionReceipt:
    return ReportDecisionReceipt(
        reportId=str(_get(row, "report_id")),
        reportDecisionId=str(_get(row, "report_decision_id")),
        decision=_get(row, "decision"),
        cardStatus=_get(card, "state"),
        beneficiaryUserId=_optional_str(_get(row, "beneficiary_user_id")),
        policyVersion=policy_version,
    )


def _version_view(row: Mapping[str, Any]) -> CardVersionView:
    return CardVersionView(
        versionId=str(_get(row, "version_id")),
        cardId=str(_get(row, "card_id")),
        versionNumber=int(_get(row, "version_no")),
        baseVersionId=_optional_str(_get(row, "base_version_id")),
        title=_get(row, "title"),
        recognition=_get(row, "recognition"),
        methodSteps=list(_get(row, "method_steps")),
        applicability=list(_get(row, "applicability")),
        exclusions=list(_get(row, "exclusions")),
        pitfalls=list(_get(row, "pitfalls")),
        rightsDeclaration=_get(row, "rights_declaration"),
        authorConfirmedAt=_get(row, "author_confirmed_at"),
    )


def _review_subject(
    card: Mapping[str, Any],
    version: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> ReviewSubject:
    return ReviewSubject(
        card_id=str(_get(card, "card_id")),
        version_id=str(_get(version, "version_id")),
        author_user_id=str(_get(card, "author_user_id")),
        track=Track(_get(card, "track")),
        title=_get(version, "title"),
        recognition=_get(version, "recognition"),
        method_steps=tuple(_get(version, "method_steps")),
        applicability=tuple(_get(version, "applicability")),
        exclusions=tuple(_get(version, "exclusions")),
        pitfalls=tuple(_get(version, "pitfalls")),
        rights_declaration=RightsDeclaration(_get(version, "rights_declaration")),
        source_text_sha256=_get(version, "source_text_sha256"),
        validation_scope=_get(validation, "validation_scope"),
        knowledge_node_version_ref=_get(validation, "knowledge_node_version_ref"),
        answer_evidence_refs=tuple(_get(validation, "answer_evidence_refs")),
        validation_policy_version=_get(validation, "validation_policy_version"),
    )


def _stable_id(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(parts)))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fingerprint(value: object) -> str:
    return _sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    )


def _get(row: Mapping[str, Any], key: str) -> Any:
    return row[key]


def _optional_str(value: object | None) -> str | None:
    return None if value is None else str(value)


def _optional_decimal(value: object | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))
