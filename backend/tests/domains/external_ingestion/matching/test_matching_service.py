from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.contracts.external_learning.models import (
    CandidateState,
    RecognitionKind,
    RecognitionRevision,
)
from app.domains.external_ingestion.matching.errors import (
    AIAnswerNotApproved,
    MatchIdempotencyConflict,
    MatchTransitionError,
    MatchVersionConflict,
)
from app.domains.external_ingestion.matching.models import (
    AuthorizationDecision,
    AIAnswerApproval,
    IndexedContent,
    MatchingPolicy,
    MatchQuery,
)
from app.domains.external_ingestion.matching.service import MatchingService


NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)
OWNER = UUID("00000000-0000-0000-0000-000000000101")
OTHER = UUID("00000000-0000-0000-0000-000000000202")
ARTIFACT = UUID("00000000-0000-0000-0000-000000000301")
RECOGNITION = UUID("00000000-0000-0000-0000-000000000401")


class Repo:
    def __init__(self) -> None:
        self.versions = {}
        self.locks: list[str] = []

    def lock(self, *, key):
        self.locks.append(key)

    def version(self, *, content_match_version_id):
        return self.versions.get(content_match_version_id)

    def head(self, *, content_match_id, owner_user_id):
        items = [
            value
            for value in self.versions.values()
            if value.content_match_id == content_match_id
            and value.owner_user_id == owner_user_id
        ]
        return max(items, key=lambda value: value.version_no, default=None)

    def insert(self, version):
        if version.content_match_version_id in self.versions:
            raise AssertionError("duplicate content-match version")
        self.versions[version.content_match_version_id] = version


class Catalog:
    def __init__(self, *items: IndexedContent) -> None:
        self.items = {item.content_version_id: item for item in items}

    def search(self, *, query, maximum_candidates):
        return tuple(self.items.values())[:maximum_candidates]

    def get(self, *, content_version_id):
        return self.items.get(content_version_id)


class Authorization:
    def __init__(
        self, *, deny: set[str] | None = None, current_snapshots: bool = True
    ) -> None:
        self.deny = deny or set()
        self.current_snapshots = current_snapshots
        self.purposes: list[str] = []

    def authorize(self, *, owner_user_id, content, purpose):
        self.purposes.append(purpose)
        if content.content_version_id in self.deny:
            return AuthorizationDecision(allowed=False, reason="license_default_deny")
        if content.distribution_class == "account_private":
            allowed = content.owner_user_id == owner_user_id
        else:
            allowed = content.cross_user_match_allowed
        return AuthorizationDecision(
            allowed=allowed,
            reason="explicit_grant" if allowed else "account_isolation",
            snapshotRef=(
                f"license-snapshot:{content.content_version_id}" if allowed else None
            ),
        )

    def snapshot_current(self, *, snapshot_ref):
        return self.current_snapshots


class RecognitionAuthority:
    def __init__(self, *, current=True):
        self.current = current

    def is_current(self, *, revision):
        return self.current


class AIProvider:
    def __init__(self, *, release: str = "ai-release-approved") -> None:
        self.provider_release = release
        self.calls = 0

    def answer(self, *, query, grounding, idempotency_key):
        self.calls += 1
        return f"基于 {grounding.content_version_id} 的候选解析"


def policy(**updates) -> MatchingPolicy:
    values = {
        "policyVersion": "mixed-match@2026-07-15",
        "similarThreshold": 0.75,
        "ambiguityMargin": 0.05,
        "textWeight": 0.8,
        "visualWeight": 0.2,
        "essayMinPromptChars": 20,
        "essayRequireMaterial": True,
        "maximumCandidates": 10,
    }
    values.update(updates)
    return MatchingPolicy.model_validate(values)


def ai_approval() -> AIAnswerApproval:
    return AIAnswerApproval(
        policyVersion="ai-answer-private@2026-07",
        provider="approved-ai-vendor",
        providerRelease="ai-release-approved",
        dataRegion="mainland_china",
        dpaEvidenceRef="dpa:ai:2026",
        securityReviewRef="security:ai:2026-07",
        approvalReceiptRef="approval:ai:2026-07",
        validFrom=NOW - timedelta(days=1),
        validUntil=NOW + timedelta(days=1),
    )


def content(
    content_version_id: str,
    *,
    prompt: str = "下列关于行政许可的说法正确的是",
    visual: str = "1" * 64,
    distribution_class: str = "licensed_transferable",
    owner_user_id: UUID | None = None,
    cross_user: bool = True,
    answer: str | None = "答案 A，依法可以撤回行政许可。",
) -> IndexedContent:
    return IndexedContent(
        contentVersionId=content_version_id,
        track="xingce",
        promptText=prompt,
        optionTexts=("A. 可以撤回", "B. 永远不得撤回"),
        visualFingerprints=(visual,),
        referenceAnswer=answer,
        solutionExplanation="依据信赖保护原则处理。",
        distributionClass=distribution_class,
        ownerUserId=owner_user_id,
        crossUserMatchAllowed=cross_user,
    )


def recognition(*, state=CandidateState.CONFIRMED, owner=OWNER) -> RecognitionRevision:
    return RecognitionRevision(
        recognitionRevisionId=RECOGNITION,
        artifactVersionId=ARTIFACT,
        ownerUserId=owner,
        revisionNo=2,
        recognitionKind=RecognitionKind.PRINTED_OCR,
        state=state,
        processorPolicyVersion="ocr-private-cn@2026-07",
        processorRoute="private-ocr-primary",
        inputSha256="2" * 64,
        outputSha256="3" * 64,
        candidatePayload={"schemaVersion": "test-only"},
        confidence=0.99,
        supersedesRecognitionRevisionId=UUID(
            "00000000-0000-0000-0000-000000000400"
        ),
        expiresAt=NOW + timedelta(days=5),
        confirmedAt=NOW if state == CandidateState.CONFIRMED else None,
        createdAt=NOW,
    )


def query(*, prompt="下列关于行政许可的说法正确的是", visual="1" * 64):
    return MatchQuery(
        track="xingce",
        promptText=prompt,
        optionTexts=("A. 可以撤回", "B. 永远不得撤回"),
        visualFingerprints=(visual,),
    )


def service(
    *items,
    repo=None,
    authorization=None,
    recognition_authority=None,
    policy_value=None,
):
    target_repo = repo or Repo()
    return (
        MatchingService(
            repository=target_repo,
            catalog=Catalog(*items),
            authorization=authorization or Authorization(),
            recognition_authority=recognition_authority or RecognitionAuthority(),
            policy=policy_value or policy(),
            clock=lambda: NOW,
        ),
        target_repo,
    )


def test_exact_text_and_visual_match_stays_candidate_until_user_confirms() -> None:
    matching, _ = service(content("question-v1"))

    candidate, evaluation = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=query(),
        idempotency_key="match-1",
    )

    assert candidate.state == "candidate"
    assert candidate.confirmed_content_version_id is None
    assert evaluation.requires_user_confirmation is True
    assert evaluation.ambiguity_reason == "single_exact"
    assert evaluation.candidates[0].match_kind == "exact"
    assert evaluation.candidates[0].score == 1


def test_unconfirmed_or_expired_recognition_never_enters_matching() -> None:
    matching, _ = service(content("question-v1"))

    with pytest.raises(MatchTransitionError, match="user-confirmed"):
        matching.evaluate(
            owner_user_id=OWNER,
            recognition=recognition(state=CandidateState.CANDIDATE),
            query=query(),
            idempotency_key="candidate-recognition",
        )

    superseded_matching, _ = service(
        content("question-v1"),
        recognition_authority=RecognitionAuthority(current=False),
    )
    with pytest.raises(MatchTransitionError, match="superseded"):
        superseded_matching.evaluate(
            owner_user_id=OWNER,
            recognition=recognition(),
            query=query(),
            idempotency_key="superseded-recognition",
        )
    expired = recognition().model_copy(update={"expires_at": NOW})
    with pytest.raises(MatchTransitionError, match="current"):
        matching.evaluate(
            owner_user_id=OWNER,
            recognition=expired,
            query=query(),
            idempotency_key="expired-recognition",
        )


def test_private_content_from_another_account_and_unlicensed_catalog_are_excluded() -> None:
    other_private = content(
        "other-private",
        distribution_class="account_private",
        owner_user_id=OTHER,
        cross_user=False,
    )
    licensed_denied = content("licensed-denied")
    own_private = content(
        "own-private",
        distribution_class="account_private",
        owner_user_id=OWNER,
        cross_user=False,
    )
    matching, _ = service(
        other_private,
        licensed_denied,
        own_private,
        authorization=Authorization(deny={"licensed-denied"}),
    )

    _, evaluation = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=query(),
        idempotency_key="privacy-match",
    )

    assert [item.content.content_version_id for item in evaluation.candidates] == [
        "own-private"
    ]


def test_score_tie_is_deterministic_and_never_auto_selects() -> None:
    matching, _ = service(content("question-b"), content("question-a"))

    candidate, evaluation = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=query(),
        idempotency_key="tie-match",
    )

    assert evaluation.ambiguity_reason == "score_tie"
    assert [item.content.content_version_id for item in evaluation.candidates] == [
        "question-a",
        "question-b",
    ]
    assert candidate.confirmed_content_version_id is None


def test_similarity_threshold_is_inclusive_but_below_threshold_is_excluded(monkeypatch) -> None:
    scores = {"included": 0.75, "excluded": 0.749999999999}

    def fake_similarity(_query, candidate_text):
        return next(value for key, value in scores.items() if key in candidate_text)

    monkeypatch.setattr(
        "app.domains.external_ingestion.matching.service.text_similarity",
        fake_similarity,
    )
    included = content("included", prompt="included", visual="2" * 64)
    excluded = content("excluded", prompt="excluded", visual="2" * 64)
    matching, _ = service(included, excluded)

    _, evaluation = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=MatchQuery(track="xingce", promptText="query"),
        idempotency_key="threshold",
    )

    assert [item.content.content_version_id for item in evaluation.candidates] == [
        "included"
    ]


@pytest.mark.parametrize(
    "prompt, materials",
    [("过短", ("有材料",)), ("这是一段足够长但没有提供任何材料的申论题目文字", ())],
)
def test_essay_completeness_gate_refuses_incomplete_queries(prompt, materials) -> None:
    essay = IndexedContent(
        contentVersionId="essay-v1",
        track="essay",
        promptText="请根据给定资料分析基层治理数字化面临的问题并提出建议",
        materialTexts=("给定资料一……",),
        distributionClass="licensed_transferable",
        crossUserMatchAllowed=True,
    )
    matching, _ = service(essay)

    candidate, evaluation = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=MatchQuery(track="essay", promptText=prompt, materialTexts=materials),
        idempotency_key=f"essay-{len(prompt)}-{len(materials)}",
    )

    assert candidate.candidates == ()
    assert evaluation.ambiguity_reason == "essay_incomplete"


def test_evaluate_replay_and_changed_request_conflict() -> None:
    provider = content("question-v1")
    matching, repo = service(provider)
    first, first_evaluation = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=query(),
        idempotency_key="replay",
    )
    replay, replay_evaluation = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=query(),
        idempotency_key="replay",
    )

    assert replay == first
    assert replay_evaluation == first_evaluation
    assert len(repo.versions) == 1

    with pytest.raises(MatchIdempotencyConflict):
        matching.evaluate(
            owner_user_id=OWNER,
            recognition=recognition(),
            query=query(prompt="完全不同的一道题"),
            idempotency_key="replay",
        )


def test_stale_competing_match_decision_loses_head_compare_and_swap() -> None:
    matching, _ = service(content("question-v1"))
    candidate, _ = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=query(),
        idempotency_key="decision-source",
    )
    confirmed = matching.confirm(
        owner_user_id=OWNER,
        content_match_version_id=candidate.content_match_version_id,
        expected_version_no=1,
        content_version_id="question-v1",
        idempotency_key="decision-winner",
    )

    assert confirmed.version_no == 2
    with pytest.raises(MatchVersionConflict):
        matching.reject(
            owner_user_id=OWNER,
            content_match_version_id=candidate.content_match_version_id,
            expected_version_no=1,
            idempotency_key="decision-loser",
        )


def test_external_answer_requires_recognition_match_and_answer_confirmation() -> None:
    provider = content("question-v1")
    matching, _ = service(provider)
    candidate, _ = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=query(),
        idempotency_key="answer-match",
    )
    match = matching.confirm(
        owner_user_id=OWNER,
        content_match_version_id=candidate.content_match_version_id,
        expected_version_no=1,
        content_version_id="question-v1",
        idempotency_key="answer-match-confirm",
    )
    answer = matching.stored_answer_candidate(
        owner_user_id=OWNER, recognition=recognition(), match=match
    )
    assert answer is not None

    before = matching.evidence_admission(
        recognition=recognition(), match=match, answer=answer
    )
    confirmed_answer = matching.confirm_answer(
        answer, expected_output_sha256=answer.output_sha256
    )
    after = matching.evidence_admission(
        recognition=recognition(), match=match, answer=confirmed_answer
    )

    assert before.reason == "answer_unconfirmed"
    assert not any(
        (before.may_affect_ability, before.may_affect_cause, before.may_affect_plan)
    )
    assert after.allowed is True
    assert all((after.may_affect_ability, after.may_affect_cause, after.may_affect_plan))


def test_ai_answer_is_last_layer_fails_closed_and_still_needs_user_confirmation() -> None:
    item = content("question-v1", answer=None)
    matching, _ = service(item)
    candidate, _ = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=query(),
        idempotency_key="ai-match",
    )
    match = matching.confirm(
        owner_user_id=OWNER,
        content_match_version_id=candidate.content_match_version_id,
        expected_version_no=1,
        content_version_id="question-v1",
        idempotency_key="ai-match-confirm",
    )

    with pytest.raises(AIAnswerNotApproved):
        matching.ai_answer_candidate(
            owner_user_id=OWNER,
            recognition=recognition(),
            match=match,
            query=query(),
            provider=AIProvider(release="unapproved-release"),
            approval=ai_approval(),
            idempotency_key="ai-answer",
        )
    answer = matching.ai_answer_candidate(
        owner_user_id=OWNER,
        recognition=recognition(),
        match=match,
        query=query(),
        provider=AIProvider(),
        approval=ai_approval(),
        idempotency_key="ai-answer",
    )
    decision = matching.evidence_admission(
        recognition=recognition(), match=match, answer=answer
    )

    assert answer.layer == "ai"
    assert answer.state == "candidate"
    assert decision.allowed is False
    assert decision.reason == "answer_unconfirmed"


def test_revoked_authorization_snapshot_blocks_previously_confirmed_answer_evidence() -> None:
    authorization = Authorization(current_snapshots=False)
    matching, _ = service(content("question-v1"), authorization=authorization)
    candidate, _ = matching.evaluate(
        owner_user_id=OWNER,
        recognition=recognition(),
        query=query(),
        idempotency_key="revoked-match",
    )
    match = matching.confirm(
        owner_user_id=OWNER,
        content_match_version_id=candidate.content_match_version_id,
        expected_version_no=1,
        content_version_id="question-v1",
        idempotency_key="revoked-match-confirm",
    )
    answer = matching.stored_answer_candidate(
        owner_user_id=OWNER, recognition=recognition(), match=match
    )
    assert answer is not None
    answer = matching.confirm_answer(answer, expected_output_sha256=answer.output_sha256)

    decision = matching.evidence_admission(
        recognition=recognition(), match=match, answer=answer
    )

    assert decision.allowed is False
    assert decision.reason == "authorization_not_current"
