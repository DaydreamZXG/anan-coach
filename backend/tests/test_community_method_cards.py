from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from typing import Any

import pytest

from app.domains.community_method_cards.candidate_provider import (
    CandidateContent,
    CandidateContext,
)
from app.domains.community_method_cards.effects import (
    ActorClusterDecision,
    CanonicalAttemptOutcome,
    QuestionEvidence,
    ServerSessionEvidence,
    VersionedTrialEligibility,
)
from app.domains.community_method_cards.moderation import (
    REQUIRED_REVIEW_DIMENSIONS,
    FiveDimensionReviewEngine,
    VersionedFixedReportPolicy,
)
from app.domains.community_method_cards.schemas import (
    CandidateConfirm,
    CardState,
    CardSubmit,
    DraftCreate,
    ExposureCreate,
    FeedbackRecord,
    ReviewCheck,
    ReviewCheckOutcome,
    ReviewDimension,
    TrialComplete,
)
from app.domains.community_method_cards.service import (
    CommunityMethodCardError,
    CommunityMethodCardService,
    SourceBinding,
    ValidationBinding,
)


NOW = datetime(2026, 7, 16, 6, tzinfo=UTC)


class _CandidateProvider:
    prompt_version = "community-structure-prompt@1"
    model_policy_version = "community-structure-model-policy@1"
    provider_release_id = "local-community-candidate"
    provider_endpoint_fingerprint = "0" * 64

    def generate(self, context: CandidateContext) -> CandidateContent:
        return CandidateContent(
            title="先判断题型，再执行步骤",
            recognition="题干出现比例关系时使用",
            method_steps=("标注已知量", "建立比例", "回代核验"),
            source_span_refs=(f"source:{context.source_version_id}:0-16",),
        )


class _Checker:
    def __init__(self, dimension: ReviewDimension) -> None:
        self.dimension = dimension
        self.outcome = ReviewCheckOutcome.PASSED

    def check(self, subject: object) -> ReviewCheck:
        del subject
        return ReviewCheck(
            dimension=self.dimension,
            outcome=self.outcome,
            reasonCode=f"{self.dimension.value}_policy_v1",
            evidenceRefs=[f"review-evidence:{self.dimension.value}"],
        )


class _QuestionVerifier:
    def verify(
        self,
        *,
        learner_user_id: str,
        question_attempt_id: str,
        question_version_id: str,
        card_version_id: str,
    ) -> QuestionEvidence:
        del learner_user_id, question_attempt_id, card_version_id
        return QuestionEvidence(
            independent=question_version_id != "question-dependent",
            new_for_learner=question_version_id != "question-old",
            correct_before=False,
            duration_before_ms=90000,
            evidence_refs=(f"question:{question_version_id}",),
        )


class _CanonicalAttemptVerifier:
    def verify(
        self,
        *,
        learner_user_id: str,
        question_attempt_id: str,
        question_version_id: str,
    ) -> CanonicalAttemptOutcome:
        del learner_user_id
        return CanonicalAttemptOutcome(
            question_attempt_id=question_attempt_id,
            question_version_id=question_version_id,
            correct_after=True,
            duration_after_ms=42000,
            evidence_refs=(f"attempt:{question_attempt_id}",),
        )


class _SessionResolver:
    def resolve(
        self,
        *,
        learner_user_id: str,
        question_attempt_id: str,
        question_version_id: str,
    ) -> ServerSessionEvidence:
        del question_version_id
        return ServerSessionEvidence(
            server_session_kind="xingce_task_session",
            server_session_id=f"session:{question_attempt_id}",
            session_evidence_sha256=hashlib.sha256(
                f"{learner_user_id}:{question_attempt_id}".encode()
            ).hexdigest(),
            attestation_policy_version="session-attestation@fixture-1",
            evidence_refs=(f"session:{question_attempt_id}",),
        )


class _ActorClusterAuthority:
    def decide(
        self,
        *,
        author_user_id: str,
        learner_user_id: str,
        session_evidence: ServerSessionEvidence,
        decided_at: datetime,
    ) -> ActorClusterDecision:
        del author_user_id, session_evidence, decided_at
        eligible = learner_user_id != "87381ead-9ae9-428c-9040-49d1600c04bf"
        return ActorClusterDecision(
            actor_cluster_hash=hashlib.sha256(learner_user_id.encode()).hexdigest(),
            policy_version="actor-cluster@fixture-1",
            eligible=eligible,
            reason_code=None if eligible else "linked_actor_cluster",
            snapshot={"eligible": eligible},
            evidence_refs=(f"actor-cluster:{learner_user_id}",),
        )


class _SourceBindingAuthority:
    def resolve(
        self,
        *,
        owner_user_id: str,
        source_type: str,
        artifact_version_id: str | None,
        reflection_version_id: str | None,
    ) -> SourceBinding:
        del owner_user_id, source_type, artifact_version_id
        return SourceBinding(
            binding_kind="xingce_reflection_version",
            artifact_version_id=None,
            learning_media_artifact_version_id=None,
            reflection_version_id=reflection_version_id,
            extraction_policy_version=None,
            source_span_refs=(f"reflection:{reflection_version_id}",),
            canonical_text_sha256=hashlib.sha256(
                "比例题先找不变量，再把两边统一到同一个基准。".encode()
            ).hexdigest(),
        )


class _ValidationBindingAuthority:
    def resolve(
        self,
        *,
        owner_user_id: str,
        card_id: str,
        version_id: str,
        track: object,
    ) -> ValidationBinding:
        del owner_user_id, card_id, track
        return ValidationBinding(
            validation_scope="canonical_task",
            knowledge_node_version_ref="knowledge-node@fixture-1",
            canonical_question_id=None,
            canonical_content_version_id=None,
            canonical_task_version_ref=f"task:{version_id}",
            answer_evidence_refs=(f"answer:{version_id}",),
            validation_policy_version="validation@fixture-1",
        )


class _ReportEvidenceAuthority:
    def resolve(
        self,
        *,
        reporter_user_id: str,
        card_version_id: str,
        evidence_locator: str,
    ) -> tuple[str, ...]:
        return (
            f"report-evidence:{reporter_user_id}:{card_version_id}:{evidence_locator}",
        )


class _MilestoneSink:
    def __init__(self) -> None:
        self.events: list[object] = []

    def append(self, event: object) -> None:
        self.events.append(event)


class _ClusterVerifier:
    def eligible(
        self,
        *,
        author_user_id: str,
        learner_user_id: str,
        device_cluster_hash: str,
    ) -> bool:
        del author_user_id, learner_user_id
        return device_cluster_hash != hashlib.sha256(b"blocked-cluster").hexdigest()


class _MemoryRepository:
    def __init__(self) -> None:
        self.cards: dict[str, dict[str, Any]] = {}
        self.version_rows: dict[str, dict[str, Any]] = {}
        self.candidates: dict[str, dict[str, Any]] = {}
        self.candidate_fingerprints: dict[tuple[str, str], str] = {}
        self.reviews: dict[str, dict[str, Any]] = {}
        self.checks: dict[str, list[dict[str, Any]]] = {}
        self.exposures: dict[str, dict[str, Any]] = {}
        self.trials: dict[str, dict[str, Any]] = {}
        self.votes: dict[str, dict[str, Any]] = {}
        self.projections: dict[tuple[str, str], dict[str, Any]] = {}
        self.milestones: set[tuple[str, str]] = set()
        self.source_bindings: dict[str, dict[str, Any]] = {}
        self.validation_bindings: dict[str, dict[str, Any]] = {}
        self.session_evidence: dict[str, dict[str, Any]] = {}
        self.eligibility_decisions: dict[str, dict[str, Any]] = {}
        self.reports: dict[str, dict[str, Any]] = {}
        self.report_decisions: dict[str, dict[str, Any]] = {}
        self.delayed: dict[str, dict[str, Any]] = {}
        self.policy = {
            "policy_id": "2ef1ee70-4a0a-4cd7-ab66-bc5db4fb0999",
            "policy_version": "community-effect@fixture-1",
            "state": "published",
            "published_at": NOW,
            "rules": {
                "algorithm": "wilson_composite_v1",
                "rankingPolicyVersion": "community-ranking@fixture-1",
                "minimumEligibleTrials": 1,
                "minimumDelayedVerifiedTrials": 0,
                "minimumHelpfulVotes": 1,
                "confidenceZ": "0.01",
                "correctnessWeight": "0.5",
                "helpfulnessWeight": "0.5",
                "delayedVerificationWeight": "0",
                "validationThreshold": "0.75",
                "minimumApplicabilityOverlap": 1,
                "maximumDiscoveryResults": 10,
                "personalizationWeight": "0.5",
                "delayedVerificationDelaySeconds": 86400,
                "requireBaseline": True,
            },
        }
        self.policy_resources = {
            operation: {
                "policy_resource_id": resource_id,
                "operation": operation,
                "policy_version": policy_version,
                "prompt_version": prompt_version,
                "rules": {"contract": "fixture-v2"},
                "rules_sha256": hashlib.sha256(
                    json.dumps(
                        {"contract": "fixture-v2"},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            }
            for operation, resource_id, policy_version, prompt_version in (
                (
                    "candidate_generation",
                    "b43c5e31-97f5-485b-8f20-17ed69d05ea6",
                    "community-structure-model-policy@1",
                    "community-structure-prompt@1",
                ),
                (
                    "automated_review",
                    "1d753dae-953c-47d3-a32f-5c4ca366c69a",
                    "community-review@fixture-1",
                    "community-review-prompt@1",
                ),
                (
                    "report_decision",
                    "61e09a43-9e53-4318-8e98-e31ec599839b",
                    "community-report@fixture-1",
                    "community-report-prompt@1",
                ),
            )
        }

    def lock(self, identity: str) -> None:
        del identity

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def card(self, card_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
        del for_update
        return self.cards.get(card_id)

    def version(self, version_id: str) -> dict[str, Any] | None:
        return self.version_rows.get(version_id)

    def versions(self, card_id: str) -> list[dict[str, Any]]:
        return sorted(
            [row for row in self.version_rows.values() if row["card_id"] == card_id],
            key=lambda row: row["version_no"],
        )

    def insert_draft(self, **values: Any) -> None:
        card = {
            "card_id": values["card_id"],
            "author_user_id": values["author_user_id"],
            "track": values["track"],
            "state": "private_draft",
            "head_version_id": values["version_id"],
            "state_version": 1,
            "created_at": values["created_at"],
            "updated_at": values["created_at"],
        }
        version = {
            "version_id": values["version_id"],
            "card_id": values["card_id"],
            "version_no": 1,
            "author_user_id": values["author_user_id"],
            "base_version_id": None,
            "source_type": values["source_type"],
            "source_artifact_id": values["source_artifact_id"],
            "source_text_sha256": values["source_text_sha256"],
            "private_source_text": values["source_text"],
            "title": None,
            "recognition": None,
            "method_steps": [],
            "applicability": list(values["applicability"]),
            "exclusions": [],
            "pitfalls": [],
            "rights_declaration": None,
            "author_confirmed_at": None,
            "created_at": values["created_at"],
        }
        self.cards[card["card_id"]] = card
        self.version_rows[version["version_id"]] = version

    def insert_source_binding(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.source_bindings[values["version_id"]] = row
        return row

    def source_binding(self, version_id: str) -> dict[str, Any] | None:
        return self.source_bindings.get(version_id)

    def copy_source_binding(self, **values: Any) -> dict[str, Any]:
        row = {
            **self.source_bindings[values["source_version_id"]],
            "source_binding_id": values["source_binding_id"],
            "version_id": values["target_version_id"],
            "card_id": values["target_card_id"],
            "owner_user_id": values["target_owner_user_id"],
            "bound_at": values["bound_at"],
        }
        self.source_bindings[values["target_version_id"]] = row
        return row

    def candidate_by_fingerprint(
        self, *, author_user_id: str, input_fingerprint: str
    ) -> dict[str, Any] | None:
        candidate_id = self.candidate_fingerprints.get(
            (author_user_id, input_fingerprint)
        )
        return self.candidates.get(candidate_id) if candidate_id else None

    def insert_candidate(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.candidates[values["candidate_id"]] = row
        self.candidate_fingerprints[
            (values["author_user_id"], values["input_fingerprint"])
        ] = values["candidate_id"]
        return row

    def record_candidate_external_processing(self, **values: Any) -> None:
        del values

    def assert_candidate_external_processing_authorized(self, **values: Any) -> None:
        del values

    def candidate(self, candidate_id: str) -> dict[str, Any] | None:
        return self.candidates.get(candidate_id)

    def confirmed_version_by_id(self, version_id: str) -> dict[str, Any] | None:
        row = self.version_rows.get(version_id)
        return row if row and row["author_confirmed_at"] is not None else None

    def insert_confirmed_version(self, **values: Any) -> dict[str, Any]:
        row = {
            **values,
            "version_no": values["version_no"],
            "base_version_id": values["base_version_id"],
            "private_source_text": values["private_source_text"],
            "author_confirmed_at": values["confirmed_at"],
            "created_at": values["confirmed_at"],
        }
        self.version_rows[values["version_id"]] = row
        return row

    def transition_card(self, **values: Any) -> dict[str, Any] | None:
        card = self.cards[values["card_id"]]
        if (
            card["state_version"] != values["expected_state_version"]
            or card["head_version_id"] != values["expected_head_version_id"]
        ):
            return None
        card.update(
            state=values["state"],
            head_version_id=values["head_version_id"],
            state_version=card["state_version"] + 1,
            updated_at=values["updated_at"],
        )
        return card

    def review_by_version(self, version_id: str) -> dict[str, Any] | None:
        return self.reviews.get(version_id)

    def published_policy_resource(self, operation: str) -> dict[str, Any] | None:
        return self.policy_resources.get(operation)

    def validation_binding(self, version_id: str) -> dict[str, Any] | None:
        return self.validation_bindings.get(version_id)

    def insert_validation_binding(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.validation_bindings[values["version_id"]] = row
        return row

    def review_checks(self, review_id: str) -> list[dict[str, Any]]:
        return self.checks[review_id]

    def insert_review(self, **values: Any) -> dict[str, Any]:
        row = {key: value for key, value in values.items() if key != "checks"}
        self.reviews[values["version_id"]] = row
        self.checks[values["review_id"]] = [dict(item) for item in values["checks"]]
        return row

    def insert_milestone(self, **values: Any) -> bool:
        identity = (values["version_id"], values["milestone"])
        if identity in self.milestones:
            return False
        self.milestones.add(identity)
        return True

    def published_effect_policy(self) -> dict[str, Any] | None:
        return self.policy

    def projection(self, *, version_id: str, policy_id: str) -> dict[str, Any] | None:
        return self.projections.get((version_id, policy_id))

    def exposure_for_learner(
        self, *, version_id: str, learner_user_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in self.exposures.values()
                if row["version_id"] == version_id
                and row["learner_user_id"] == learner_user_id
            ),
            None,
        )

    def insert_exposure(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.exposures[values["exposure_id"]] = row
        return row

    def exposure(self, exposure_id: str) -> dict[str, Any] | None:
        row = self.exposures.get(exposure_id)
        if row is None:
            return None
        card = self.cards[row["card_id"]]
        return {
            **row,
            "author_user_id": card["author_user_id"],
            "state": card["state"],
            "head_version_id": card["head_version_id"],
        }

    def trial_by_attempt(
        self, *, version_id: str, learner_user_id: str, question_attempt_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in self.trials.values()
                if row["version_id"] == version_id
                and row["learner_user_id"] == learner_user_id
                and row["question_attempt_id"] == question_attempt_id
            ),
            None,
        )

    def insert_trial(self, *, values: dict[str, Any]) -> dict[str, Any]:
        row = dict(values)
        self.trials[row["trial_id"]] = row
        return row

    def insert_trial_session_evidence(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.session_evidence[values["trial_id"]] = row
        return row

    def insert_eligibility_decision(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.eligibility_decisions[values["trial_id"]] = row
        return row

    def delayed_verification(self, trial_id: str) -> dict[str, Any] | None:
        return self.delayed.get(trial_id)

    def insert_delayed_verification(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.delayed[values["trial_id"]] = row
        return row

    def trial(self, trial_id: str) -> dict[str, Any] | None:
        row = self.trials.get(trial_id)
        if row is None:
            return None
        card = self.cards[row["card_id"]]
        return {**row, "author_user_id": card["author_user_id"], "state": card["state"]}

    def vote_for_version(
        self, *, version_id: str, voter_user_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in self.votes.values()
                if row["version_id"] == version_id
                and row["voter_user_id"] == voter_user_id
            ),
            None,
        )

    def insert_vote(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.votes[values["vote_id"]] = row
        return row

    def effect_signals(
        self, version_id: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        trials = [
            {
                "correct_before": row["correct_before"],
                "correct_after": row["correct_after"],
                "delayed_verification_outcome": None,
            }
            for row in self.trials.values()
            if row["version_id"] == version_id
            and row["eligibility_status"] == "eligible"
        ]
        votes = [
            row["vote"]
            for row in self.votes.values()
            if row["version_id"] == version_id
        ]
        return trials, votes

    def upsert_projection(self, **values: Any) -> None:
        self.projections[(values["version_id"], values["policy_id"])] = dict(values)

    def report_by_evidence(
        self, *, version_id: str, reporter_user_id: str, evidence_sha256: str
    ) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in self.reports.values()
                if row["version_id"] == version_id
                and row["reporter_user_id"] == reporter_user_id
                and row["evidence_sha256"] == evidence_sha256
            ),
            None,
        )

    def insert_report(self, *, values: dict[str, Any]) -> dict[str, Any]:
        row = dict(values)
        self.reports[row["report_id"]] = row
        return row

    def report(self, report_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
        del for_update
        return self.reports.get(report_id)

    def latest_report_decision(self, report_id: str) -> dict[str, Any] | None:
        return self.report_decisions.get(report_id)

    def insert_report_decision(self, **values: Any) -> dict[str, Any]:
        row = dict(values)
        self.report_decisions[values["report_id"]] = row
        return row

    def public_detail(self, *, version_id: str, policy_id: str) -> dict[str, Any] | None:
        version = self.version_rows.get(version_id)
        if version is None:
            return None
        card = self.cards[version["card_id"]]
        projection = self.projections.get((version_id, policy_id), {})
        return {**version, **projection, **card}


def test_stage35_authoring_review_trial_and_version_isolation() -> None:
    repository = _MemoryRepository()
    milestone_sink = _MilestoneSink()
    checkers = {dimension: _Checker(dimension) for dimension in REQUIRED_REVIEW_DIMENSIONS}
    service = CommunityMethodCardService(
        repository,
        candidate_provider=_CandidateProvider(),
        review_engine=FiveDimensionReviewEngine(
            policy_version="community-review@fixture-1", checkers=checkers
        ),
        trial_eligibility=VersionedTrialEligibility(
            question_verifier=_QuestionVerifier(),
            actor_cluster_verifier=_ClusterVerifier(),
        ),
        report_policy=VersionedFixedReportPolicy(
            policy_version="community-report@fixture-1",
            immediate_restriction_categories=frozenset({"unsafe"}),
        ),
        source_binding_authority=_SourceBindingAuthority(),
        validation_binding_authority=_ValidationBindingAuthority(),
        canonical_attempt_verifier=_CanonicalAttemptVerifier(),
        session_evidence_resolver=_SessionResolver(),
        actor_cluster_authority=_ActorClusterAuthority(),
        report_evidence_authority=_ReportEvidenceAuthority(),
        milestone_sink=milestone_sink,
        clock=lambda: NOW,
    )
    author_id = "971f26bd-6ed0-4622-b026-860321f54243"
    learner_id = "9506fa2b-d69c-4e25-a0ec-c6f75b3826a2"
    excluded_learner_id = "87381ead-9ae9-428c-9040-49d1600c04bf"

    draft = service.create_draft(
        author_user_id=author_id,
        command=DraftCreate(
            sourceType="reflection",
            sourceText="比例题先找不变量，再把两边统一到同一个基准。",
            reflectionVersionId="0e251b24-9aad-4a87-9a77-3b951ee16874",
            track="xingce",
            applicability=["数量关系", "比例问题"],
            idempotencyKey="draft-command-1",
        ),
    )
    initial_candidate = service.generate_candidate(
        author_user_id=author_id, card_id=draft.card_id
    )
    first_confirmed = service.confirm_candidate(
        author_user_id=author_id,
        command=CandidateConfirm(
            cardId=draft.card_id,
            baseVersionId=draft.version_id,
            candidateId=initial_candidate.candidate_id,
            title=initial_candidate.title,
            recognition=initial_candidate.recognition,
            methodSteps=initial_candidate.method_steps,
            applicability=["数量关系", "比例问题"],
            exclusions=["无需比例的直接计算"],
            pitfalls=["未统一基准"],
            rightsDeclaration="original",
        ),
    )
    frozen_first_payload = dict(repository.version_rows[first_confirmed.version_id])

    # One non-pass result blocks trial even though the other four dimensions pass.
    checkers[ReviewDimension.DUPLICATE].outcome = ReviewCheckOutcome.NEEDS_REVISION
    revision = service.submit_for_review(
        author_user_id=author_id,
        command=CardSubmit(
            cardId=draft.card_id,
            versionId=first_confirmed.version_id,
            idempotencyKey="review-command-1",
        ),
    )
    assert revision.status == CardState.REVISION_REQUIRED
    assert len(revision.check_codes) == 5

    revised_candidate = service.generate_candidate(
        author_user_id=author_id, card_id=draft.card_id
    )
    second_confirmed = service.confirm_candidate(
        author_user_id=author_id,
        command=CandidateConfirm(
            cardId=draft.card_id,
            baseVersionId=first_confirmed.version_id,
            candidateId=revised_candidate.candidate_id,
            title="比例题：不变量优先",
            recognition=revised_candidate.recognition,
            methodSteps=[*revised_candidate.method_steps, "检查量纲"],
            applicability=["数量关系", "比例问题"],
            exclusions=["无需比例的直接计算"],
            pitfalls=["未统一基准", "漏掉量纲"],
            rightsDeclaration="original",
        ),
    )
    assert repository.version_rows[first_confirmed.version_id] == frozen_first_payload
    assert second_confirmed.version_number == first_confirmed.version_number + 1

    checkers[ReviewDimension.DUPLICATE].outcome = ReviewCheckOutcome.PASSED
    passed = service.submit_for_review(
        author_user_id=author_id,
        command=CardSubmit(
            cardId=draft.card_id,
            versionId=second_confirmed.version_id,
            idempotencyKey="review-command-2",
        ),
    )
    assert passed.status == CardState.TRIAL
    assert all(":passed:" in code for code in passed.check_codes)

    with pytest.raises(CommunityMethodCardError, match="author_forbidden"):
        service.record_exposure(
            learner_user_id=author_id,
            command=ExposureCreate(
                cardVersionId=second_confirmed.version_id,
                applicabilitySnapshot=["比例问题"],
                rankingPolicyVersion="community-ranking@fixture-1",
            ),
        )

    excluded_exposure = service.record_exposure(
        learner_user_id=excluded_learner_id,
        command=ExposureCreate(
            cardVersionId=second_confirmed.version_id,
            applicabilitySnapshot=["比例问题"],
            rankingPolicyVersion="community-ranking@fixture-1",
        ),
    )
    excluded = service.complete_trial(
        learner_user_id=excluded_learner_id,
        command=TrialComplete(
            exposureId=excluded_exposure.exposure_id,
            questionAttemptId="attempt-excluded",
            questionVersionId="question-independent-excluded",
        ),
    )
    assert excluded.feedback_eligible is False
    with pytest.raises(CommunityMethodCardError, match="trial_not_eligible"):
        service.record_feedback(
            learner_user_id=excluded_learner_id,
            command=FeedbackRecord(
                trialId=excluded.trial_id, vote="helpful", reasonCode="clear"
            ),
        )

    exposure = service.record_exposure(
        learner_user_id=learner_id,
        command=ExposureCreate(
            cardVersionId=second_confirmed.version_id,
            applicabilitySnapshot=["比例问题"],
            rankingPolicyVersion="community-ranking@fixture-1",
        ),
    )
    trial = service.complete_trial(
        learner_user_id=learner_id,
        command=TrialComplete(
            exposureId=exposure.exposure_id,
            questionAttemptId="attempt-independent-new",
            questionVersionId="question-independent-new",
        ),
    )
    assert trial.feedback_eligible is True
    vote = service.record_feedback(
        learner_user_id=learner_id,
        command=FeedbackRecord(
            trialId=trial.trial_id, vote="helpful", reasonCode="transferable"
        ),
    )
    assert vote.recorded is True
    projection_key = (second_confirmed.version_id, repository.policy["policy_id"])
    assert repository.projections[projection_key]["eligible_trial_count"] == 1
    assert repository.cards[draft.card_id]["state"] == "community_validated"

    # Effectiveness is keyed to the immutable version. A later version starts empty.
    later_version_id = "a82d95f7-3cdd-4d7d-b7a0-8610e826f4e6"
    repository.version_rows[later_version_id] = {
        **repository.version_rows[second_confirmed.version_id],
        "version_id": later_version_id,
        "version_no": second_confirmed.version_number + 1,
        "base_version_id": second_confirmed.version_id,
    }
    assert repository.projection(
        version_id=later_version_id, policy_id=repository.policy["policy_id"]
    ) is None
    assert repository.projection(
        version_id=second_confirmed.version_id,
        policy_id=repository.policy["policy_id"],
    )["conservative_score"] >= Decimal("0.75")
