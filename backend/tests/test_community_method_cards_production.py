from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domains.community_method_cards.effects import VersionedTrialEligibility
from app.domains.community_method_cards.moderation import (
    REQUIRED_REVIEW_DIMENSIONS,
    FiveDimensionReviewEngine,
    VersionedFixedReportPolicy,
)
from app.domains.community_method_cards.schemas import (
    DraftCreate,
    ReportCreate,
    TrialComplete,
)
from app.domains.community_method_cards.service import CommunityMethodCardService
from tests.test_community_method_cards import (
    _ActorClusterAuthority,
    _CandidateProvider,
    _CanonicalAttemptVerifier,
    _Checker,
    _ClusterVerifier,
    _MemoryRepository,
    _MilestoneSink,
    _QuestionVerifier,
    _ReportEvidenceAuthority,
    _SessionResolver,
    _SourceBindingAuthority,
    _ValidationBindingAuthority,
)


NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)
AUTHOR_ID = "971f26bd-6ed0-4622-b026-860321f54243"
LEARNER_ID = "9506fa2b-d69c-4e25-a0ec-c6f75b3826a2"
CARD_ID = "ab0fd634-00ce-4f78-b9b0-077cbbe4a544"
VERSION_ID = "ed547a91-e73b-4263-bb4a-b35534986447"
EXPOSURE_ID = "e2f3facf-d86f-4ae4-9677-c5fb861389a1"


def _service(repository: _MemoryRepository, sink: _MilestoneSink) -> CommunityMethodCardService:
    return CommunityMethodCardService(
        repository,
        candidate_provider=_CandidateProvider(),
        review_engine=FiveDimensionReviewEngine(
            policy_version="community-review@fixture-1",
            checkers={
                dimension: _Checker(dimension)
                for dimension in REQUIRED_REVIEW_DIMENSIONS
            },
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
        milestone_sink=sink,
        clock=lambda: NOW,
    )


def _seed_public_trial(repository: _MemoryRepository) -> None:
    repository.cards[CARD_ID] = {
        "card_id": CARD_ID,
        "author_user_id": AUTHOR_ID,
        "track": "xingce",
        "state": "trial",
        "head_version_id": VERSION_ID,
        "state_version": 2,
        "created_at": NOW,
        "updated_at": NOW,
    }
    repository.version_rows[VERSION_ID] = {
        "version_id": VERSION_ID,
        "card_id": CARD_ID,
        "version_no": 2,
        "author_user_id": AUTHOR_ID,
        "base_version_id": "60b8278f-4ab9-46df-8acb-a8219f4379b3",
        "source_type": "text",
        "source_artifact_id": None,
        "source_text_sha256": "a" * 64,
        "private_source_text": "绝不能出现在公开详情里的原始笔记",
        "title": "比例题：不变量优先",
        "recognition": "识别比例关系",
        "method_steps": ["标注不变量", "统一基准", "回代核验"],
        "applicability": ["数量关系"],
        "exclusions": ["直接计算题"],
        "pitfalls": ["量纲不统一"],
        "rights_declaration": "original",
        "author_confirmed_at": NOW,
        "created_at": NOW,
    }
    repository.exposures[EXPOSURE_ID] = {
        "exposure_id": EXPOSURE_ID,
        "card_id": CARD_ID,
        "version_id": VERSION_ID,
        "learner_user_id": LEARNER_ID,
        "applicability_snapshot": ["数量关系"],
        "ranking_policy_version": "community-ranking@fixture-1",
        "opted_in_at": NOW,
    }


def test_v2_attempt_commands_reject_client_claimed_results_and_device_identity() -> None:
    with pytest.raises(ValidationError):
        TrialComplete.model_validate(
            {
                "exposureId": EXPOSURE_ID,
                "questionAttemptId": "attempt-1",
                "questionVersionId": "question-1",
                "correctAfter": True,
                "durationAfterMs": 1,
                "deviceClusterId": "client-device",
            }
        )
    with pytest.raises(ValidationError):
        DraftCreate.model_validate(
            {
                "sourceType": "reflection",
                "sourceText": "复盘",
                "track": "xingce",
                "applicability": [],
                "idempotencyKey": "draft-1",
            }
        )


def test_trial_persists_server_evidence_and_public_detail_is_allow_listed() -> None:
    repository = _MemoryRepository()
    sink = _MilestoneSink()
    _seed_public_trial(repository)
    service = _service(repository, sink)

    receipt = service.complete_trial(
        learner_user_id=LEARNER_ID,
        command=TrialComplete(
            exposureId=EXPOSURE_ID,
            questionAttemptId="attempt-server-owned",
            questionVersionId="question-server-owned",
        ),
    )

    trial = repository.trials[receipt.trial_id]
    assert trial["correct_after"] is True
    assert trial["duration_after_ms"] == 42000
    assert repository.session_evidence[receipt.trial_id]["server_session_id"] == (
        "session:attempt-server-owned"
    )
    assert repository.eligibility_decisions[receipt.trial_id]["decision_snapshot"]

    repository.projections[(VERSION_ID, repository.policy["policy_id"])] = {
        "eligible_trial_count": 3,
        "delayed_verified_count": 1,
        "conservative_score": Decimal("0.812500"),
        "risk_flags": ["delayed_verification_insufficient"],
    }
    detail = service.public_detail(card_version_id=VERSION_ID)
    payload = detail.model_dump(by_alias=True, mode="json")
    assert payload["methodSteps"] == ["标注不变量", "统一基准", "回代核验"]
    assert payload["exclusions"] == ["直接计算题"]
    assert payload["pitfalls"] == ["量纲不统一"]
    assert payload["evidenceCount"] == 3
    assert "privateSourceText" not in payload
    assert "votes" not in payload


def test_report_is_pending_until_independent_decision_and_rewards_reporter() -> None:
    repository = _MemoryRepository()
    sink = _MilestoneSink()
    _seed_public_trial(repository)
    service = _service(repository, sink)

    intake = service.report(
        reporter_user_id=LEARNER_ID,
        command=ReportCreate(
            cardVersionId=VERSION_ID,
            category="unsafe",
            evidenceRef="canonical-attempt:unsafe-1",
        ),
    )
    assert intake.decision == "pending"
    assert repository.cards[CARD_ID]["state"] == "trial"

    decision = service.decide_report(
        report_id=intake.report_id, actor_ref="moderation-worker:fixture"
    )
    assert decision.decision == "confirmed"
    assert decision.beneficiary_user_id == LEARNER_ID
    assert decision.beneficiary_user_id != AUTHOR_ID
    assert repository.cards[CARD_ID]["state"] == "restricted"
    assert sink.events[-1].author_id == LEARNER_ID
