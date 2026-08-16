from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.session import get_db_session
from app.domains.ability_estimation.schemas import AbilityEstimateCurrent
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.learning_brain.router import get_learning_brain_service
from app.domains.learning_brain.schemas import (
    BrainCandidateSummary,
    BrainDecisionSummary,
    BrainEvidenceRef,
    BrainEvidenceSummary,
    LearningBrainProfileVersion,
    ModuleAbility,
    StrategyDirective,
    StrategyDirectiveEntry,
)
from app.domains.learning_brain.service import compile_learner_coach_context
from app.main import create_app


NOW = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def test_recognition_levels_are_compiled_only_from_versioned_evidence() -> None:
    cases = (
        ({}, 0, "just_met"),
        ({"scoreEvidenceCount": 1}, 0, "initial_hypothesis"),
        ({"taskExecutionCount": 1}, 0, "verifying"),
        ({"xingceMasteryCount": 1}, 1, "understands"),
    )
    for counters, stable_mastery_count, expected in cases:
        context = compile_learner_coach_context(
            _profile(counters=counters, stable_mastery_count=stable_mastery_count),
            now=NOW,
        )

        assert context.recognition.level == expected
        assert context.profile_version_id == context.profile.profile_version_id
        assert context.profile.source_refs[0].owner == "objective_learning"


def test_freshness_is_explicit_for_current_stale_conflicted_and_unavailable() -> None:
    assert compile_learner_coach_context(_profile(), now=NOW).freshness == "unavailable"
    assert (
        compile_learner_coach_context(
            _profile(counters={"scoreEvidenceCount": 1}, readiness="partial"),
            now=NOW,
        ).freshness
        == "current"
    )
    assert (
        compile_learner_coach_context(
            _profile(
                counters={"scoreEvidenceCount": 1},
                readiness="partial",
                valid_until=NOW - timedelta(seconds=1),
            ),
            now=NOW,
        ).freshness
        == "stale"
    )
    assert (
        compile_learner_coach_context(
            _profile(counters={"scoreEvidenceCount": 1}, readiness="conflicted"),
            now=NOW,
        ).freshness
        == "conflicted"
    )


def test_strategy_and_evidence_refs_survive_the_read_only_compilation() -> None:
    directive = StrategyDirective(
        pending=StrategyDirectiveEntry(
            status="awaiting_confirmation",
            decisionType="baseline_guidance",
            scopeType="track",
            scopeKey="xingce",
            rationale="先完成一次行测摸底，再校准当前判断。",
            expiresAt=NOW + timedelta(days=1),
            generatedAt=NOW,
        )
    )
    profile = _profile(counters={"scoreEvidenceCount": 1}, readiness="partial")
    before = profile.model_dump(by_alias=True, mode="json")

    context = compile_learner_coach_context(
        profile,
        strategy_directive=directive,
        now=NOW,
    )

    assert context.strategy_directive.pending is not None
    assert context.strategy_directive.pending.rationale.startswith("先完成")
    assert context.profile.source_refs[0].version_id == "attempt-version-1"
    assert profile.model_dump(by_alias=True, mode="json") == before


def test_context_contract_keeps_profile_and_recognition_version_locked() -> None:
    context = compile_learner_coach_context(
        _profile(counters={"taskExecutionCount": 1}, readiness="partial"),
        now=NOW,
    )

    assert context.contract_version == "learner-coach-context@v1"
    assert context.profile_version_id == context.profile.profile_version_id
    assert context.recognition.level == "verifying"
    assert context.recognition.evidence_count == 1
    assert context.freshness == "current"


def test_coach_context_route_uses_only_the_authenticated_account() -> None:
    user_id = str(uuid4())
    service = _CoachContextServiceStub(
        compile_learner_coach_context(
            _profile(counters={"scoreEvidenceCount": 1}, readiness="partial"),
            now=NOW,
        )
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        learner_key=f"learner:{user_id}",
        display_name="测试用户",
        email=None,
    )
    app.dependency_overrides[get_learning_brain_service] = lambda: service
    session = _SessionStub()

    def session_override():
        yield session

    app.dependency_overrides[get_db_session] = session_override
    client = TestClient(app)

    response = client.get("/api/v2/learning-brain/coach-context")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["contractVersion"] == "learner-coach-context@v1"
    assert service.user_ids == [user_id]
    assert session.commits == 1


class _CoachContextServiceStub:
    def __init__(self, context: object) -> None:
        self.context = context
        self.user_ids: list[str] = []

    def get_coach_context(self, _session: object, *, user_id: str) -> object:
        self.user_ids.append(user_id)
        return self.context


class _SessionStub:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


def _profile(
    *,
    counters: dict[str, int] | None = None,
    stable_mastery_count: int = 0,
    readiness: str | None = None,
    valid_until: datetime | None = None,
) -> LearningBrainProfileVersion:
    values = {
        "scoreEvidenceCount": 0,
        "xingceAttemptCount": 0,
        "xingceMasteryCount": 0,
        "essayMasteryCount": 0,
        "taskExecutionCount": 0,
        "externalAnswerCount": 0,
    }
    values.update(counters or {})
    evidence = BrainEvidenceSummary(**values)
    module = (
        ModuleAbility(
            track="xingce",
            moduleKey="verbal",
            displayName="言语理解与表达",
            readiness="reliable",
            scoreBand=None,
            evidenceCount=1,
            externalEvidenceCount=0,
            correctRate=1,
            averageSeconds=40,
            stableMasteryCount=stable_mastery_count,
            dueCount=0,
            confidence="high",
            freshness="current",
            latestEvidenceAt=NOW,
            conflictCodes=[],
            evidenceRefs=[_evidence_ref()],
        )
        if stable_mastery_count > 0
        else None
    )
    resolved_readiness = readiness or ("unavailable" if evidence.total_count == 0 else "partial")
    return LearningBrainProfileVersion(
        profileVersionId=uuid4(),
        versionNo=1,
        readiness=resolved_readiness,
        ability=AbilityEstimateCurrent(
            estimate=None,
            readiness="unavailable",
            missingComponents=["xingce", "shenlun"],
        ),
        evidenceSummary=evidence,
        candidateSummary=BrainCandidateSummary(
            awaitingConfirmation=0,
            accepted=0,
            rejected=0,
            expired=0,
        ),
        modules=[module] if module is not None else [],
        errorPatterns=[],
        decisionSummary=BrainDecisionSummary(
            activeStrategyCount=0,
            reviewDueCount=0,
        ),
        conflicts=["ability_conflict"] if resolved_readiness == "conflicted" else [],
        sourceRefs=[_evidence_ref()],
        inputVersions={"evidence": "attempt-version-1"},
        inputFingerprint="a" * 64,
        generatedAt=NOW - timedelta(hours=1),
        validUntil=valid_until or (NOW + timedelta(days=1)),
    )


def _evidence_ref() -> BrainEvidenceRef:
    return BrainEvidenceRef(
        owner="objective_learning",
        entityType="practice_attempt",
        entityId="attempt-1",
        versionId="attempt-version-1",
        occurredAt=NOW - timedelta(hours=1),
        role="basis",
    )
