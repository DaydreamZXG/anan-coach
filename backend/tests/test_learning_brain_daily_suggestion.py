from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.learning_brain.agent import (
    LearningBrainAgentError,
    StrategySuggestionResult,
)
from app.domains.learning_brain.router import get_learning_brain_agent_service
from app.domains.learning_brain.schemas import BrainEvidenceRef, InferenceCandidateVersion
from app.main import create_app
from tests.test_learning_brain_agent import (
    NOW,
    _DirectiveProvider,
    _agent,
    _guidance_directive,
    _seed_new_user,
)


class _SessionStub:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _AgentStub:
    def __init__(self, result: StrategySuggestionResult | None = None) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def maybe_suggest_strategy(
        self,
        _session: object,
        *,
        user_id: str,
        trigger_kind: str,
    ) -> StrategySuggestionResult:
        self.calls.append((user_id, trigger_kind))
        if self.result is None:
            raise LearningBrainAgentError("provider unavailable")
        return self.result


def test_daily_route_returns_same_user_candidate_and_commits_enhancement() -> None:
    user_id = str(uuid4())
    candidate = _candidate()
    agent = _AgentStub(
        StrategySuggestionResult(
            candidate=candidate,
            suggestion_id=str(uuid4()),
            trigger_kind="daily_first_entry",
        )
    )
    session = _SessionStub()
    client = _client(user_id=user_id, session=session, agent=agent)

    response = client.post("/api/v2/learning-brain/strategy-suggestions/daily", json={})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "suggested"
    assert data["triggerKind"] == "daily_first_entry"
    assert data["candidate"]["candidateId"] == str(candidate.candidate_id)
    assert data["candidate"]["status"] == "awaiting_confirmation"
    assert agent.calls == [(user_id, "daily_first_entry")]
    assert session.commits == 1
    assert session.rollbacks == 0


def test_daily_route_fails_open_when_agent_is_unavailable() -> None:
    user_id = str(uuid4())
    agent = _AgentStub()
    session = _SessionStub()
    client = _client(user_id=user_id, session=session, agent=agent)

    response = client.post("/api/v2/learning-brain/strategy-suggestions/daily", json={})

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "status": "unavailable",
        "triggerKind": "daily_first_entry",
        "suggestionId": None,
        "skippedReason": "safe_unavailable",
        "candidate": None,
    }
    assert session.commits == 0
    assert session.rollbacks == 1


def test_daily_agent_serializes_concurrent_first_entry_replays(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_new_user(db_session_factory)
    provider = _DirectiveProvider(_guidance_directive())
    service = _agent(provider)

    def trigger() -> StrategySuggestionResult:
        with db_session_factory.begin() as session:
            return service.maybe_suggest_strategy(
                session,
                user_id=user_id,
                trigger_kind="daily_first_entry",
                now=NOW,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _index: trigger(), range(2)))

    assert first.suggestion_id == second.suggestion_id
    assert {first.skipped_reason, second.skipped_reason} == {
        None,
        "already_suggested_today",
    }
    assert len(provider.calls) == 1


def _client(*, user_id: str, session: _SessionStub, agent: _AgentStub) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        learner_key=f"learner:{user_id}",
        display_name="测试用户",
        email=None,
    )

    def session_override():
        yield session

    app.dependency_overrides[get_db_session] = session_override
    app.dependency_overrides[get_learning_brain_agent_service] = lambda: agent
    return TestClient(app)


def _candidate() -> InferenceCandidateVersion:
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    return InferenceCandidateVersion(
        candidate_version_id=uuid4(),
        candidate_id=uuid4(),
        version_no=1,
        inference_type="strategy_directive",
        status="awaiting_confirmation",
        producer_type="agent",
        producer_id="learning-brain-agent@v1",
        value={
            "decisionType": "baseline_guidance",
            "scopeType": "track",
            "scopeKey": "xingce",
            "rationale": "先完成一次行测摸底，再验证当前判断。",
        },
        evidence_refs=[
            BrainEvidenceRef(
                owner="learning-brain",
                entity_type="profile_version",
                entity_id=str(uuid4()),
                version_id=str(uuid4()),
                occurred_at=now,
                role="basis",
            )
        ],
        confidence=0.6,
        requires_user_confirmation=True,
        confirmation_status="pending",
        validation_status="valid",
        policy_version="weekly-strategy-directive@v1",
        valid_from=now,
        expires_at=now + timedelta(days=7),
        input_fingerprint="a" * 64,
        created_at=now,
    )
