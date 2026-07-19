from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.ai.external_processing import ExternalProcessingRepository


class CommunityRepositoryPort(Protocol):
    def lock(self, identity: str) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class CommunityMethodCardRepository:
    """PostgreSQL owner for community cards; official product.method_cards is untouched."""

    def __init__(self, session: Session, *, environment: str = "local") -> None:
        self._session = session
        self._environment = environment

    def lock(self, identity: str) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"community-method-card:{identity}"},
        )

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def card(self, card_id: str, *, for_update: bool = False) -> RowMapping | None:
        lock = " for update" if for_update else ""
        return self._session.execute(
            text(
                "select * from product.community_method_cards "
                "where card_id = cast(:card_id as uuid)" + lock
            ),
            {"card_id": card_id},
        ).mappings().one_or_none()

    def version(self, version_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select version.*, card.track, card.state as card_state "
                "from product.community_method_card_versions version "
                "join product.community_method_cards card on card.card_id = version.card_id "
                "where version.version_id = cast(:version_id as uuid)"
            ),
            {"version_id": version_id},
        ).mappings().one_or_none()

    def versions(self, card_id: str) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    "select * from product.community_method_card_versions "
                    "where card_id = cast(:card_id as uuid) order by version_no"
                ),
                {"card_id": card_id},
            ).mappings().all()
        )

    def insert_draft(
        self,
        *,
        card_id: str,
        version_id: str,
        author_user_id: str,
        track: str,
        source_type: str,
        source_artifact_id: str | None,
        source_text: str,
        source_text_sha256: str,
        applicability: list[str],
        created_at: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into product.community_method_cards (
                  card_id, author_user_id, track, state, head_version_id,
                  state_version, created_at, updated_at
                ) values (
                  cast(:card_id as uuid), cast(:author_user_id as uuid), :track,
                  'private_draft', null, 1, :created_at, :created_at
                )
                """
            ),
            {
                "card_id": card_id,
                "author_user_id": author_user_id,
                "track": track,
                "created_at": created_at,
            },
        )
        self._session.execute(
            text(
                """
                insert into product.community_method_card_versions (
                  version_id, card_id, version_no, author_user_id, base_version_id,
                  source_type, source_artifact_id, source_text_sha256,
                  private_source_text, applicability, created_at
                ) values (
                  cast(:version_id as uuid), cast(:card_id as uuid), 1,
                  cast(:author_user_id as uuid), null, :source_type,
                  cast(:source_artifact_id as uuid), :source_text_sha256,
                  :source_text, cast(:applicability as jsonb), :created_at
                )
                """
            ),
            {
                "version_id": version_id,
                "card_id": card_id,
                "author_user_id": author_user_id,
                "source_type": source_type,
                "source_artifact_id": source_artifact_id,
                "source_text_sha256": source_text_sha256,
                "source_text": source_text,
                "applicability": _json(applicability),
                "created_at": created_at,
            },
        )
        self._session.execute(
            text(
                "update product.community_method_cards set head_version_id = "
                "cast(:version_id as uuid) where card_id = cast(:card_id as uuid)"
            ),
            {"version_id": version_id, "card_id": card_id},
        )

    def source_binding(self, version_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_source_bindings "
                "where version_id = cast(:version_id as uuid) and source_role = 'primary'"
            ),
            {"version_id": version_id},
        ).mappings().one_or_none()

    def insert_source_binding(self, **values: Any) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_source_bindings (
                  source_binding_id, card_id, version_id, owner_user_id,
                  binding_kind, source_role, artifact_version_id,
                  learning_media_artifact_version_id,
                  reflection_version_id, direct_text_sha256,
                  extraction_policy_version, source_span_refs, bound_at
                ) values (
                  cast(:source_binding_id as uuid), cast(:card_id as uuid),
                  cast(:version_id as uuid), cast(:owner_user_id as uuid),
                  :binding_kind, :source_role, cast(:artifact_version_id as uuid),
                  cast(:learning_media_artifact_version_id as uuid),
                  cast(:reflection_version_id as uuid), :direct_text_sha256,
                  :extraction_policy_version, cast(:source_span_refs as jsonb), :bound_at
                ) returning *
                """
            ),
            {**values, "source_span_refs": _json(values["source_span_refs"])},
        ).mappings().one()

    def copy_source_binding(
        self,
        *,
        source_version_id: str,
        target_version_id: str,
        target_card_id: str,
        target_owner_user_id: str,
        source_binding_id: str,
        bound_at: datetime,
    ) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_source_bindings (
                  source_binding_id, card_id, version_id, owner_user_id,
                  binding_kind, source_role, artifact_version_id,
                  learning_media_artifact_version_id,
                  reflection_version_id, direct_text_sha256,
                  extraction_policy_version, source_span_refs, bound_at
                )
                select cast(:source_binding_id as uuid), cast(:target_card_id as uuid),
                       cast(:target_version_id as uuid), cast(:target_owner_user_id as uuid),
                       binding_kind, source_role, artifact_version_id,
                       learning_media_artifact_version_id,
                       reflection_version_id, direct_text_sha256,
                       extraction_policy_version, source_span_refs, :bound_at
                  from product.community_method_card_source_bindings
                 where version_id = cast(:source_version_id as uuid)
                   and source_role = 'primary'
                returning *
                """
            ),
            {
                "source_binding_id": source_binding_id,
                "source_version_id": source_version_id,
                "target_version_id": target_version_id,
                "target_card_id": target_card_id,
                "target_owner_user_id": target_owner_user_id,
                "bound_at": bound_at,
            },
        ).mappings().one()

    def candidate(self, candidate_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_candidates "
                "where candidate_id = cast(:candidate_id as uuid)"
            ),
            {"candidate_id": candidate_id},
        ).mappings().one_or_none()

    def candidate_by_fingerprint(
        self, *, author_user_id: str, input_fingerprint: str
    ) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_candidates "
                "where author_user_id = cast(:author_user_id as uuid) "
                "and input_fingerprint = :input_fingerprint"
            ),
            {
                "author_user_id": author_user_id,
                "input_fingerprint": input_fingerprint,
            },
        ).mappings().one_or_none()

    def insert_candidate(
        self,
        *,
        candidate_id: str,
        card_id: str,
        source_version_id: str,
        author_user_id: str,
        prompt_version: str,
        model_policy_version: str,
        status: str,
        candidate_payload: dict[str, Any] | None,
        source_span_refs: list[str],
        input_fingerprint: str,
        failure_code: str | None,
        created_at: datetime,
        completed_at: datetime,
    ) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_candidates (
                  candidate_id, card_id, source_version_id, author_user_id,
                  prompt_version, model_policy_version, status, candidate_payload,
                  source_span_refs, input_fingerprint, failure_code, created_at, completed_at
                ) values (
                  cast(:candidate_id as uuid), cast(:card_id as uuid),
                  cast(:source_version_id as uuid), cast(:author_user_id as uuid),
                  :prompt_version, :model_policy_version, :status,
                  cast(:candidate_payload as jsonb), cast(:source_span_refs as jsonb),
                  :input_fingerprint, :failure_code, :created_at, :completed_at
                ) returning *
                """
            ),
            {
                "candidate_id": candidate_id,
                "card_id": card_id,
                "source_version_id": source_version_id,
                "author_user_id": author_user_id,
                "prompt_version": prompt_version,
                "model_policy_version": model_policy_version,
                "status": status,
                "candidate_payload": _json(candidate_payload),
                "source_span_refs": _json(source_span_refs),
                "input_fingerprint": input_fingerprint,
                "failure_code": failure_code,
                "created_at": created_at,
                "completed_at": completed_at,
            },
        ).mappings().one()

    def record_candidate_external_processing(
        self,
        *,
        candidate_id: str,
        author_user_id: str,
        provider_release_id: str,
        provider_endpoint_fingerprint: str,
        input_fingerprint: str,
        dispatched_at: datetime,
    ) -> None:
        ExternalProcessingRepository().record_community_candidate_dispatch(
            self._session,
            candidate_id=UUID(candidate_id),
            user_id=author_user_id,
            declared_provider_release=provider_release_id,
            provider_endpoint_fingerprint=provider_endpoint_fingerprint,
            input_fingerprint=input_fingerprint,
            production_authorization_required=(
                self._environment.strip().casefold() == "production"
            ),
            dispatched_at=dispatched_at,
        )

    def assert_candidate_external_processing_authorized(
        self,
        *,
        provider_release_id: str,
        provider_endpoint_fingerprint: str,
        checked_at: datetime,
    ) -> None:
        if self._environment.strip().casefold() != "production":
            return
        ExternalProcessingRepository().require_active_release(
            self._session,
            declared_provider_release=provider_release_id,
            provider_endpoint_fingerprint=provider_endpoint_fingerprint,
            purpose="community_candidate_generate",
            data_categories=("community_content",),
            at=checked_at,
        )

    def confirmed_version_by_id(self, version_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_versions "
                "where version_id = cast(:version_id as uuid) "
                "and author_confirmed_at is not null"
            ),
            {"version_id": version_id},
        ).mappings().one_or_none()

    def insert_confirmed_version(
        self,
        *,
        version_id: str,
        card_id: str,
        version_no: int,
        author_user_id: str,
        base_version_id: str,
        source_type: str,
        source_artifact_id: str | None,
        source_text_sha256: str,
        private_source_text: str,
        title: str,
        recognition: str,
        method_steps: list[str],
        applicability: list[str],
        exclusions: list[str],
        pitfalls: list[str],
        rights_declaration: str,
        confirmed_at: datetime,
    ) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_versions (
                  version_id, card_id, version_no, author_user_id, base_version_id,
                  source_type, source_artifact_id, source_text_sha256,
                  private_source_text, title, recognition, method_steps,
                  applicability, exclusions, pitfalls, rights_declaration,
                  author_confirmed_at, created_at
                ) values (
                  cast(:version_id as uuid), cast(:card_id as uuid), :version_no,
                  cast(:author_user_id as uuid), cast(:base_version_id as uuid),
                  :source_type, cast(:source_artifact_id as uuid), :source_text_sha256,
                  :private_source_text, :title, :recognition, cast(:method_steps as jsonb),
                  cast(:applicability as jsonb), cast(:exclusions as jsonb),
                  cast(:pitfalls as jsonb), :rights_declaration, :confirmed_at, :confirmed_at
                ) returning *
                """
            ),
            {
                "version_id": version_id,
                "card_id": card_id,
                "version_no": version_no,
                "author_user_id": author_user_id,
                "base_version_id": base_version_id,
                "source_type": source_type,
                "source_artifact_id": source_artifact_id,
                "source_text_sha256": source_text_sha256,
                "private_source_text": private_source_text,
                "title": title,
                "recognition": recognition,
                "method_steps": _json(method_steps),
                "applicability": _json(applicability),
                "exclusions": _json(exclusions),
                "pitfalls": _json(pitfalls),
                "rights_declaration": rights_declaration,
                "confirmed_at": confirmed_at,
            },
        ).mappings().one()

    def transition_card(
        self,
        *,
        card_id: str,
        expected_state_version: int,
        expected_head_version_id: str,
        state: str,
        head_version_id: str,
        updated_at: datetime,
    ) -> RowMapping | None:
        return self._session.execute(
            text(
                """
                update product.community_method_cards
                set state = :state, head_version_id = cast(:head_version_id as uuid),
                    state_version = state_version + 1, updated_at = :updated_at
                where card_id = cast(:card_id as uuid)
                  and state_version = :expected_state_version
                  and head_version_id = cast(:expected_head_version_id as uuid)
                returning *
                """
            ),
            {
                "card_id": card_id,
                "expected_state_version": expected_state_version,
                "expected_head_version_id": expected_head_version_id,
                "state": state,
                "head_version_id": head_version_id,
                "updated_at": updated_at,
            },
        ).mappings().one_or_none()

    def review_by_version(self, version_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_reviews "
                "where version_id = cast(:version_id as uuid)"
            ),
            {"version_id": version_id},
        ).mappings().one_or_none()

    def validation_binding(self, version_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_validation_bindings "
                "where version_id = cast(:version_id as uuid)"
            ),
            {"version_id": version_id},
        ).mappings().one_or_none()

    def insert_validation_binding(self, **values: Any) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_validation_bindings (
                  validation_binding_id, card_id, version_id, owner_user_id, track,
                  validation_scope, knowledge_node_version_ref,
                  canonical_question_id, canonical_content_version_id,
                  canonical_task_version_ref, answer_evidence_refs,
                  validation_policy_version, general_method_outcome, bound_at
                ) values (
                  cast(:validation_binding_id as uuid), cast(:card_id as uuid),
                  cast(:version_id as uuid), cast(:owner_user_id as uuid), :track,
                  :validation_scope, :knowledge_node_version_ref,
                  :canonical_question_id, :canonical_content_version_id,
                  :canonical_task_version_ref, cast(:answer_evidence_refs as jsonb),
                  :validation_policy_version, :general_method_outcome, :bound_at
                ) returning *
                """
            ),
            {**values, "answer_evidence_refs": _json(values["answer_evidence_refs"])},
        ).mappings().one()

    def published_policy_resource(self, operation: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_policy_resources "
                "where operation = :operation and state = 'published'"
            ),
            {"operation": operation},
        ).mappings().one_or_none()

    def review_checks(self, review_id: str) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    "select * from product.community_method_card_review_checks "
                    "where review_id = cast(:review_id as uuid) order by dimension"
                ),
                {"review_id": review_id},
            ).mappings().all()
        )

    def insert_review(
        self,
        *,
        review_id: str,
        card_id: str,
        version_id: str,
        policy_version: str,
        status: str,
        request_key: str,
        request_fingerprint: str,
        checks: list[dict[str, Any]],
        created_at: datetime,
    ) -> RowMapping:
        review = self._session.execute(
            text(
                """
                insert into product.community_method_card_reviews (
                  review_id, card_id, version_id, policy_version, status,
                  request_key, request_fingerprint, created_at
                ) values (
                  cast(:review_id as uuid), cast(:card_id as uuid),
                  cast(:version_id as uuid), :policy_version, :status,
                  :request_key, :request_fingerprint, :created_at
                ) returning *
                """
            ),
            {
                "review_id": review_id,
                "card_id": card_id,
                "version_id": version_id,
                "policy_version": policy_version,
                "status": status,
                "request_key": request_key,
                "request_fingerprint": request_fingerprint,
                "created_at": created_at,
            },
        ).mappings().one()
        for check in checks:
            self._session.execute(
                text(
                    """
                    insert into product.community_method_card_review_checks (
                      review_id, dimension, outcome, reason_code, evidence_refs, created_at
                    ) values (
                      cast(:review_id as uuid), :dimension, :outcome, :reason_code,
                      cast(:evidence_refs as jsonb), :created_at
                    )
                    """
                ),
                {
                    "review_id": review_id,
                    "created_at": created_at,
                    **check,
                    "evidence_refs": _json(check["evidence_refs"]),
                },
            )
        return review

    def insert_milestone(
        self,
        *,
        event_id: str,
        author_user_id: str,
        card_id: str,
        version_id: str,
        milestone: str,
        evidence_refs: list[str],
        occurred_at: datetime,
    ) -> bool:
        row = self._session.execute(
            text(
                """
                insert into product.community_method_card_milestone_events (
                  event_id, author_user_id, card_id, version_id, milestone,
                  evidence_refs, occurred_at
                ) values (
                  cast(:event_id as uuid), cast(:author_user_id as uuid),
                  cast(:card_id as uuid), cast(:version_id as uuid), :milestone,
                  cast(:evidence_refs as jsonb), :occurred_at
                ) on conflict (version_id, milestone) do nothing returning event_id
                """
            ),
            {
                "event_id": event_id,
                "author_user_id": author_user_id,
                "card_id": card_id,
                "version_id": version_id,
                "milestone": milestone,
                "evidence_refs": _json(evidence_refs),
                "occurred_at": occurred_at,
            },
        ).first()
        return row is not None

    def exposure(self, exposure_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select exposure.*, card.author_user_id, card.state, card.head_version_id "
                "from product.community_method_card_exposures exposure "
                "join product.community_method_cards card on card.card_id = exposure.card_id "
                "where exposure.exposure_id = cast(:exposure_id as uuid)"
            ),
            {"exposure_id": exposure_id},
        ).mappings().one_or_none()

    def exposure_for_learner(
        self, *, version_id: str, learner_user_id: str
    ) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_exposures "
                "where version_id = cast(:version_id as uuid) "
                "and learner_user_id = cast(:learner_user_id as uuid)"
            ),
            {"version_id": version_id, "learner_user_id": learner_user_id},
        ).mappings().one_or_none()

    def insert_exposure(
        self,
        *,
        exposure_id: str,
        card_id: str,
        version_id: str,
        learner_user_id: str,
        applicability_snapshot: list[str],
        ranking_policy_version: str,
        opted_in_at: datetime,
    ) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_exposures (
                  exposure_id, card_id, version_id, learner_user_id,
                  applicability_snapshot, ranking_policy_version, opted_in_at
                ) values (
                  cast(:exposure_id as uuid), cast(:card_id as uuid),
                  cast(:version_id as uuid), cast(:learner_user_id as uuid),
                  cast(:applicability_snapshot as jsonb), :ranking_policy_version, :opted_in_at
                ) returning *
                """
            ),
            {
                "exposure_id": exposure_id,
                "card_id": card_id,
                "version_id": version_id,
                "learner_user_id": learner_user_id,
                "applicability_snapshot": _json(applicability_snapshot),
                "ranking_policy_version": ranking_policy_version,
                "opted_in_at": opted_in_at,
            },
        ).mappings().one()

    def trial_by_attempt(
        self, *, version_id: str, learner_user_id: str, question_attempt_id: str
    ) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_trials "
                "where version_id = cast(:version_id as uuid) "
                "and learner_user_id = cast(:learner_user_id as uuid) "
                "and question_attempt_id = :question_attempt_id"
            ),
            {
                "version_id": version_id,
                "learner_user_id": learner_user_id,
                "question_attempt_id": question_attempt_id,
            },
        ).mappings().one_or_none()

    def trial(self, trial_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select trial.*, card.author_user_id, card.state "
                "from product.community_method_card_trials trial "
                "join product.community_method_cards card on card.card_id = trial.card_id "
                "where trial.trial_id = cast(:trial_id as uuid)"
            ),
            {"trial_id": trial_id},
        ).mappings().one_or_none()

    def insert_trial(self, *, values: dict[str, Any]) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_trials (
                  trial_id, exposure_id, card_id, version_id, learner_user_id,
                  question_attempt_id, question_version_id, correct_before,
                  correct_after, duration_before_ms, duration_after_ms,
                  eligibility_status, exclusion_reason, device_cluster_hash,
                  delayed_verification_due_at, completed_at
                ) values (
                  cast(:trial_id as uuid), cast(:exposure_id as uuid),
                  cast(:card_id as uuid), cast(:version_id as uuid),
                  cast(:learner_user_id as uuid), :question_attempt_id,
                  :question_version_id, :correct_before, :correct_after,
                  :duration_before_ms, :duration_after_ms, :eligibility_status,
                  :exclusion_reason, :device_cluster_hash,
                  :delayed_verification_due_at, :completed_at
                ) returning *
                """
            ),
            values,
        ).mappings().one()

    def insert_trial_session_evidence(self, **values: Any) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_trial_session_evidence (
                  session_evidence_id, trial_id, card_id, version_id, learner_user_id,
                  server_session_kind, server_session_id, session_evidence_sha256,
                  attestation_policy_version, evidence_refs, recorded_at
                ) values (
                  cast(:session_evidence_id as uuid), cast(:trial_id as uuid),
                  cast(:card_id as uuid), cast(:version_id as uuid),
                  cast(:learner_user_id as uuid), :server_session_kind,
                  :server_session_id, :session_evidence_sha256,
                  :attestation_policy_version, cast(:evidence_refs as jsonb), :recorded_at
                ) returning *
                """
            ),
            {**values, "evidence_refs": _json(values["evidence_refs"])},
        ).mappings().one()

    def insert_eligibility_decision(self, **values: Any) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_eligibility_decisions (
                  eligibility_decision_id, session_evidence_id, trial_id, card_id,
                  version_id, learner_user_id, actor_cluster_hash,
                  cluster_policy_version, decision, reason_code, decision_snapshot,
                  evidence_refs, decided_at
                ) values (
                  cast(:eligibility_decision_id as uuid),
                  cast(:session_evidence_id as uuid), cast(:trial_id as uuid),
                  cast(:card_id as uuid), cast(:version_id as uuid),
                  cast(:learner_user_id as uuid), :actor_cluster_hash,
                  :cluster_policy_version, :decision, :reason_code,
                  cast(:decision_snapshot as jsonb), cast(:evidence_refs as jsonb),
                  :decided_at
                ) returning *
                """
            ),
            {
                **values,
                "decision_snapshot": _json(values["decision_snapshot"]),
                "evidence_refs": _json(values["evidence_refs"]),
            },
        ).mappings().one()

    def delayed_verification(self, trial_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_delayed_verifications "
                "where trial_id = cast(:trial_id as uuid)"
            ),
            {"trial_id": trial_id},
        ).mappings().one_or_none()

    def insert_delayed_verification(
        self,
        *,
        verification_id: str,
        trial_id: str,
        question_attempt_id: str,
        question_version_id: str,
        correct_after: bool,
        duration_after_ms: int,
        outcome: str,
        evidence_refs: list[str],
        completed_at: datetime,
    ) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_delayed_verifications (
                  verification_id, trial_id, question_attempt_id, question_version_id,
                  correct_after, duration_after_ms, outcome, evidence_refs, completed_at
                ) values (
                  cast(:verification_id as uuid), cast(:trial_id as uuid),
                  :question_attempt_id, :question_version_id, :correct_after,
                  :duration_after_ms, :outcome, cast(:evidence_refs as jsonb), :completed_at
                ) returning *
                """
            ),
            {
                "verification_id": verification_id,
                "trial_id": trial_id,
                "question_attempt_id": question_attempt_id,
                "question_version_id": question_version_id,
                "correct_after": correct_after,
                "duration_after_ms": duration_after_ms,
                "outcome": outcome,
                "evidence_refs": _json(evidence_refs),
                "completed_at": completed_at,
            },
        ).mappings().one()

    def vote_for_version(
        self, *, version_id: str, voter_user_id: str
    ) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_votes "
                "where version_id = cast(:version_id as uuid) "
                "and voter_user_id = cast(:voter_user_id as uuid)"
            ),
            {"version_id": version_id, "voter_user_id": voter_user_id},
        ).mappings().one_or_none()

    def insert_vote(
        self,
        *,
        vote_id: str,
        trial_id: str,
        card_id: str,
        version_id: str,
        voter_user_id: str,
        vote: str,
        reason_code: str,
        created_at: datetime,
    ) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_votes (
                  vote_id, trial_id, card_id, version_id, voter_user_id,
                  vote, reason_code, created_at
                ) values (
                  cast(:vote_id as uuid), cast(:trial_id as uuid),
                  cast(:card_id as uuid), cast(:version_id as uuid),
                  cast(:voter_user_id as uuid), :vote, :reason_code, :created_at
                ) returning *
                """
            ),
            {
                "vote_id": vote_id,
                "trial_id": trial_id,
                "card_id": card_id,
                "version_id": version_id,
                "voter_user_id": voter_user_id,
                "vote": vote,
                "reason_code": reason_code,
                "created_at": created_at,
            },
        ).mappings().one()

    def report_by_evidence(
        self, *, version_id: str, reporter_user_id: str, evidence_sha256: str
    ) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_reports "
                "where version_id = cast(:version_id as uuid) "
                "and reporter_user_id = cast(:reporter_user_id as uuid) "
                "and evidence_sha256 = :evidence_sha256"
            ),
            {
                "version_id": version_id,
                "reporter_user_id": reporter_user_id,
                "evidence_sha256": evidence_sha256,
            },
        ).mappings().one_or_none()

    def insert_report(self, *, values: dict[str, Any]) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_reports (
                  report_id, card_id, version_id, reporter_user_id, category,
                  evidence_ref, evidence_sha256, decision, created_at
                ) values (
                  cast(:report_id as uuid), cast(:card_id as uuid),
                  cast(:version_id as uuid), cast(:reporter_user_id as uuid),
                  :category, :evidence_ref, :evidence_sha256, :decision, :created_at
                ) returning *
                """
            ),
            values,
        ).mappings().one()

    def report(self, report_id: str, *, for_update: bool = False) -> RowMapping | None:
        lock = " for update" if for_update else ""
        return self._session.execute(
            text(
                "select * from product.community_method_card_reports "
                "where report_id = cast(:report_id as uuid)" + lock
            ),
            {"report_id": report_id},
        ).mappings().one_or_none()

    def latest_report_decision(self, report_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_report_decisions "
                "where report_id = cast(:report_id as uuid) "
                "order by decision_version desc limit 1"
            ),
            {"report_id": report_id},
        ).mappings().one_or_none()

    def insert_report_decision(self, **values: Any) -> RowMapping:
        return self._session.execute(
            text(
                """
                insert into product.community_method_card_report_decisions (
                  report_decision_id, report_id, card_id, version_id,
                  reporter_user_id, decision_version, supersedes_report_decision_id,
                  decision, reason_code, policy_resource_id, reporter_is_beneficiary,
                  beneficiary_user_id, evidence_refs, actor_ref, decided_at
                ) values (
                  cast(:report_decision_id as uuid), cast(:report_id as uuid),
                  cast(:card_id as uuid), cast(:version_id as uuid),
                  cast(:reporter_user_id as uuid), :decision_version,
                  cast(:supersedes_report_decision_id as uuid), :decision, :reason_code,
                  cast(:policy_resource_id as uuid), :reporter_is_beneficiary,
                  cast(:beneficiary_user_id as uuid), cast(:evidence_refs as jsonb),
                  :actor_ref, :decided_at
                ) returning *
                """
            ),
            {**values, "evidence_refs": _json(values["evidence_refs"])},
        ).mappings().one()

    def published_effect_policy(self) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_effect_policies "
                "where state = 'published'"
            )
        ).mappings().one_or_none()

    def effect_signals(self, version_id: str) -> tuple[list[RowMapping], list[str]]:
        trials = list(
            self._session.execute(
                text(
                    "select trial.correct_before, trial.correct_after, "
                    "verification.outcome as delayed_verification_outcome "
                    "from product.community_method_card_trials trial "
                    "left join product.community_method_card_delayed_verifications verification "
                    "on verification.trial_id = trial.trial_id "
                    "where trial.version_id = cast(:version_id as uuid) "
                    "and trial.eligibility_status = 'eligible' "
                    "order by trial.completed_at, trial.trial_id"
                ),
                {"version_id": version_id},
            ).mappings().all()
        )
        votes = list(
            self._session.execute(
                text(
                    "select vote from product.community_method_card_votes "
                    "where version_id = cast(:version_id as uuid) order by created_at, vote_id"
                ),
                {"version_id": version_id},
            ).scalars().all()
        )
        return trials, votes

    def upsert_projection(
        self,
        *,
        card_id: str,
        version_id: str,
        policy_id: str,
        eligible_trial_count: int,
        delayed_verified_count: int,
        helpful_count: int,
        not_helpful_count: int,
        conservative_score: Decimal,
        risk_flags: list[str],
        evidence_cursor: str,
        updated_at: datetime,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into product.community_method_card_effect_projections (
                  card_id, version_id, policy_id, eligible_trial_count,
                  delayed_verified_count, helpful_count, not_helpful_count,
                  conservative_score, risk_flags, evidence_cursor, updated_at
                ) values (
                  cast(:card_id as uuid), cast(:version_id as uuid),
                  cast(:policy_id as uuid), :eligible_trial_count,
                  :delayed_verified_count, :helpful_count, :not_helpful_count,
                  :conservative_score, cast(:risk_flags as jsonb), :evidence_cursor, :updated_at
                ) on conflict (version_id, policy_id) do update set
                  eligible_trial_count = excluded.eligible_trial_count,
                  delayed_verified_count = excluded.delayed_verified_count,
                  helpful_count = excluded.helpful_count,
                  not_helpful_count = excluded.not_helpful_count,
                  conservative_score = excluded.conservative_score,
                  risk_flags = excluded.risk_flags,
                  evidence_cursor = excluded.evidence_cursor,
                  updated_at = excluded.updated_at
                """
            ),
            {
                "card_id": card_id,
                "version_id": version_id,
                "policy_id": policy_id,
                "eligible_trial_count": eligible_trial_count,
                "delayed_verified_count": delayed_verified_count,
                "helpful_count": helpful_count,
                "not_helpful_count": not_helpful_count,
                "conservative_score": conservative_score,
                "risk_flags": _json(risk_flags),
                "evidence_cursor": evidence_cursor,
                "updated_at": updated_at,
            },
        )

    def projection(self, *, version_id: str, policy_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                "select * from product.community_method_card_effect_projections "
                "where version_id = cast(:version_id as uuid) "
                "and policy_id = cast(:policy_id as uuid)"
            ),
            {"version_id": version_id, "policy_id": policy_id},
        ).mappings().one_or_none()

    def discovery_rows(
        self, *, track: str, learner_user_id: str
    ) -> list[RowMapping]:
        return list(
            self._session.execute(
                text(
                    """
                    select card.card_id, card.state, version.version_id, version.title,
                           version.recognition, version.method_steps,
                           version.applicability, version.exclusions, version.pitfalls,
                           projection.conservative_score,
                           projection.eligible_trial_count, projection.risk_flags
                    from product.community_method_cards card
                    join product.community_method_card_versions version
                      on version.version_id = card.head_version_id
                    left join product.community_method_card_effect_policies policy
                      on policy.state = 'published'
                    left join product.community_method_card_effect_projections projection
                      on projection.version_id = version.version_id
                     and projection.policy_id = policy.policy_id
                    where card.track = :track
                      and card.state in ('trial', 'community_validated')
                      and card.author_user_id <> cast(:learner_user_id as uuid)
                      and version.author_confirmed_at is not null
                      and version.rights_declaration in ('original', 'authorized_derivative')
                    """
                ),
                {"track": track, "learner_user_id": learner_user_id},
            ).mappings().all()
        )

    def public_detail(self, *, version_id: str, policy_id: str) -> RowMapping | None:
        return self._session.execute(
            text(
                """
                select card.state, card.head_version_id, version.version_id,
                       version.title, version.recognition, version.method_steps,
                       version.applicability, version.exclusions, version.pitfalls,
                       projection.eligible_trial_count,
                       projection.delayed_verified_count,
                       projection.conservative_score, projection.risk_flags
                  from product.community_method_cards card
                  join product.community_method_card_versions version
                    on version.card_id = card.card_id
                  left join product.community_method_card_effect_projections projection
                    on projection.version_id = version.version_id
                   and projection.policy_id = cast(:policy_id as uuid)
                 where version.version_id = cast(:version_id as uuid)
                   and version.author_confirmed_at is not null
                   and version.rights_declaration in ('original', 'authorized_derivative')
                """
            ),
            {"version_id": version_id, "policy_id": policy_id},
        ).mappings().one_or_none()

    def reward_entry_ids(self, author_user_id: str) -> list[str]:
        return [
            str(value)
            for value in self._session.execute(
                text(
                    "select ledger_entry_id from commerce.community_reward_ledger_entries "
                    "where beneficiary_user_id = cast(:author_user_id as uuid) "
                    "order by created_at desc, ledger_entry_id desc"
                ),
                {"author_user_id": author_user_id},
            ).scalars().all()
        ]

    def authored_card_ids(self, author_user_id: str) -> list[str]:
        return [
            str(value)
            for value in self._session.execute(
                text(
                    "select card_id from product.community_method_cards "
                    "where author_user_id = cast(:author_user_id as uuid) "
                    "order by updated_at desc, card_id desc"
                ),
                {"author_user_id": author_user_id},
            ).scalars().all()
        ]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
