from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.model_cost import ModelPriceCard, ModelTokenUsage
from app.domains.xingce_learning.cause_agent.contracts import (
    CandidateReady,
    CauseAgentEvidence,
    CauseAgentOutput,
    FollowupRequired,
)
from app.domains.xingce_learning.cause_agent.provider import CauseAgentProviderResult
from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker
from tests.test_xingce_planning_closure import _submitted_attempt


def test_reflection_worker_candidate_and_confirmation_are_one_audited_chain(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-agent-confirm-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    reflected = _reflect(client, auth, review, key="cause-confirm-reflection")

    assert reflected["state"] == "cause_candidate_processing"
    assert reflected["review"]["aiCandidate"] == {
        "state": "processing",
        "candidateVersionId": None,
        "reason": None,
        "causeCode": None,
        "summary": None,
        "improvement": None,
        "question": None,
        "rationale": None,
        "explanation": None,
        "missingEvidence": [],
        "evidenceRefs": [],
        "limitations": [],
        "quotaState": "not_metered",
        "entitlementPolicyVersion": "included-with-canonical-practice@v1",
    }

    provider = SequenceProvider(["candidate_ready"])
    _run_one(db_session_factory, provider)
    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    )
    assert current.status_code == 200, current.text
    candidate_view = current.json()["data"]
    candidate = candidate_view["review"]["aiCandidate"]
    assert candidate_view["state"] == "cause_decision_required"
    assert candidate["state"] == "candidate_ready"
    assert candidate["causeCode"] == "stem_misread"
    assert candidate["quotaState"] == "not_metered"
    assert "charged" not in candidate

    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "cause-confirm-decision"},
        json={
            "expectedRevision": candidate_view["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "decision": "confirm",
        },
    )
    assert decision.status_code == 200, decision.text
    confirmed = decision.json()["data"]
    assert confirmed["review"]["causeDecision"]["decision"] == "confirm"
    assert confirmed["review"]["causeDecision"]["confirmedCauseVersionId"]

    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from learning.xingce_confirmed_cause_versions")
        ).scalar_one() == 1
        run = session.execute(
            text(
                "select state, model_call_count, tool_call_count "
                "from ai_audit.agent_run_versions "
                "where not exists (select 1 from ai_audit.agent_run_versions newer "
                "where newer.supersedes_run_version_id = agent_run_versions.run_version_id)"
            )
        ).mappings().one()
        assert dict(run) == {
            "state": "candidate_ready",
            "model_call_count": 1,
            "tool_call_count": 4,
        }
        assert session.execute(
            text(
                "select count(*) from ai_audit.agent_tool_call_events "
                "where event_type = 'succeeded'"
            )
        ).scalar_one() == 4


def test_followup_reuses_frozen_evidence_and_cannot_create_extra_tool_calls(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-agent-followup-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="cause-followup-reflection")
    provider = SequenceProvider(["followup_required", "candidate_ready"])
    _run_one(db_session_factory, provider)

    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    candidate = current["review"]["aiCandidate"]
    assert current["state"] == "cause_followup_required"
    assert candidate["state"] == "followup_required"

    followed = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-followup",
        headers={**auth, "Idempotency-Key": "cause-followup-answer"},
        json={
            "expectedRevision": current["review"]["revision"],
            "candidateVersionId": candidate["candidateVersionId"],
            "answer": "我是漏看了题干中“不正确”三个字。",
        },
    )
    assert followed.status_code == 200, followed.text
    assert followed.json()["data"]["state"] == "cause_candidate_processing"
    _run_one(db_session_factory, provider)

    ready = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    assert ready["state"] == "cause_decision_required"
    assert ready["review"]["aiCandidate"]["state"] == "candidate_ready"
    assert provider.followup_answers == [None, "我是漏看了题干中“不正确”三个字。"]

    with db_session_factory() as session:
        current_run = session.execute(
            text(
                "select state, followup_round, model_call_count, tool_call_count "
                "from ai_audit.agent_run_versions run "
                "where not exists (select 1 from ai_audit.agent_run_versions newer "
                "where newer.supersedes_run_version_id = run.run_version_id)"
            )
        ).mappings().one()
        assert dict(current_run) == {
            "state": "candidate_ready",
            "followup_round": 1,
            "model_call_count": 2,
            "tool_call_count": 4,
        }
        assert session.execute(
            text("select count(*) from ai_audit.agent_tool_call_events")
        ).scalar_one() == 12
        assert session.execute(
            text("select count(*) from ai_audit.model_call_ledger")
        ).scalar_one() == 2


def test_reject_and_defer_never_materialize_a_confirmed_cause(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-agent-reject-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    _reflect(client, auth, review, key="cause-reject-reflection")
    _run_one(db_session_factory, SequenceProvider(["candidate_ready"]))
    ready = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    candidate_id = ready["review"]["aiCandidate"]["candidateVersionId"]

    rejected = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "cause-reject-decision"},
        json={
            "expectedRevision": ready["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "reject",
        },
    )
    assert rejected.status_code == 200, rejected.text
    rejected_view = rejected.json()["data"]
    assert rejected_view["review"]["causeDecision"]["confirmedCauseVersionId"] is None

    deferred = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "cause-defer-revision"},
        json={
            "expectedRevision": rejected_view["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "defer",
        },
    )
    assert deferred.status_code == 200, deferred.text
    assert deferred.json()["data"]["review"]["causeDecision"]["decision"] == "defer"
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from learning.xingce_confirmed_cause_versions")
        ).scalar_one() == 0


def test_ungrounded_provider_output_is_published_as_audited_termination(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="cause-agent-termination-token",
        choices=["D", "B", "C"],
    )
    _reflect(
        client,
        auth,
        submitted["review"],
        key="cause-termination-reflection",
    )
    _run_one(db_session_factory, SequenceProvider(["invalid_grounding"]))

    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    ).json()["data"]
    assert current["state"] == "cause_agent_terminated"
    assert current["review"]["aiCandidate"]["state"] == "terminated"
    assert current["review"]["aiCandidate"]["reason"] == (
        "cause_agent_output_not_grounded"
    )
    with db_session_factory() as session:
        run = session.execute(
            text(
                "select state, termination_reason, model_call_count, tool_call_count "
                "from ai_audit.agent_run_versions run "
                "where not exists (select 1 from ai_audit.agent_run_versions newer "
                "where newer.supersedes_run_version_id = run.run_version_id)"
            )
        ).mappings().one()
        assert dict(run) == {
            "state": "terminated",
            "termination_reason": "cause_agent_output_not_grounded",
            "model_call_count": 1,
            "tool_call_count": 4,
        }


class SequenceProvider:
    provider_name = "xingce_cause_provider@local-unreleased"
    provider_release_id = "local-unreleased"
    model = "gemini-3.5-flash"
    prompt_version = "xingce-cause-prompt@v1"
    schema_version = "cause-candidate@v2"

    def __init__(self, outputs: Iterable[str]) -> None:
        self._outputs = iter(outputs)
        self.followup_answers: list[str | None] = []

    def validate_configuration(self) -> None:
        return None

    def generate(
        self,
        *,
        evidence: CauseAgentEvidence,
        followup_answer: str | None,
        idempotency_key: str,
    ) -> CauseAgentProviderResult:
        del idempotency_key
        self.followup_answers.append(followup_answer)
        kind = next(self._outputs)
        refs = sorted(evidence.allowed_evidence_refs)
        output: CauseAgentOutput
        if kind == "followup_required":
            output = FollowupRequired(
                question="你当时是没看懂方法，还是漏看了题干限定词？",
                rationale="当前证据不足以区分知识缺口和审题失误。",
                evidenceRefs=refs[:2],
            )
        else:
            output = CandidateReady(
                causeCode="stem_misread",
                summary="漏看了题干中的关键限定词",
                improvement="作答前先圈出问法和限定词再比较选项",
                evidenceRefs=(
                    ["invented-evidence:outside-frozen-scope"]
                    if kind == "invalid_grounding"
                    else refs[:3]
                ),
                limitations=["只使用了本次作答和用户复盘证据"],
            )
        payload = output.model_dump(by_alias=True, mode="json")
        return CauseAgentProviderResult(
            output=output,
            usage=ModelTokenUsage(
                input_tokens=180,
                cached_input_tokens=20,
                output_tokens=45,
            ),
            output_fingerprint=sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
            latency_ms=25,
        )


def _reflect(client: Any, auth: dict[str, str], review: dict[str, Any], *, key: str) -> dict[str, Any]:
    response = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": key},
        json={
            "expectedRevision": review["revision"],
            "rawText": "第一题我排除得太快，可能漏看了题干条件。",
            "tags": ["审题", "排除法"],
            "strategyNote": "下次先圈限定词。",
            "doubtNote": None,
            "voiceArtifactVersionIds": [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _run_one(
    factory: sessionmaker[Session],
    provider: SequenceProvider,
) -> None:
    worker = CauseAgentWorker(
        settings=Settings(),
        provider=provider,
        price_card=ModelPriceCard(
            version="cause-agent-test-v1",
            input_cny_per_million=Decimal("1.00"),
            cached_input_cny_per_million=Decimal("0.10"),
            output_cny_per_million=Decimal("2.00"),
        ),
    )
    with factory() as session:
        completed = worker.process_next(session)
    assert completed is not None
    assert completed.status == "succeeded"
