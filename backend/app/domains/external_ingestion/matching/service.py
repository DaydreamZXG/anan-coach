from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from app.contracts.external_learning.models import (
    CandidateState,
    ContentMatchVersion,
    LearningTrack,
    MatchCandidate,
    RecognitionRevision,
)
from app.domains.external_ingestion.matching.errors import (
    AIAnswerNotApproved,
    MatchIdempotencyConflict,
    MatchingAuthorizationError,
    MatchNotFound,
    MatchTransitionError,
    MatchVersionConflict,
)
from app.domains.external_ingestion.matching.fingerprint import (
    canonical_fingerprint,
    grouped_text_similarity,
    normalize_text,
    requires_visual_evidence,
    text_similarity,
    visual_similarity,
)
from app.domains.external_ingestion.matching.models import (
    AnswerLayer,
    AIAnswerApproval,
    AuthorizationPurpose,
    EvidenceAdmission,
    IndexedContent,
    MatchEvaluation,
    MatchingPolicy,
    MatchQuery,
    ScoredCandidate,
)
from app.domains.external_ingestion.matching.repository import (
    ContentMatchRepositoryPort,
    ContentSearchPort,
    MatchAuthorizationPort,
    RecognitionAuthorityPort,
)


class AIAnswerProvider(Protocol):
    @property
    def provider_release(self) -> str: ...

    def answer(
        self,
        *,
        query: MatchQuery,
        grounding: IndexedContent,
        idempotency_key: str,
    ) -> str: ...


class MatchingService:
    def __init__(
        self,
        *,
        repository: ContentMatchRepositoryPort,
        catalog: ContentSearchPort,
        authorization: MatchAuthorizationPort,
        recognition_authority: RecognitionAuthorityPort,
        policy: MatchingPolicy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._catalog = catalog
        self._authorization = authorization
        self._recognition_authority = recognition_authority
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        *,
        owner_user_id: UUID,
        recognition: RecognitionRevision,
        question_unit_id: str,
        query: MatchQuery,
        idempotency_key: str,
    ) -> tuple[ContentMatchVersion, MatchEvaluation]:
        now = self._clock()
        self._require_confirmed_recognition(owner_user_id, recognition, now=now)
        input_fingerprint = canonical_fingerprint(
            {
                "recognitionRevisionId": str(recognition.recognition_revision_id),
                "recognitionOutputSha256": recognition.output_sha256,
                "questionUnitId": question_unit_id,
                "policyVersion": self._policy.policy_version,
                "query": query.model_dump(by_alias=True, mode="json"),
            }
        )
        request_hash = canonical_fingerprint(
            {
                "ownerUserId": str(owner_user_id),
                "inputFingerprint": input_fingerprint,
            }
        )
        match_id = uuid5(
            NAMESPACE_URL,
            f"content-match:{owner_user_id}:{recognition.recognition_revision_id}:"
            f"{question_unit_id}",
        )
        version_id = _command_id(owner_user_id, "evaluate", idempotency_key)
        existing = self._repository.version(content_match_version_id=version_id)
        if existing is not None:
            self._assert_match_replay(
                existing, request_hash, expected_input_fingerprint=input_fingerprint
            )
            return existing, self._evaluation_from(existing)

        self._repository.lock(key=f"content-match:{match_id}")
        existing = self._repository.version(content_match_version_id=version_id)
        if existing is not None:
            self._assert_match_replay(
                existing, request_hash, expected_input_fingerprint=input_fingerprint
            )
            return existing, self._evaluation_from(existing)
        if self._repository.head(content_match_id=match_id, owner_user_id=owner_user_id):
            raise MatchVersionConflict(
                "matching already exists for this confirmed recognition revision"
            )

        evaluation = self._score(owner_user_id=owner_user_id, query=query, input_fingerprint=input_fingerprint)
        candidates = tuple(
            MatchCandidate(
                contentVersionId=item.content.content_version_id,
                matchKind=item.match_kind,
                score=item.score,
                evidenceRefs=(
                    *item.evidence_refs,
                    f"request-hash:{request_hash}",
                    f"ambiguity:{evaluation.ambiguity_reason}",
                ),
            )
            for item in evaluation.candidates
        )
        version = ContentMatchVersion(
            contentMatchVersionId=version_id,
            contentMatchId=match_id,
            artifactVersionId=recognition.artifact_version_id,
            recognitionRevisionId=recognition.recognition_revision_id,
            questionUnitId=question_unit_id,
            ownerUserId=owner_user_id,
            versionNo=1,
            state="candidate",
            candidates=candidates,
            evaluationReason=evaluation.ambiguity_reason,
            matchingPolicyVersion=self._policy.policy_version,
            inputFingerprint=input_fingerprint,
            createdAt=now,
        )
        self._repository.insert(version)
        return version, self._evaluation_from(version)

    def confirm(
        self,
        *,
        owner_user_id: UUID,
        content_match_version_id: UUID,
        expected_version_no: int,
        content_version_id: str,
        idempotency_key: str,
    ) -> ContentMatchVersion:
        source = self._owned_match(owner_user_id, content_match_version_id)
        if source.state != "candidate":
            raise MatchTransitionError("only a candidate match can be confirmed")
        if content_version_id not in {
            candidate.content_version_id for candidate in source.candidates
        }:
            raise MatchTransitionError("confirmed content is not an offered candidate")
        content = self._catalog.get(content_version_id=content_version_id)
        if content is None:
            raise MatchNotFound("selected content version no longer exists")
        authorization = self._authorization.authorize(
            owner_user_id=owner_user_id,
            content=content,
            purpose=_match_purpose(content),
        )
        if not authorization.allowed:
            raise MatchingAuthorizationError(
                "selected content is no longer authorized for matching"
            )
        return self._transition(
            owner_user_id=owner_user_id,
            source=source,
            expected_version_no=expected_version_no,
            state="confirmed",
            confirmed_content_version_id=content_version_id,
            operation="confirm",
            idempotency_key=idempotency_key,
        )

    def reject(
        self,
        *,
        owner_user_id: UUID,
        content_match_version_id: UUID,
        expected_version_no: int,
        idempotency_key: str,
    ) -> ContentMatchVersion:
        source = self._owned_match(owner_user_id, content_match_version_id)
        if source.state != "candidate":
            raise MatchTransitionError("only a candidate match can be rejected")
        return self._transition(
            owner_user_id=owner_user_id,
            source=source,
            expected_version_no=expected_version_no,
            state="rejected",
            confirmed_content_version_id=None,
            operation="reject",
            idempotency_key=idempotency_key,
        )

    def stored_answer_candidate(
        self,
        *,
        owner_user_id: UUID,
        recognition: RecognitionRevision,
        match: ContentMatchVersion,
    ) -> AnswerLayer | None:
        self._require_confirmed_lineage(owner_user_id, recognition, match)
        assert match.confirmed_content_version_id is not None
        content = self._catalog.get(
            content_version_id=match.confirmed_content_version_id
        )
        if content is None or not content.reference_answer:
            return None
        decision = self._authorization.authorize(
            owner_user_id=owner_user_id,
            content=content,
            purpose="reference_answer",
        )
        if not decision.allowed or decision.snapshot_ref is None:
            raise MatchingAuthorizationError("stored answer use is no longer authorized")
        selected = next(
            candidate
            for candidate in match.candidates
            if candidate.content_version_id == match.confirmed_content_version_id
        )
        return AnswerLayer(
            layer=selected.match_kind,
            state="candidate",
            answerText=content.reference_answer,
            contentVersionId=content.content_version_id,
            recognitionRevisionId=recognition.recognition_revision_id,
            contentMatchVersionId=match.content_match_version_id,
            authorizationSnapshotRef=decision.snapshot_ref,
            outputSha256=sha256(content.reference_answer.encode("utf-8")).hexdigest(),
        )

    def matched_content(
        self,
        *,
        owner_user_id: UUID,
        recognition: RecognitionRevision,
        match: ContentMatchVersion,
    ) -> IndexedContent:
        """Return the confirmed catalog snapshot instead of OCR-derived display text."""

        self._require_confirmed_lineage(owner_user_id, recognition, match)
        assert match.confirmed_content_version_id is not None
        content = self._catalog.get(
            content_version_id=match.confirmed_content_version_id
        )
        if content is None:
            raise MatchNotFound("confirmed content version no longer exists")
        decision = self._authorization.authorize(
            owner_user_id=owner_user_id,
            content=content,
            purpose=_match_purpose(content),
        )
        if not decision.allowed:
            raise MatchingAuthorizationError(
                "confirmed content is no longer authorized for display"
            )
        return content

    def ai_answer_candidate(
        self,
        *,
        owner_user_id: UUID,
        recognition: RecognitionRevision,
        match: ContentMatchVersion,
        query: MatchQuery,
        provider: AIAnswerProvider,
        approval: AIAnswerApproval,
        idempotency_key: str,
    ) -> AnswerLayer:
        self._require_confirmed_lineage(owner_user_id, recognition, match)
        if not approval.permits(
            provider_release=provider.provider_release, at=self._clock()
        ):
            raise AIAnswerNotApproved("AI answer processor is not release-approved")
        assert match.confirmed_content_version_id is not None
        grounding = self._catalog.get(
            content_version_id=match.confirmed_content_version_id
        )
        if grounding is None:
            raise MatchNotFound("confirmed grounding content no longer exists")
        decision = self._authorization.authorize(
            owner_user_id=owner_user_id,
            content=grounding,
            purpose="ai_grounding",
        )
        if not decision.allowed or decision.snapshot_ref is None:
            raise MatchingAuthorizationError("AI grounding is not currently authorized")
        answer = provider.answer(
            query=query, grounding=grounding, idempotency_key=idempotency_key
        ).strip()
        if not answer:
            raise AIAnswerNotApproved("AI answer processor returned an empty answer")
        return AnswerLayer(
            layer="ai",
            state="candidate",
            answerText=answer,
            recognitionRevisionId=recognition.recognition_revision_id,
            contentMatchVersionId=match.content_match_version_id,
            authorizationSnapshotRef=decision.snapshot_ref,
            processorPolicyVersion=approval.policy_version,
            outputSha256=sha256(answer.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def confirm_answer(answer: AnswerLayer, *, expected_output_sha256: str) -> AnswerLayer:
        if answer.output_sha256 != expected_output_sha256:
            raise MatchVersionConflict("answer changed before confirmation")
        return answer.model_copy(update={"state": "confirmed"})

    def evidence_admission(
        self,
        *,
        recognition: RecognitionRevision,
        match: ContentMatchVersion,
        answer: AnswerLayer,
    ) -> EvidenceAdmission:
        allowed = False
        if recognition.state != CandidateState.CONFIRMED:
            reason = "recognition_unconfirmed"
        elif match.state != "confirmed":
            reason = "match_unconfirmed"
        elif answer.state != "confirmed":
            reason = "answer_unconfirmed"
        elif (
            match.recognition_revision_id != recognition.recognition_revision_id
            or answer.recognition_revision_id != recognition.recognition_revision_id
            or answer.content_match_version_id != match.content_match_version_id
            or (
                answer.layer != "ai"
                and answer.content_version_id != match.confirmed_content_version_id
            )
        ):
            reason = "lineage_mismatch"
        elif not self._authorization.snapshot_current(
            snapshot_ref=answer.authorization_snapshot_ref
        ):
            reason = "authorization_not_current"
        else:
            allowed = True
            reason = "confirmed_external_answer"
        return EvidenceAdmission(
            allowed=allowed,
            reason=reason,
            answerOutputSha256=answer.output_sha256,
            mayAffectAbility=allowed,
            mayAffectCause=allowed,
            mayAffectPlan=allowed,
        )

    def _score(
        self, *, owner_user_id: UUID, query: MatchQuery, input_fingerprint: str
    ) -> MatchEvaluation:
        if not self._complete(query):
            return MatchEvaluation(
                inputFingerprint=input_fingerprint,
                candidates=(),
                ambiguityReason="essay_incomplete",
            )
        records = self._catalog.search(
            query=query, maximum_candidates=self._policy.maximum_candidates
        )
        scored: list[ScoredCandidate] = []
        query_text = _query_text(query)
        for content in records:
            if content.track != query.track:
                continue
            if (
                query.visual_feature_hashes
                and requires_visual_evidence(query.prompt_text)
                and not content.visual_feature_hashes
            ):
                continue
            if (
                content.distribution_class == "account_private"
                and content.owner_user_id != owner_user_id
            ):
                continue
            decision = self._authorization.authorize(
                owner_user_id=owner_user_id,
                content=content,
                purpose=_match_purpose(content),
            )
            if not decision.allowed or decision.snapshot_ref is None:
                continue
            candidate_text = _content_text(content)
            text_score = grouped_text_similarity(
                query_prompt=query.prompt_text,
                content_prompt=content.prompt_text,
                query_options=query.option_texts,
                content_options=content.option_texts,
                query_materials=query.material_texts,
                content_materials=content.material_texts,
                scorer=text_similarity,
            )
            visual_score = visual_similarity(
                query.visual_feature_hashes, content.visual_feature_hashes
            )
            has_text_pair = bool(
                normalize_text(query_text) and normalize_text(candidate_text)
            )
            has_visual_pair = bool(
                query.visual_feature_hashes and content.visual_feature_hashes
            )
            if has_text_pair and has_visual_pair:
                score = (
                    (text_score * self._policy.text_weight)
                    + (visual_score * self._policy.visual_weight)
                )
            elif has_visual_pair:
                score = visual_score
            else:
                score = text_score
            score = round(score, 12)
            exact = (
                has_text_pair
                and text_score == self._policy.exact_text_threshold
                and (not has_visual_pair or visual_score == 1)
            )
            if not exact and score < self._policy.similar_threshold:
                continue
            scored.append(
                ScoredCandidate(
                    content=content,
                    matchKind="exact" if exact else "similar",
                    score=1.0 if exact else score,
                    textScore=text_score,
                    visualScore=visual_score,
                    evidenceRefs=(
                        f"recognition-input:{input_fingerprint}",
                        f"authorization:{decision.snapshot_ref}",
                        f"matching-policy:{self._policy.policy_version}",
                        f"text-score:{text_score:.12f}",
                        f"visual-score:{visual_score:.12f}",
                    ),
                )
            )
        scored.sort(key=lambda item: (-item.score, item.content.content_version_id))
        ranked = tuple(scored[: self._policy.maximum_candidates])
        if not ranked:
            candidates: tuple[ScoredCandidate, ...] = ()
            reason = "no_eligible_candidate"
        elif len(ranked) > 1 and (
            ranked[0].score - ranked[1].score <= self._policy.ambiguity_margin
        ):
            candidates = tuple(
                item
                for item in ranked
                if ranked[0].score - item.score <= self._policy.ambiguity_margin
            )
            reason = "score_tie"
        elif ranked[0].match_kind == "exact":
            candidates = (ranked[0],)
            reason = "single_exact"
        else:
            candidates = (ranked[0],)
            reason = "single_similar"
        return MatchEvaluation(
            inputFingerprint=input_fingerprint,
            candidates=candidates,
            ambiguityReason=reason,
        )

    def _complete(self, query: MatchQuery) -> bool:
        if query.track != LearningTrack.ESSAY:
            return bool(normalize_text(query.prompt_text) or query.visual_feature_hashes)
        if len(normalize_text(query.prompt_text)) < self._policy.essay_min_prompt_chars:
            return False
        return not self._policy.essay_require_material or bool(
            normalize_text("".join(query.material_texts))
        )

    def _transition(
        self,
        *,
        owner_user_id: UUID,
        source: ContentMatchVersion,
        expected_version_no: int,
        state: str,
        confirmed_content_version_id: str | None,
        operation: str,
        idempotency_key: str,
    ) -> ContentMatchVersion:
        request_hash = canonical_fingerprint(
            {
                "sourceVersionId": str(source.content_match_version_id),
                "expectedVersionNo": expected_version_no,
                "state": state,
                "confirmedContentVersionId": confirmed_content_version_id,
            }
        )
        version_id = _command_id(owner_user_id, operation, idempotency_key)
        existing = self._repository.version(content_match_version_id=version_id)
        if existing is not None:
            self._assert_match_replay(existing, request_hash)
            return existing
        self._repository.lock(key=f"content-match:{source.content_match_id}")
        existing = self._repository.version(content_match_version_id=version_id)
        if existing is not None:
            self._assert_match_replay(existing, request_hash)
            return existing
        head = self._repository.head(
            content_match_id=source.content_match_id, owner_user_id=owner_user_id
        )
        if (
            head is None
            or head.content_match_version_id != source.content_match_version_id
            or head.version_no != expected_version_no
        ):
            raise MatchVersionConflict("content-match head changed before decision")
        candidates = tuple(
            candidate.model_copy(
                update={
                    "evidence_refs": (
                        *candidate.evidence_refs,
                        f"request-hash:{request_hash}",
                    )
                }
            )
            for candidate in source.candidates
        )
        version = ContentMatchVersion(
            contentMatchVersionId=version_id,
            contentMatchId=source.content_match_id,
            artifactVersionId=source.artifact_version_id,
            recognitionRevisionId=source.recognition_revision_id,
            questionUnitId=source.question_unit_id,
            ownerUserId=owner_user_id,
            versionNo=head.version_no + 1,
            state=state,
            candidates=candidates,
            evaluationReason=source.evaluation_reason,
            confirmedContentVersionId=confirmed_content_version_id,
            matchingPolicyVersion=source.matching_policy_version,
            inputFingerprint=source.input_fingerprint,
            supersedesContentMatchVersionId=source.content_match_version_id,
            createdAt=self._clock(),
        )
        self._repository.insert(version)
        return version

    def _owned_match(
        self, owner_user_id: UUID, content_match_version_id: UUID
    ) -> ContentMatchVersion:
        match = self._repository.version(
            content_match_version_id=content_match_version_id
        )
        if match is None or match.owner_user_id != owner_user_id:
            raise MatchNotFound("content match was not found for this owner")
        return match

    def _require_confirmed_recognition(
        self,
        owner_user_id: UUID, recognition: RecognitionRevision, *, now: datetime
    ) -> None:
        if recognition.owner_user_id != owner_user_id:
            raise MatchNotFound("recognition revision was not found for this owner")
        if recognition.state != CandidateState.CONFIRMED or recognition.expires_at <= now:
            raise MatchTransitionError(
                "only a current, user-confirmed recognition revision can be matched"
            )
        if not self._recognition_authority.is_current(revision=recognition):
            raise MatchTransitionError(
                "superseded recognition revisions cannot enter matching"
            )

    def _require_confirmed_lineage(
        self,
        owner_user_id: UUID,
        recognition: RecognitionRevision,
        match: ContentMatchVersion,
    ) -> None:
        if recognition.owner_user_id != owner_user_id or match.owner_user_id != owner_user_id:
            raise MatchNotFound("external learning lineage was not found for this owner")
        if recognition.state != CandidateState.CONFIRMED or match.state != "confirmed":
            raise MatchTransitionError("recognition and match must both be user-confirmed")
        if match.recognition_revision_id != recognition.recognition_revision_id:
            raise MatchTransitionError("content match does not descend from this recognition")
        if not self._recognition_authority.is_current(revision=recognition):
            raise MatchTransitionError(
                "superseded recognition revisions cannot authorize answer use"
            )

    @staticmethod
    def _assert_match_replay(
        version: ContentMatchVersion,
        request_hash: str,
        *,
        expected_input_fingerprint: str | None = None,
    ) -> None:
        if (
            expected_input_fingerprint is not None
            and version.input_fingerprint != expected_input_fingerprint
        ):
            raise MatchIdempotencyConflict(
                "idempotency key was already used for a different match request"
            )
        marker = f"request-hash:{request_hash}"
        if not version.candidates:
            return
        if not all(marker in candidate.evidence_refs for candidate in version.candidates):
            raise MatchIdempotencyConflict(
                "idempotency key was already used for a different match request"
            )

    def _evaluation_from(self, version: ContentMatchVersion) -> MatchEvaluation:
        scored: list[ScoredCandidate] = []
        for candidate in version.candidates:
            content = self._catalog.get(content_version_id=candidate.content_version_id)
            if content is None:
                continue
            scored.append(
                ScoredCandidate(
                    content=content,
                    matchKind=candidate.match_kind,
                    score=candidate.score,
                    textScore=_score_ref(candidate.evidence_refs, "text-score"),
                    visualScore=_score_ref(candidate.evidence_refs, "visual-score"),
                    evidenceRefs=candidate.evidence_refs,
                )
            )
        return MatchEvaluation(
            inputFingerprint=version.input_fingerprint,
            candidates=tuple(scored),
            ambiguityReason=version.evaluation_reason,
        )


def _query_text(query: MatchQuery) -> str:
    return "\n".join((query.prompt_text, *query.option_texts, *query.material_texts))


def _content_text(content: IndexedContent) -> str:
    return "\n".join(
        (content.prompt_text, *content.option_texts, *content.material_texts)
    )


def _command_id(owner_user_id: UUID, operation: str, idempotency_key: str) -> UUID:
    if not idempotency_key.strip():
        raise ValueError("idempotency key is required")
    return uuid5(NAMESPACE_URL, f"content-match:{owner_user_id}:{operation}:{idempotency_key}")


def _match_purpose(content: IndexedContent) -> AuthorizationPurpose:
    return (
        "owner_private_match"
        if content.distribution_class == "account_private"
        else "cross_user_match"
    )


def _score_ref(refs: tuple[str, ...], name: str) -> float:
    prefix = f"{name}:"
    for ref in refs:
        if ref.startswith(prefix):
            return float(ref.removeprefix(prefix))
    return 0.0
