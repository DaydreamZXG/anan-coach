"""岸岸 / 错因确认 → 中控大脑建议的接线测试（阶段 2-B）。

验证三个行为：
1. 岸岸对话（ask v2）触发 maybe_suggest_strategy_safely(on_demand)
2. 错因确认（cause-decision）触发 maybe_suggest_strategy_safely(cause_confirmed)
3. 大脑建议失败静默降级，不阻断岸岸回答 / 错因确认

另测 maybe_suggest_strategy_safely 本身：成功落库、provider 失败静默返回 None。
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.learning_brain.agent import maybe_suggest_strategy_safely
from tests.test_companion_external import (
    FakeCompanionAIProvider,
    _make_client,
    _seed_profile_and_context,
    _seed_user,
)
from tests.test_planning_routes import _client
from tests.position_release_helpers import seed_active_position_release

NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ask_payload(learner_state_version: str) -> dict:
    return {
        "questionText": "资料分析增长率怎么补？",
        "context": {
            "routeId": "pages/companion/index",
            "workspace": "training",
            "objectRefs": [],
            "taskMode": None,
            "taskStage": None,
            "learnerStateVersion": learner_state_version,
        },
    }


def _seed_position(factory: sessionmaker[Session]) -> None:
    with factory.begin() as session:
        seed_active_position_release(
            session,
            activated_at=NOW,
            database_now=datetime.now(UTC),
            rule_set_version="brain-trigger-rules",
            position_data_version="brain-trigger-positions",
        )


def _fetch_state_version(client: TestClient, auth: dict[str, str]) -> str:
    resp = client.get("/api/v2/learning-brain/profile", headers=auth)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["profileVersionId"]


class _RecordingBrain:
    """Record trigger kinds; can raise like a failing provider."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self._fail = fail

    def maybe_suggest_strategy(
        self, session, *, user_id: str, trigger_kind: str, **kwargs
    ):
        del session, user_id, kwargs
        self.calls.append(trigger_kind)
        if self._fail:
            from app.domains.learning_brain.provider import (
                LearningBrainProviderError,
            )

            raise LearningBrainProviderError(
                "provider_http_503", "provider down", retryable=True
            )
        return "suggestion-recorded"


def _install_ask_trigger(monkeypatch, brain: _RecordingBrain) -> None:
    """替换 agent 的默认 service 工厂为 fake，保持 safely 的降级语义真实。"""
    from app.domains.learning_brain import agent as agent_module

    monkeypatch.setattr(
        agent_module,
        "LearningBrainAgentService",
        lambda *args, **kwargs: brain,
    )


def _install_cause_trigger(monkeypatch, brain: _RecordingBrain) -> None:
    _install_ask_trigger(monkeypatch, brain)


def _seed_ask_user(factory: sessionmaker[Session], *, token: str) -> str:
    user_id = _seed_user(factory, token=token)
    _seed_position(factory)
    _seed_profile_and_context(factory, user_id=user_id)
    return user_id


def test_ask_trigger_after_profile_success(
    db_session_factory: sessionmaker[Session],
    monkeypatch,
) -> None:
    """岸岸对话触发大脑建议（on_demand）。"""
    brain = _RecordingBrain()
    _install_ask_trigger(monkeypatch, brain)
    token = "brain-ask-trigger"
    _seed_ask_user(db_session_factory, token=token)
    client = _make_client(db_session_factory, provider=FakeCompanionAIProvider())
    state_version = _fetch_state_version(client, _auth(token))
    resp = client.post(
        "/api/v2/companion/ask",
        headers={**_auth(token), "Idempotency-Key": "brain-ask-trigger-key"},
        json=_ask_payload(state_version),
    )
    assert resp.status_code == 200, resp.text
    assert brain.calls == ["on_demand"]


def test_ask_failure_does_not_block_answer(
    db_session_factory: sessionmaker[Session],
    monkeypatch,
) -> None:
    """大脑失败静默降级：岸岸仍正常回答，不抛错。"""
    brain = _RecordingBrain(fail=True)
    _install_ask_trigger(monkeypatch, brain)
    token = "brain-ask-fail"
    _seed_ask_user(db_session_factory, token=token)
    client = _make_client(db_session_factory, provider=FakeCompanionAIProvider())
    state_version = _fetch_state_version(client, _auth(token))
    resp = client.post(
        "/api/v2/companion/ask",
        headers={**_auth(token), "Idempotency-Key": "brain-ask-fail-key"},
        json=_ask_payload(state_version),
    )
    assert resp.status_code == 200, resp.text
    assert brain.calls == ["on_demand"]


def test_cause_decision_trigger(
    db_session_factory: sessionmaker[Session],
    monkeypatch,
) -> None:
    """错因确认触发大脑建议（cause_confirmed）。"""
    from tests.test_xingce_rescue import (
        _seed_rescue_learner,
        _seed_rescue_release,
        _run_attempt,
    )
    from tests.test_xingce_cause_agent_workflow import (
        SequenceProvider,
        _reflect,
        _run_one,
    )

    _seed_rescue_release(db_session_factory)
    token = "brain-cause-trigger"
    _seed_rescue_learner(db_session_factory, token=token)
    client = _client(factory=db_session_factory)
    auth = _auth(token)
    submitted, _ = _run_attempt(
        db_session_factory, client, auth, token=token, choices=["D", "B", "C"]
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="brain-cause-reflection")
    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    view = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}", headers=auth
    ).json()["data"]
    candidate = view["review"]["aiCandidate"]
    brain = _RecordingBrain()
    _install_cause_trigger(monkeypatch, brain)
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "brain-cause-decision"},
        json={
            "expectedRevision": view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text
    assert brain.calls == ["cause_confirmed"]


def test_cause_decision_failure_does_not_block(
    db_session_factory: sessionmaker[Session],
    monkeypatch,
) -> None:
    """大脑失败静默降级：错因确认仍成功返回。"""
    from tests.test_xingce_rescue import (
        _seed_rescue_learner,
        _seed_rescue_release,
        _run_attempt,
    )
    from tests.test_xingce_cause_agent_workflow import (
        SequenceProvider,
        _reflect,
        _run_one,
    )

    _seed_rescue_release(db_session_factory)
    token = "brain-cause-fail"
    _seed_rescue_learner(db_session_factory, token=token)
    client = _client(factory=db_session_factory)
    auth = _auth(token)
    submitted, _ = _run_attempt(
        db_session_factory, client, auth, token=token, choices=["D", "B", "C"]
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="brain-cause-fail-reflection")
    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    view = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}", headers=auth
    ).json()["data"]
    candidate = view["review"]["aiCandidate"]
    brain = _RecordingBrain(fail=True)
    _install_cause_trigger(monkeypatch, brain)
    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "brain-cause-fail-decision"},
        json={
            "expectedRevision": view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text
    assert brain.calls == ["cause_confirmed"]


def _session(factory: sessionmaker[Session]) -> Session:
    return factory()


def test_maybe_suggest_strategy_safely_success(
    db_session_factory: sessionmaker[Session],
) -> None:
    """safely 包装：成功时返回结果并 commit。"""
    user_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), 'safely-success')"
            ),
            {"user_id": user_id},
        )

    class _FakeAgent:
        def maybe_suggest_strategy(
            self, session, *, user_id: str, trigger_kind: str, **kwargs
        ):
            del session, user_id, trigger_kind, kwargs
            return "result"

    result = maybe_suggest_strategy_safely(
        _session(db_session_factory),
        user_id=user_id,
        trigger_kind="on_demand",
        service=_FakeAgent(),  # type: ignore[arg-type]
    )
    assert result == "result"


def test_maybe_suggest_strategy_safely_silent_on_error(
    db_session_factory: sessionmaker[Session],
) -> None:
    """safely 包装：provider 失败静默返回 None，不抛错。"""
    user_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), 'safely-error')"
            ),
            {"user_id": user_id},
        )

    class _FailingAgent:
        def maybe_suggest_strategy(
            self, session, *, user_id: str, trigger_kind: str, **kwargs
        ):
            del session, user_id, trigger_kind, kwargs
            from app.domains.learning_brain.provider import (
                LearningBrainProviderError,
            )

            raise LearningBrainProviderError(
                "provider_http_503", "down", retryable=True
            )

    result = maybe_suggest_strategy_safely(
        _session(db_session_factory),
        user_id=user_id,
        trigger_kind="on_demand",
        service=_FailingAgent(),  # type: ignore[arg-type]
    )
    assert result is None
