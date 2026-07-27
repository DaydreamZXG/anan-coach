from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.guokao_shenlun import (
    GUOKAO_SHENLUN_GENERAL_METHOD_VALIDATION_POLICY_VERSION,
    unique_guokao_shenlun_applicability,
)
from app.core.config import Settings
from app.domains.knowledge.module_vocabulary import load_current_module_vocabulary
from app.domains.community_method_cards.candidate_provider import (
    CommunityCandidateHttpProvider,
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
from app.domains.community_method_cards.repository import CommunityMethodCardRepository
from app.domains.community_method_cards.schemas import (
    MilestoneQualified,
    ReviewCheck,
    ReviewCheckOutcome,
    ReviewDimension,
    Track,
)
from app.domains.community_method_cards.service import (
    CommunityMethodCardError,
    CommunityMethodCardService,
    SourceBinding,
    ValidationBinding,
)
from app.domains.community_rewards.repository import SqlCommunityRewardRepository
from app.domains.community_rewards.service import CommunityRewardService


@dataclass(frozen=True)
class AuthenticatedSessionContext:
    session_id: str
    user_id: str
    device_id: str


class SqlSourceBindingAuthority:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        *,
        owner_user_id: str,
        source_type: str,
        artifact_version_id: str | None,
        reflection_version_id: str | None,
    ) -> SourceBinding:
        if source_type == "reflection":
            reflection = self._reflection(owner_user_id, reflection_version_id)
            raw_text = _reflection_text(reflection["payload"])
            return SourceBinding(
                binding_kind="xingce_reflection_version",
                artifact_version_id=None,
                learning_media_artifact_version_id=None,
                reflection_version_id=str(reflection["reflection_version_id"]),
                extraction_policy_version=None,
                source_span_refs=(
                    f"xingce-reflection:{reflection['reflection_version_id']}",
                ),
                canonical_text_sha256=_sha256(raw_text),
            )
        if source_type == "voice_transcript":
            if artifact_version_id is None or reflection_version_id is None:
                raise CommunityMethodCardError("source_binding_invalid")
            row = self._session.execute(
                text(
                    """
                    select media.artifact_version_id, media.content_sha256,
                           binding.binding_id, reflection.reflection_version_id,
                           reflection.payload, reflection.payload_sha256
                    from learning.learning_media_artifact_versions media
                    join learning.learning_media_bindings binding
                      on binding.artifact_version_id = media.artifact_version_id
                     and binding.user_id = media.user_id
                     and binding.entity_type = 'xingce_reflection'
                    join learning.xingce_reflection_versions reflection
                      on reflection.reflection_version_id = binding.entity_version_id
                     and reflection.user_id = binding.user_id
                    where media.artifact_version_id = cast(:artifact_version_id as uuid)
                      and reflection.reflection_version_id = cast(:reflection_version_id as uuid)
                      and media.user_id = cast(:owner_user_id as uuid)
                      and media.state = 'available'
                    """
                ),
                {
                    "artifact_version_id": artifact_version_id,
                    "reflection_version_id": reflection_version_id,
                    "owner_user_id": owner_user_id,
                },
            ).mappings().one_or_none()
            if row is None:
                raise CommunityMethodCardError("source_artifact_not_owned")
            return SourceBinding(
                binding_kind="learning_media_reflection_version",
                artifact_version_id=None,
                learning_media_artifact_version_id=str(row["artifact_version_id"]),
                reflection_version_id=str(row["reflection_version_id"]),
                extraction_policy_version=None,
                source_span_refs=(
                    f"learning-media:{row['artifact_version_id']}",
                    f"learning-media-binding:{row['binding_id']}",
                    f"xingce-reflection:{row['reflection_version_id']}",
                ),
                canonical_text_sha256=_sha256(_reflection_text(row["payload"])),
            )
        if source_type == "handwriting_extraction":
            if artifact_version_id is None or reflection_version_id is not None:
                raise CommunityMethodCardError("source_binding_invalid")
            row = self._session.execute(
                text(
                    """
                    select artifact.artifact_version_id, recognition.recognition_revision_id,
                           recognition.processor_policy_version,
                           recognition.candidate_payload
                    from learning.source_artifact_versions artifact
                    join learning.recognition_revisions recognition
                      on recognition.artifact_version_id = artifact.artifact_version_id
                     and recognition.owner_user_id = artifact.owner_user_id
                    where artifact.artifact_version_id = cast(:artifact_version_id as uuid)
                      and artifact.owner_user_id = cast(:owner_user_id as uuid)
                      and artifact.state = 'available'
                      and recognition.recognition_kind = 'handwriting_ocr'
                      and recognition.state = 'confirmed'
                      and not exists (
                        select 1 from learning.recognition_revisions successor
                        where successor.supersedes_recognition_revision_id =
                              recognition.recognition_revision_id
                      )
                    """
                ),
                {
                    "artifact_version_id": artifact_version_id,
                    "owner_user_id": owner_user_id,
                },
            ).mappings().one_or_none()
            if row is None:
                raise CommunityMethodCardError("source_artifact_not_owned")
            canonical_text, span_refs = _recognition_text(row["candidate_payload"])
            return SourceBinding(
                binding_kind="source_artifact_version",
                artifact_version_id=str(row["artifact_version_id"]),
                learning_media_artifact_version_id=None,
                reflection_version_id=None,
                extraction_policy_version=str(row["processor_policy_version"]),
                source_span_refs=tuple(
                    [f"recognition:{row['recognition_revision_id']}", *span_refs]
                ),
                canonical_text_sha256=_sha256(canonical_text),
            )
        raise CommunityMethodCardError("source_binding_invalid")

    def _reflection(
        self, owner_user_id: str, reflection_version_id: str | None
    ) -> Mapping[str, Any]:
        if reflection_version_id is None:
            raise CommunityMethodCardError("source_binding_invalid")
        row = self._session.execute(
            text(
                """
                select reflection_version_id, payload, payload_sha256
                from learning.xingce_reflection_versions
                where reflection_version_id = cast(:reflection_version_id as uuid)
                  and user_id = cast(:owner_user_id as uuid)
                  and not exists (
                    select 1 from learning.xingce_reflection_versions successor
                    where successor.supersedes_reflection_version_id =
                          learning.xingce_reflection_versions.reflection_version_id
                  )
                """
            ),
            {
                "reflection_version_id": reflection_version_id,
                "owner_user_id": owner_user_id,
            },
        ).mappings().one_or_none()
        if row is None:
            raise CommunityMethodCardError("reflection_not_owned")
        return row


class SqlValidationBindingAuthority:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        *,
        owner_user_id: str,
        card_id: str,
        version_id: str,
        track: Track,
    ) -> ValidationBinding:
        if track == Track.XINGCE:
            canonical = self._session.execute(
                text(
                """
                    select assessment.question_id, assessment.content_version_id,
                           assessment.assessment_id
                    from product.community_method_card_source_bindings source
                    join learning.xingce_reflection_versions reflection
                      on reflection.reflection_version_id = source.reflection_version_id
                     and reflection.user_id = source.owner_user_id
                    join learning.xingce_attempt_records attempt
                      on attempt.session_id = reflection.session_id
                     and attempt.user_id = reflection.user_id
                    join learning.xingce_assessments assessment
                      on assessment.attempt_record_id = attempt.attempt_record_id
                     and assessment.user_id = attempt.user_id
                    where source.version_id = cast(:version_id as uuid)
                      and source.owner_user_id = cast(:owner_user_id as uuid)
                      and 1 = (
                        select count(*)
                        from learning.xingce_attempt_records session_attempt
                        where session_attempt.session_id = reflection.session_id
                          and session_attempt.user_id = reflection.user_id
                      )
                    order by attempt.created_at desc, attempt.attempt_record_id desc
                    limit 1
                    """
                ),
                {"version_id": version_id, "owner_user_id": owner_user_id},
            ).mappings().one_or_none()
            if canonical is not None:
                node = self._published_question_node(
                    question_id=str(canonical["question_id"]),
                    content_version_id=str(canonical["content_version_id"]),
                )
                return ValidationBinding(
                    validation_scope="canonical_question",
                    knowledge_node_version_ref=_node_version(node),
                    canonical_question_id=str(canonical["question_id"]),
                    canonical_content_version_id=str(
                        canonical["content_version_id"]
                    ),
                    canonical_task_version_ref=None,
                    answer_evidence_refs=(
                        f"xingce-assessment:{canonical['assessment_id']}",
                    ),
                    validation_policy_version=_node_version(node),
                )
        version = self._session.execute(
            text(
                """
                select applicability from product.community_method_card_versions
                where version_id = cast(:version_id as uuid)
                  and card_id = cast(:card_id as uuid)
                  and author_user_id = cast(:owner_user_id as uuid)
                """
            ),
            {
                "version_id": version_id,
                "card_id": card_id,
                "owner_user_id": owner_user_id,
            },
        ).mappings().one()
        if track == Track.ESSAY:
            return self._essay_general_method_binding(version["applicability"])
        node = self._unique_applicability_node(track=track, values=version["applicability"])
        return ValidationBinding(
            validation_scope="general_method",
            knowledge_node_version_ref=_node_version(node),
            canonical_question_id=None,
            canonical_content_version_id=None,
            canonical_task_version_ref=None,
            answer_evidence_refs=(),
            validation_policy_version=_node_version(node),
            general_method_outcome="needs_revision",
        )

    @staticmethod
    def _essay_general_method_binding(values: list[str]) -> ValidationBinding:
        category = unique_guokao_shenlun_applicability(values)
        if category is None:
            raise CommunityMethodCardError("knowledge_binding_ambiguous")
        return ValidationBinding(
            validation_scope="general_method",
            knowledge_node_version_ref=category.applicability_version_ref,
            canonical_question_id=None,
            canonical_content_version_id=None,
            canonical_task_version_ref=None,
            answer_evidence_refs=(),
            validation_policy_version=(
                GUOKAO_SHENLUN_GENERAL_METHOD_VALIDATION_POLICY_VERSION
            ),
            general_method_outcome="needs_revision",
        )

    def _published_node(
        self, *, track: str, module_name: str, tag_value: str
    ) -> Mapping[str, Any]:
        vocabulary = load_current_module_vocabulary(
            self._session,
            exam_scope="guokao",
            track=track,
        )
        canonical_module = (
            vocabulary.canonicalize(module_name) if vocabulary is not None else None
        )
        if canonical_module is None:
            raise CommunityMethodCardError("knowledge_binding_ambiguous")
        nodes = self._session.execute(
            text(
                """
                with active_releases as (
                  select
                    release.release_id,
                    release.release_version,
                    release.fresh_until,
                    count(*) over () as active_release_count
                  from product.knowledge_map_releases release
                  where release.exam_scope = 'guokao'
                    and release.track = :track
                    and release.status = 'active'
                ), current_release as (
                  select release_id, release_version
                  from active_releases
                  where active_release_count = 1
                    and fresh_until > statement_timestamp()
                    and release_id = cast(:release_id as uuid)
                )
                select
                  release.release_id,
                  release.release_version,
                  node.node_id,
                  node.updated_at
                from current_release release
                join product.knowledge_map_nodes node
                  on node.release_id = release.release_id
                where node.exam_scope = 'guokao'
                  and node.track = :track
                  and node.module_name = :module_name
                  and node.tag_value = :tag_value
                  and node.status = 'published'
                  and node.support_status = 'text_supported'
                order by node.node_id
                """
            ),
            {
                "release_id": vocabulary.release_id,
                "track": track,
                "module_name": canonical_module,
                "tag_value": tag_value,
            },
        ).mappings().all()
        if len(nodes) != 1:
            raise CommunityMethodCardError("knowledge_binding_ambiguous")
        return nodes[0]

    def _published_question_node(
        self,
        *,
        question_id: str,
        content_version_id: str,
    ) -> Mapping[str, Any]:
        """Resolve the exact frozen question binding in the current release."""

        vocabulary = load_current_module_vocabulary(
            self._session,
            exam_scope="guokao",
            track="xingce",
        )
        if vocabulary is None:
            raise CommunityMethodCardError("knowledge_binding_ambiguous")
        nodes = self._session.execute(
            text(
                """
                with active_releases as (
                  select
                    release.release_id,
                    release.release_version,
                    release.fresh_until,
                    count(*) over () as active_release_count
                  from product.knowledge_map_releases release
                  where release.exam_scope = 'guokao'
                    and release.track = 'xingce'
                    and release.status = 'active'
                ), current_release as (
                  select release_id, release_version
                  from active_releases
                  where active_release_count = 1
                    and fresh_until > statement_timestamp()
                    and release_id = cast(:release_id as uuid)
                )
                select
                  release.release_id,
                  release.release_version,
                  node.node_id,
                  node.node_type,
                  node.updated_at,
                  content.snapshot_payload as question_snapshot
                from current_release release
                join product.knowledge_map_question_bindings binding
                  on binding.release_id = release.release_id
                 and binding.question_id = :question_id
                 and binding.content_version_id = :content_version_id
                 and binding.delivery_eligible
                join product.knowledge_map_nodes node
                  on node.release_id = release.release_id
                 and node.node_id = binding.node_id
                join product.question_content_versions content
                  on content.question_id = binding.question_id
                 and content.content_version_id = binding.content_version_id
                 and content.track = 'xingce'
                where node.exam_scope = 'guokao'
                  and node.track = 'xingce'
                  and node.node_type in (
                    'micro_skill',
                    'knowledge_point',
                    'knowledge_family'
                  )
                  and node.status = 'published'
                  and node.support_status = 'text_supported'
                order by
                  case node.node_type
                    when 'micro_skill' then 0
                    when 'knowledge_point' then 1
                    else 2
                  end,
                  node.node_id
                """
            ),
            {
                "release_id": vocabulary.release_id,
                "question_id": question_id,
                "content_version_id": content_version_id,
            },
        ).mappings().all()
        if not nodes:
            raise CommunityMethodCardError("knowledge_binding_ambiguous")
        most_specific_type = str(nodes[0]["node_type"])
        most_specific = [
            node for node in nodes if str(node["node_type"]) == most_specific_type
        ]
        if len(most_specific) != 1:
            raise CommunityMethodCardError("knowledge_binding_ambiguous")
        return most_specific[0]

    def _unique_applicability_node(
        self, *, track: Track, values: list[str]
    ) -> Mapping[str, Any]:
        if not values:
            raise CommunityMethodCardError("knowledge_binding_missing")
        if track != Track.XINGCE:
            raise CommunityMethodCardError("knowledge_binding_ambiguous")
        db_track = "xingce"
        vocabulary = load_current_module_vocabulary(
            self._session,
            exam_scope="guokao",
            track=db_track,
        )
        if vocabulary is None:
            raise CommunityMethodCardError("knowledge_binding_ambiguous")
        normalized_values = list(values)
        normalized_values.extend(
            canonical
            for value in values
            if (canonical := vocabulary.canonicalize(value)) is not None
        )
        normalized_values = list(dict.fromkeys(normalized_values))
        nodes = self._session.execute(
            text(
                """
                with active_releases as (
                  select
                    release.release_id,
                    release.release_version,
                    release.fresh_until,
                    count(*) over () as active_release_count
                  from product.knowledge_map_releases release
                  where release.exam_scope = 'guokao'
                    and release.track = :track
                    and release.status = 'active'
                ), current_release as (
                  select release_id, release_version
                  from active_releases
                  where active_release_count = 1
                    and fresh_until > statement_timestamp()
                    and release_id = cast(:expected_release_id as uuid)
                )
                select
                  release.release_id,
                  release.release_version,
                  node.node_id,
                  node.updated_at
                from current_release release
                join product.knowledge_map_nodes node
                  on node.release_id = release.release_id
                where node.exam_scope = 'guokao'
                  and node.track = :track
                  and node.status = 'published'
                  and node.support_status = 'text_supported'
                  and (
                    node.tag_value = any(cast(:values as text[]))
                    or node.display_path = any(cast(:values as text[]))
                  )
                order by node.node_id
                """
            ),
            {
                "track": db_track,
                "expected_release_id": vocabulary.release_id,
                "values": normalized_values,
            },
        ).mappings().all()
        if len(nodes) != 1:
            raise CommunityMethodCardError("knowledge_binding_ambiguous")
        return nodes[0]


class SqlCanonicalAttemptAuthority:
    def __init__(
        self,
        session: Session,
        authenticated_session: AuthenticatedSessionContext,
        *,
        attestation_policy_version: str,
    ) -> None:
        self._session = session
        self._authenticated_session = authenticated_session
        self._attestation_policy_version = attestation_policy_version

    def verify(
        self,
        *,
        learner_user_id: str,
        question_attempt_id: str,
        question_version_id: str,
    ) -> CanonicalAttemptOutcome:
        row = self._xingce_attempt(
            learner_user_id, question_attempt_id, question_version_id
        )
        if row is not None:
            duration_ms = int(row["timing_evidence"]["questionElapsedSeconds"]) * 1000
            return CanonicalAttemptOutcome(
                question_attempt_id=question_attempt_id,
                question_version_id=question_version_id,
                correct_after=bool(row["correct"]),
                duration_after_ms=duration_ms,
                evidence_refs=(
                    f"xingce-attempt:{row['attempt_record_id']}",
                    f"xingce-assessment:{row['assessment_id']}",
                    f"question-content:{row['content_version_id']}",
                ),
            )
        essay = self._essay_attempt(
            learner_user_id, question_attempt_id, question_version_id
        )
        if essay is None:
            raise CommunityMethodCardError("canonical_attempt_not_found")
        return CanonicalAttemptOutcome(
            question_attempt_id=question_attempt_id,
            question_version_id=question_version_id,
            correct_after=essay["status"] == "verified",
            duration_after_ms=int(essay["elapsed_seconds"] or 0) * 1000,
            evidence_refs=(
                f"essay-transfer-verification:{essay['verification_version_id']}",
                f"essay-attempt:{essay['transfer_attempt_id']}",
                f"essay-task:{essay['transfer_task_version_id']}",
            ),
        )

    def resolve(
        self,
        *,
        learner_user_id: str,
        question_attempt_id: str,
        question_version_id: str,
    ) -> ServerSessionEvidence:
        xingce = self._xingce_attempt(
            learner_user_id, question_attempt_id, question_version_id
        )
        if xingce is not None:
            kind = "xingce_task_session"
            canonical_session_id = str(xingce["session_id"])
            canonical_refs = [f"xingce-session:{canonical_session_id}"]
        else:
            essay = self._essay_attempt(
                learner_user_id, question_attempt_id, question_version_id
            )
            if essay is None:
                raise CommunityMethodCardError("canonical_attempt_not_found")
            kind = "essay_attempt_session"
            canonical_session_id = str(essay["transfer_attempt_id"])
            canonical_refs = [f"essay-attempt:{canonical_session_id}"]
        payload = {
            "authSessionId": self._authenticated_session.session_id,
            "canonicalSessionId": canonical_session_id,
            "deviceIdSha256": _sha256(self._authenticated_session.device_id),
            "learnerUserId": learner_user_id,
            "questionAttemptId": question_attempt_id,
            "questionVersionId": question_version_id,
        }
        return ServerSessionEvidence(
            server_session_kind=kind,
            server_session_id=canonical_session_id,
            session_evidence_sha256=_fingerprint(payload),
            attestation_policy_version=self._attestation_policy_version,
            evidence_refs=tuple(
                [
                    f"auth-session:{self._authenticated_session.session_id}",
                    *canonical_refs,
                ]
            ),
        )

    def _xingce_attempt(
        self, user_id: str, attempt_id: str, version_id: str
    ) -> Mapping[str, Any] | None:
        return self._session.execute(
            text(
                """
                select attempt.attempt_record_id, attempt.session_id,
                       attempt.content_version_id, attempt.timing_evidence,
                       assessment.assessment_id, assessment.correct,
                       attempt.question_id, attempt.created_at
                from learning.xingce_attempt_records attempt
                join learning.xingce_assessments assessment
                  on assessment.attempt_record_id = attempt.attempt_record_id
                 and assessment.user_id = attempt.user_id
                where attempt.user_id = cast(:user_id as uuid)
                  and attempt.item_attempt_id = :attempt_id
                  and attempt.content_version_id = :version_id
                """
            ),
            {"user_id": user_id, "attempt_id": attempt_id, "version_id": version_id},
        ).mappings().one_or_none()

    def _essay_attempt(
        self, user_id: str, attempt_id: str, version_id: str
    ) -> Mapping[str, Any] | None:
        try:
            UUID(attempt_id)
            UUID(version_id)
        except ValueError:
            return None
        return self._session.execute(
            text(
                """
                select verification.verification_version_id, verification.status,
                       verification.transfer_attempt_id,
                       verification.transfer_task_version_id,
                       attempt.elapsed_seconds
                from learning.essay_transfer_verification_versions verification
                join learning.essay_attempts attempt
                  on attempt.attempt_id = verification.transfer_attempt_id
                 and attempt.user_id = verification.user_id
                where verification.user_id = cast(:user_id as uuid)
                  and verification.transfer_attempt_id = cast(:attempt_id as uuid)
                  and verification.transfer_task_version_id = cast(:version_id as uuid)
                  and not exists (
                    select 1
                    from learning.essay_transfer_verification_versions successor
                    where successor.supersedes_verification_version_id =
                          verification.verification_version_id
                  )
                order by verification.generated_at desc,
                         verification.verification_version_id desc
                limit 1
                """
            ),
            {"user_id": user_id, "attempt_id": attempt_id, "version_id": version_id},
        ).mappings().one_or_none()


class SqlQuestionEvidenceVerifier:
    def __init__(self, session: Session) -> None:
        self._session = session

    def verify(
        self,
        *,
        learner_user_id: str,
        question_attempt_id: str,
        question_version_id: str,
        card_version_id: str,
    ) -> QuestionEvidence:
        row = self._session.execute(
            text(
                """
                select attempt.attempt_record_id, attempt.question_id,
                       attempt.created_at, attempt.timing_evidence,
                       exposure.opted_in_at,
                       validation.canonical_question_id,
                       not exists (
                         select 1 from learning.xingce_attempt_records prior
                         where prior.user_id = attempt.user_id
                           and prior.question_id = attempt.question_id
                           and prior.created_at < exposure.opted_in_at
                       ) as new_for_learner
                from learning.xingce_attempt_records attempt
                join product.community_method_card_exposures exposure
                  on exposure.version_id = cast(:card_version_id as uuid)
                 and exposure.learner_user_id = attempt.user_id
                left join product.community_method_card_validation_bindings validation
                  on validation.version_id = exposure.version_id
                where attempt.user_id = cast(:learner_user_id as uuid)
                  and attempt.item_attempt_id = :question_attempt_id
                  and attempt.content_version_id = :question_version_id
                  and attempt.created_at >= exposure.opted_in_at
                """
            ),
            {
                "learner_user_id": learner_user_id,
                "question_attempt_id": question_attempt_id,
                "question_version_id": question_version_id,
                "card_version_id": card_version_id,
            },
        ).mappings().one_or_none()
        if row is not None:
            independent = (
                row["canonical_question_id"] is None
                or str(row["canonical_question_id"]) != str(row["question_id"])
            )
            return QuestionEvidence(
                independent=independent,
                new_for_learner=bool(row["new_for_learner"]),
                correct_before=None,
                duration_before_ms=None,
                evidence_refs=(
                    f"xingce-attempt:{row['attempt_record_id']}",
                    f"community-exposure:{card_version_id}:{learner_user_id}",
                ),
            )
        essay = self._session.execute(
            text(
                """
                select verification.verification_version_id,
                       verification.source_task_version_id,
                       verification.transfer_task_version_id,
                       exposure.opted_in_at, verification.generated_at
                from learning.essay_transfer_verification_versions verification
                join product.community_method_card_exposures exposure
                  on exposure.version_id = cast(:card_version_id as uuid)
                 and exposure.learner_user_id = verification.user_id
                where verification.user_id = cast(:learner_user_id as uuid)
                  and verification.transfer_attempt_id::text = :question_attempt_id
                  and verification.transfer_task_version_id::text = :question_version_id
                  and verification.generated_at >= exposure.opted_in_at
                  and not exists (
                    select 1
                    from learning.essay_transfer_verification_versions successor
                    where successor.supersedes_verification_version_id =
                          verification.verification_version_id
                  )
                order by verification.generated_at desc,
                         verification.verification_version_id desc
                limit 1
                """
            ),
            {
                "learner_user_id": learner_user_id,
                "question_attempt_id": question_attempt_id,
                "question_version_id": question_version_id,
                "card_version_id": card_version_id,
            },
        ).mappings().one_or_none()
        if essay is None:
            raise CommunityMethodCardError("question_evidence_not_found")
        return QuestionEvidence(
            independent=(
                essay["source_task_version_id"] != essay["transfer_task_version_id"]
            ),
            new_for_learner=True,
            correct_before=None,
            duration_before_ms=None,
            evidence_refs=(
                f"essay-transfer-verification:{essay['verification_version_id']}",
                f"community-exposure:{card_version_id}:{learner_user_id}",
            ),
        )


class SqlActorClusterAuthority:
    def __init__(
        self,
        session: Session,
        authenticated_session: AuthenticatedSessionContext,
        *,
        policy_version: str,
    ) -> None:
        self._session = session
        self._authenticated_session = authenticated_session
        self._policy_version = policy_version

    def eligible(
        self, *, author_user_id: str, learner_user_id: str, device_cluster_hash: str
    ) -> bool:
        del device_cluster_hash
        return not self._same_device(author_user_id, learner_user_id)

    def decide(
        self,
        *,
        author_user_id: str,
        learner_user_id: str,
        session_evidence: ServerSessionEvidence,
        decided_at: Any,
    ) -> ActorClusterDecision:
        same_device = self._same_device(author_user_id, learner_user_id)
        cluster_payload = {
            "authorUserId": author_user_id,
            "learnerUserId": learner_user_id,
            "learnerDeviceSha256": _sha256(self._authenticated_session.device_id),
            "sameAuthenticatedDevice": same_device,
            "sessionEvidenceSha256": session_evidence.session_evidence_sha256,
            "decidedAt": str(decided_at),
        }
        return ActorClusterDecision(
            actor_cluster_hash=_fingerprint(cluster_payload),
            policy_version=self._policy_version,
            eligible=not same_device,
            reason_code="same_authenticated_device" if same_device else None,
            snapshot=cluster_payload,
            evidence_refs=(
                f"auth-session:{self._authenticated_session.session_id}",
                f"session-evidence:{session_evidence.session_evidence_sha256}",
            ),
        )

    def _same_device(self, author_user_id: str, learner_user_id: str) -> bool:
        if author_user_id == learner_user_id:
            return True
        return bool(
            self._session.execute(
                text(
                    """
                    select exists (
                      select 1 from learner.app_sessions author_session
                      where author_session.user_id = cast(:author_user_id as uuid)
                        and author_session.device_id = :device_id
                    )
                    """
                ),
                {
                    "author_user_id": author_user_id,
                    "device_id": self._authenticated_session.device_id,
                },
            ).scalar_one()
        )


class SqlReportEvidenceAuthority:
    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        *,
        reporter_user_id: str,
        card_version_id: str,
        evidence_locator: str,
    ) -> tuple[str, ...]:
        parts = evidence_locator.split(":")
        if len(parts) == 3 and parts[0] == "xingce-attempt":
            row = self._session.execute(
                text(
                    """
                    select attempt.attempt_record_id, assessment.assessment_id
                    from learning.xingce_attempt_records attempt
                    join learning.xingce_assessments assessment
                      on assessment.attempt_record_id = attempt.attempt_record_id
                     and assessment.user_id = attempt.user_id
                    where attempt.user_id = cast(:reporter_user_id as uuid)
                      and attempt.item_attempt_id = :attempt_id
                      and attempt.content_version_id = :version_id
                    """
                ),
                {
                    "reporter_user_id": reporter_user_id,
                    "attempt_id": parts[1],
                    "version_id": parts[2],
                },
            ).mappings().one_or_none()
            if row is not None:
                return (
                    f"xingce-attempt:{row['attempt_record_id']}",
                    f"xingce-assessment:{row['assessment_id']}",
                    f"community-card-version:{card_version_id}",
                )
        if len(parts) == 3 and parts[0] == "essay-transfer":
            row = self._session.execute(
                text(
                    """
                    select verification_version_id, transfer_attempt_id
                    from learning.essay_transfer_verification_versions
                    where user_id = cast(:reporter_user_id as uuid)
                      and transfer_attempt_id::text = :verification_id
                      and transfer_task_version_id::text = :task_version_id
                      and not exists (
                        select 1
                        from learning.essay_transfer_verification_versions successor
                        where successor.supersedes_verification_version_id =
                              learning.essay_transfer_verification_versions.verification_version_id
                      )
                    order by generated_at desc, verification_version_id desc
                    limit 1
                    """
                ),
                {
                    "reporter_user_id": reporter_user_id,
                    "verification_id": parts[1],
                    "task_version_id": parts[2],
                },
            ).mappings().one_or_none()
            if row is not None:
                return (
                    f"essay-transfer-verification:{row['verification_version_id']}",
                    f"essay-attempt:{row['transfer_attempt_id']}",
                    f"community-card-version:{card_version_id}",
                )
        raise CommunityMethodCardError("report_evidence_invalid")


class SqlReviewDimensionChecker:
    def __init__(
        self,
        session: Session,
        *,
        dimension: ReviewDimension,
        policy_version: str,
        rules: Mapping[str, Any],
    ) -> None:
        self._session = session
        self._dimension = dimension
        self._policy_version = policy_version
        self._rules = rules

    def check(self, subject: Any) -> ReviewCheck:
        if self._dimension == ReviewDimension.DUPLICATE:
            duplicate = self._session.execute(
                text(
                    """
                    select version_id from product.community_method_card_versions version
                    join product.community_method_cards card using (card_id)
                    where version.source_text_sha256 = :source_hash
                      and version.version_id <> cast(:version_id as uuid)
                      and card.state not in ('rejected', 'retired')
                    limit 1
                    """
                ),
                {"source_hash": subject.source_text_sha256, "version_id": subject.version_id},
            ).scalar_one_or_none()
            return self._result(
                ReviewCheckOutcome.NEEDS_REVISION if duplicate else ReviewCheckOutcome.PASSED,
                "exact_private_source_duplicate" if duplicate else "no_exact_source_duplicate",
                [f"community-card-version:{duplicate}"] if duplicate else [],
            )
        if self._dimension == ReviewDimension.COPYRIGHT:
            allowed = subject.rights_declaration.value in {
                "original",
                "authorized_derivative",
            }
            return self._result(
                ReviewCheckOutcome.PASSED if allowed else ReviewCheckOutcome.FAILED,
                "rights_declared" if allowed else "rights_prohibit_publication",
                [f"rights:{subject.rights_declaration.value}"],
            )
        if self._dimension == ReviewDimension.SAFETY:
            blocked_patterns = self._rules.get("blockedPatterns")
            if not isinstance(blocked_patterns, list) or not all(
                isinstance(item, str) and item for item in blocked_patterns
            ):
                raise CommunityMethodCardError("automated_review_policy_invalid")
            content = "\n".join(
                [subject.title, subject.recognition, *subject.method_steps]
            )
            match = next(
                (pattern for pattern in blocked_patterns if re.search(pattern, content, re.I)),
                None,
            )
            return self._result(
                ReviewCheckOutcome.FAILED if match else ReviewCheckOutcome.PASSED,
                "blocked_content_pattern" if match else "safety_rules_passed",
                [f"policy:{self._policy_version}"],
            )
        if self._dimension == ReviewDimension.KNOWLEDGE:
            supported = subject.validation_scope != "general_method"
            return self._result(
                ReviewCheckOutcome.PASSED if supported else ReviewCheckOutcome.NEEDS_REVISION,
                "knowledge_version_bound" if supported else "knowledge_evidence_missing",
                [subject.knowledge_node_version_ref],
            )
        consistent = bool(subject.answer_evidence_refs) and (
            subject.validation_scope != "general_method"
        )
        return self._result(
            ReviewCheckOutcome.PASSED if consistent else ReviewCheckOutcome.NEEDS_REVISION,
            "canonical_answer_evidence_bound"
            if consistent
            else "canonical_answer_evidence_missing",
            list(subject.answer_evidence_refs),
        )

    def _result(
        self,
        outcome: ReviewCheckOutcome,
        reason_code: str,
        evidence_refs: list[str],
    ) -> ReviewCheck:
        return ReviewCheck(
            dimension=self._dimension,
            outcome=outcome,
            reasonCode=reason_code,
            evidenceRefs=[f"review-policy:{self._policy_version}", *evidence_refs],
        )


class RewardMilestoneSink:
    def __init__(self, session: Session) -> None:
        self._service = CommunityRewardService(SqlCommunityRewardRepository(session))

    def append(self, event: MilestoneQualified) -> None:
        self._service.evaluate_milestone(event_id=UUID(event.event_id))


def build_community_method_card_service(
    *,
    session: Session,
    settings: Settings,
    authenticated_session: AuthenticatedSessionContext,
) -> CommunityMethodCardService:
    repository = CommunityMethodCardRepository(
        session, environment=settings.environment
    )
    candidate_resource = repository.published_policy_resource("candidate_generation")
    review_resource = repository.published_policy_resource("automated_review")
    report_resource = repository.published_policy_resource("report_decision")
    effect_resource = repository.published_effect_policy()
    if None in (candidate_resource, review_resource, report_resource, effect_resource):
        raise CommunityMethodCardError("community_policy_unavailable")
    assert candidate_resource is not None
    assert review_resource is not None
    assert report_resource is not None
    assert effect_resource is not None
    review_rules = review_resource["rules"]
    report_rules = report_resource["rules"]
    if not isinstance(review_rules, Mapping) or not isinstance(report_rules, Mapping):
        raise CommunityMethodCardError("community_policy_invalid")
    confirmed_categories = report_rules.get("confirmedCategories")
    if not isinstance(confirmed_categories, list) or not all(
        isinstance(item, str) and item for item in confirmed_categories
    ):
        raise CommunityMethodCardError("report_decision_policy_invalid")
    canonical = SqlCanonicalAttemptAuthority(
        session,
        authenticated_session,
        attestation_policy_version=str(effect_resource["policy_version"]),
    )
    actor_cluster = SqlActorClusterAuthority(
        session,
        authenticated_session,
        policy_version=str(effect_resource["policy_version"]),
    )
    checkers = {
        dimension: SqlReviewDimensionChecker(
            session,
            dimension=dimension,
            policy_version=str(review_resource["policy_version"]),
            rules=review_rules,
        )
        for dimension in REQUIRED_REVIEW_DIMENSIONS
    }
    return CommunityMethodCardService(
        repository,
        candidate_provider=CommunityCandidateHttpProvider(
            settings,
            prompt_version=str(candidate_resource["prompt_version"]),
            model_policy_version=str(candidate_resource["policy_version"]),
            provider_name=str(candidate_resource["provider"]),
            model=str(candidate_resource["model"]),
            provider_release_id=str(candidate_resource["provider_release_id"]),
            output_schema_version=str(candidate_resource["output_schema_version"]),
        ),
        review_engine=FiveDimensionReviewEngine(
            policy_version=str(review_resource["policy_version"]), checkers=checkers
        ),
        trial_eligibility=VersionedTrialEligibility(
            question_verifier=SqlQuestionEvidenceVerifier(session),
            actor_cluster_verifier=actor_cluster,
        ),
        report_policy=VersionedFixedReportPolicy(
            policy_version=str(report_resource["policy_version"]),
            immediate_restriction_categories=frozenset(confirmed_categories),
        ),
        source_binding_authority=SqlSourceBindingAuthority(session),
        validation_binding_authority=SqlValidationBindingAuthority(session),
        canonical_attempt_verifier=canonical,
        session_evidence_resolver=canonical,
        actor_cluster_authority=actor_cluster,
        report_evidence_authority=SqlReportEvidenceAuthority(session),
        milestone_sink=RewardMilestoneSink(session),
    )


def _reflection_text(payload: Mapping[str, Any]) -> str:
    raw_text = payload.get("rawText")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise CommunityMethodCardError("reflection_payload_invalid")
    return raw_text


def _recognition_text(payload: Mapping[str, Any]) -> tuple[str, list[str]]:
    spans = payload.get("textSpans")
    if not isinstance(spans, list) or not spans:
        raise CommunityMethodCardError("recognition_payload_invalid")
    text_parts: list[str] = []
    refs: list[str] = []
    for span in spans:
        if not isinstance(span, Mapping):
            raise CommunityMethodCardError("recognition_payload_invalid")
        span_id = span.get("spanId")
        span_text = span.get("text")
        if not isinstance(span_id, str) or not isinstance(span_text, str):
            raise CommunityMethodCardError("recognition_payload_invalid")
        text_parts.append(span_text)
        refs.append(f"recognition-span:{span_id}")
    return "\n".join(text_parts), refs


def _node_version(node: Mapping[str, Any]) -> str:
    return (
        f"knowledge-map-release:{node['release_version']}"
        f"/node:{node['node_id']}@{node['updated_at'].isoformat()}"
    )


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _fingerprint(value: object) -> str:
    return _sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )
