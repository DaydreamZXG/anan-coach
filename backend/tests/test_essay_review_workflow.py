from __future__ import annotations

from base64 import b64encode
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.model_cost import (
    ModelPriceCard,
    ModelTokenUsage,
)
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.essay_intelligence.grounded_provider import (
    GroundedReviewMeteredProviderError,
    GroundedReviewProviderError,
    GroundedReviewProviderResult,
)
from app.domains.essay_intelligence.blind_adjudicator import (
    BlindAdjudicatorMeteredError,
    BlindAdjudicatorProviderResult,
)
from app.domains.essay_intelligence.grounding import (
    GroundedReviewContext,
    normalize_grounding_text,
    review_output_fingerprint,
)
from app.domains.essay_intelligence.multi_model_grading import (
    ADJUDICATION_CONTRACT_VERSION,
    EssayBlindAdjudicationDecision,
    EssayGradingCandidate,
    compare_essay_grading_candidates,
    resolve_essay_grading,
)
from app.domains.essay_intelligence.multi_model_candidate_runner import (
    EssayCandidateSlot,
)
from app.domains.essay_intelligence.multi_model_grading_repository import (
    EssayMultiModelGradingRepository,
)
from app.domains.essay_intelligence.multi_model_runtime import (
    EssayMultiModelRuntimePlan,
    EssayReviewExecutionIdentity,
)
from app.domains.essay_intelligence.review_contracts import (
    GROUNDED_REVIEW_PROMPT_VERSION,
    GROUNDED_REVIEW_PROVIDER_NAME,
    GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
)
from app.domains.essay_intelligence.review_service import (
    GroundedReviewApplicationService,
)
from app.domains.essay_intelligence.schemas import EssayProviderReviewPayload
from app.domains.essay_intelligence.workflow_repository import (
    EssayWorkflowContractError,
    EssayWorkflowNotFound,
)
from app.domains.essay_intelligence.workflow_schemas import (
    EssayAnswerCreateCommand,
    EssayAttemptCreateCommand,
    EssayReviewQueueCommand,
)
from app.domains.essay_intelligence.workflow_service import (
    EssayReviewWorkflowService,
)
from app.domains.essay_intelligence.review_worker import (
    GroundedEssayReviewWorker,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class CatalogSeed:
    task_id: UUID
    material_id: UUID
    rubric_id: UUID
    other_material_id: UUID
    other_rubric_id: UUID


@dataclass(frozen=True)
class QueuedReview:
    user_id: str
    attempt_id: UUID
    answer_id: UUID
    job_id: UUID
    review_id: UUID
    command_id: str


class FakeGroundedProvider:
    provider_name = GROUNDED_REVIEW_PROVIDER_NAME
    provider_release_id = "test-provider-release-v1"
    model = "gemini-3.5-flash"
    endpoint = "https://provider.example/v1/chat/completions"

    def __init__(
        self,
        *,
        initial: str = "valid",
        repair: str = "valid",
        initial_usage: ModelTokenUsage | None = None,
        repair_usage: ModelTokenUsage | None = None,
        request_id_audit: bool = False,
        provider_name: str | None = None,
        provider_release_id: str | None = None,
        model: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        if provider_name is not None:
            self.provider_name = provider_name
        if provider_release_id is not None:
            self.provider_release_id = provider_release_id
        if model is not None:
            self.model = model
        if endpoint is not None:
            self.endpoint = endpoint
        self.initial = initial
        self.repair_behavior = repair
        self.initial_usage = initial_usage or ModelTokenUsage(
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
        )
        self.repair_usage = repair_usage or ModelTokenUsage(
            input_tokens=80,
            cached_input_tokens=0,
            output_tokens=10,
        )
        self.request_id_audit = request_id_audit
        self.generate_calls = 0
        self.repair_calls = 0
        self.idempotency_keys: list[str | None] = []
        self.results: list[GroundedReviewProviderResult] = []

    def generate(
        self,
        *,
        context: GroundedReviewContext,
        request: object,
        idempotency_key: str | None = None,
    ) -> GroundedReviewProviderResult:
        del request
        self.generate_calls += 1
        self.idempotency_keys.append(idempotency_key)
        if self.initial == "retry_once" and self.generate_calls == 1:
            raise GroundedReviewProviderError(
                "provider_transport_failed",
                "provider response was not received",
                retryable=True,
            )
        if self.initial == "retryable_error":
            raise GroundedReviewProviderError(
                "provider_transport_failed",
                "provider response was not received",
                retryable=True,
            )
        if self.initial == "terminal_error":
            raise GroundedReviewProviderError(
                "provider_rejected",
                "provider rejected the request",
                retryable=False,
            )
        if self.initial == "metered_error":
            raise GroundedReviewMeteredProviderError(
                "provider_content_missing",
                "provider returned billed usage without admissible content",
                usage=self.initial_usage,
                latency_ms=19,
                output_fingerprint=_hash("billed-invalid-content"),
                provider_request_id_header="x-test-provider-request-id",
                provider_request_id_fingerprint=_hash(
                    "raw-billed-provider-request-id"
                ),
            )
        result = _provider_result(
            context=context,
            invalid=self.initial == "invalid",
            usage=self.initial_usage,
            request_id_audit=self.request_id_audit,
        )
        if self.initial == "invalid_audit_pair":
            result = replace(
                result,
                provider_request_id_header="x-test-provider-request-id",
                provider_request_id_fingerprint=None,
            )
        self.results.append(result)
        return result

    def repair(
        self,
        *,
        context: GroundedReviewContext,
        request: object,
        rejected_content: str,
        violation_code: str,
        violation_detail: str,
        idempotency_key: str | None = None,
    ) -> GroundedReviewProviderResult:
        del request, rejected_content, violation_code, violation_detail
        self.repair_calls += 1
        self.idempotency_keys.append(idempotency_key)
        if self.repair_behavior == "terminal_error":
            raise GroundedReviewProviderError(
                "repair_rejected",
                "provider rejected the repair",
                retryable=False,
            )
        result = _provider_result(
            context=context,
            invalid=self.repair_behavior == "invalid",
            usage=self.repair_usage,
            request_id_audit=self.request_id_audit,
        )
        self.results.append(result)
        return result


class ShiftedScoreProvider(FakeGroundedProvider):
    def generate(self, **kwargs: Any) -> GroundedReviewProviderResult:
        result = super().generate(**kwargs)
        payload = deepcopy(result.payload)
        assert payload is not None
        shifted = {"lower": 9.0, "recommended": 11.0, "upper": 13.0}
        payload["score"]["scoreRange"] = deepcopy(shifted)
        payload["dimensions"][0]["scoreRange"] = deepcopy(shifted)
        payload["pointCoverage"][0]["awardedRange"] = deepcopy(shifted)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        shifted_result = replace(
            result,
            payload=payload,
            raw_content=raw,
            output_fingerprint=_hash(raw),
        )
        self.results[-1] = shifted_result
        return shifted_result


class FakeBlindAdjudicator:
    provider_name = "test-blind-adjudicator@release-v1"
    provider_release_id = "test-blind-adjudicator-release-v1"
    model = "test-blind-adjudicator-model-v1"
    endpoint = "https://adjudicator.example/v1/chat/completions"

    def __init__(self, *, behavior: str = "valid") -> None:
        self.calls = 0
        self.behavior = behavior

    def adjudicate(self, *, request: Any, idempotency_key: str):
        self.calls += 1
        assert idempotency_key.endswith(":adjudication")
        if self.behavior == "metered_error":
            raise BlindAdjudicatorMeteredError(
                "adjudication_response_invalid",
                "billable adjudicator output violated the contract",
                usage=ModelTokenUsage(
                    input_tokens=80,
                    cached_input_tokens=0,
                    output_tokens=20,
                ),
                output_fingerprint=_hash("invalid-adjudication"),
                latency_ms=12,
            )
        selected = request.candidates[0].payload
        point = selected.point_coverage[0]
        decision = EssayBlindAdjudicationDecision(
            contractVersion=ADJUDICATION_CONTRACT_VERSION,
            selectedCandidateRef=request.candidates[0].candidate_ref,
            rationale="候选 A 的评分点和材料证据对应更完整。",
            rubricPointIds=[point.rubric_point_id],
            answerEvidenceRefs=[point.answer_evidence_refs[0]],
            materialEvidenceRefs=[point.material_evidence_refs[0]],
        )
        serialized = json.dumps(
            decision.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        return BlindAdjudicatorProviderResult(
            decision=decision,
            usage=ModelTokenUsage(
                input_tokens=80,
                cached_input_tokens=0,
                output_tokens=20,
            ),
            output_fingerprint=_hash(serialized),
            latency_ms=12,
            provider_request_id_header=None,
            provider_request_id_fingerprint=None,
        )


class FailOnceApplication:
    def __init__(self) -> None:
        self._delegate = GroundedReviewApplicationService()
        self.calls = 0

    def apply(self, **kwargs: Any):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("crash after durable provider checkpoint")
        return self._delegate.apply(**kwargs)


def test_attempt_and_answer_commands_are_owner_scoped_and_idempotent(
    db_session_factory: sessionmaker[Session],
) -> None:
    catalog = _seed_catalog(db_session_factory)
    owner_id = _seed_user(db_session_factory)
    other_user_id = _seed_user(db_session_factory)
    workflow = _workflow()
    attempt_command = _attempt_command(catalog)

    with db_session_factory() as session:
        first = workflow.create_attempt(
            session,
            user_id=owner_id,
            command_id="attempt-command-1",
            command=attempt_command,
            now=NOW,
        )
        session.commit()

    with db_session_factory() as session:
        replay = workflow.create_attempt(
            session,
            user_id=owner_id,
            command_id="attempt-command-1",
            command=attempt_command,
            now=NOW + timedelta(minutes=1),
        )
        assert replay.attempt.attempt_id == first.attempt.attempt_id
        assert session.execute(
            text("select count(*) from learning.essay_attempts")
        ).scalar_one() == 1

    changed_attempt = attempt_command.model_copy(update={"source_context": "paper"})
    with db_session_factory() as session:
        with pytest.raises(EssayWorkflowContractError, match="Idempotency key"):
            workflow.create_attempt(
                session,
                user_id=owner_id,
                command_id="attempt-command-1",
                command=changed_attempt,
            )

    answer_command = EssayAnswerCreateCommand(
        attempt_id=first.attempt.attempt_id,
        source_type="typed",
        answer_text="基层治理存在问题。",
        thinking=None,
        based_on_answer_version_id=None,
    )
    with db_session_factory() as session:
        with pytest.raises(EssayWorkflowNotFound, match="Owned essay attempt"):
            workflow.create_answer(
                session,
                user_id=other_user_id,
                command_id="other-user-answer",
                command=answer_command,
            )

    with db_session_factory() as session:
        answer = workflow.create_answer(
            session,
            user_id=owner_id,
            command_id="answer-command-1",
            command=answer_command,
            now=NOW,
        )
        session.commit()

    with db_session_factory() as session:
        replay = workflow.create_answer(
            session,
            user_id=owner_id,
            command_id="answer-command-1",
            command=answer_command,
        )
        assert replay.answer.answer_version_id == answer.answer.answer_version_id
        assert session.execute(
            text("select count(*) from learning.essay_answer_versions")
        ).scalar_one() == 1

    changed_answer = answer_command.model_copy(
        update={"answer_text": "不同的答案内容。"}
    )
    with db_session_factory() as session:
        with pytest.raises(EssayWorkflowContractError, match="Idempotency key"):
            workflow.create_answer(
                session,
                user_id=owner_id,
                command_id="answer-command-1",
                command=changed_answer,
            )


def test_attempt_rejects_rubric_bound_to_a_different_material_version(
    db_session_factory: sessionmaker[Session],
) -> None:
    catalog = _seed_catalog(db_session_factory)
    user_id = _seed_user(db_session_factory)
    workflow = _workflow()
    mismatched = EssayAttemptCreateCommand(
        task_version_id=catalog.task_id,
        material_bundle_version_id=catalog.material_id,
        rubric_version_id=catalog.other_rubric_id,
        source_context="in_program",
        timed_status="untimed",
        elapsed_seconds=None,
    )

    with db_session_factory() as session:
        with pytest.raises(
            EssayWorkflowContractError,
            match="rubric bound to that material version",
        ):
            workflow.create_attempt(
                session,
                user_id=user_id,
                command_id="mismatched-rubric",
                command=mismatched,
            )
        assert session.execute(
            text("select count(*) from learning.essay_attempts")
        ).scalar_one() == 0


def test_queue_review_reserves_quota_exactly_once(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)

    with db_session_factory() as session:
        workflow = _workflow()
        replay = workflow.queue_review(
            session,
            user_id=queued.user_id,
            command_id=queued.command_id,
            command=EssayReviewQueueCommand(
                attempt_id=queued.attempt_id,
                answer_version_id=queued.answer_id,
                review_scope="grounded_full",
            ),
            commerce_service=_commerce(session),
        )
        session.commit()
        assert replay.job_id == queued.job_id

    with db_session_factory() as session:
        usage = session.execute(
            text(
                "select used_count from commerce.entitlement_usage_daily "
                "where user_id = cast(:user_id as uuid) and feature = 'essay_review'"
            ),
            {"user_id": queued.user_id},
        ).scalar_one()
        assert usage == 1
        reservation = session.execute(
            text(
                "select status, job_id from commerce.feature_usage_reservations "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": queued.user_id},
        ).mappings().one()
        assert reservation["status"] == "reserved"
        assert reservation["job_id"] == queued.job_id
        assert session.execute(
            text(
                "select count(*) from platform.background_jobs "
                "where job_type = 'essay_grounded_review_v2'"
            )
        ).scalar_one() == 1
        payload = _job_payload(session, queued.job_id)
        assert payload["executionAttemptNo"] == 1
        assert len(payload["executionFingerprint"]) == 64
        assert payload["supersedesReviewVersionId"] is None
        assert session.execute(
            text(
                "select count(*) from platform.outbox_events "
                "where event_type = 'essay_review.requested'"
            )
        ).scalar_one() == 1


def test_worker_completes_and_separates_raw_and_canonical_fingerprints(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider()
    price_card = _price_card()
    worker = _worker(provider=provider, price_card=price_card)

    with db_session_factory() as session:
        job = worker.process_next(session)
        assert job is not None
        assert job.status == "succeeded"
        assert job.result == {
            "reviewVersionId": str(queued.review_id),
            "reviewStatus": "completed",
            "providerRequestIds": [],
        }

    assert provider.generate_calls == 1
    assert provider.repair_calls == 0
    assert provider.idempotency_keys[0]
    provider_result = provider.results[0]
    assert provider_result.payload is not None
    parsed = EssayProviderReviewPayload.model_validate_json(
        json.dumps(provider_result.payload, ensure_ascii=False)
    )
    canonical_fingerprint = review_output_fingerprint(parsed)
    assert canonical_fingerprint != provider_result.output_fingerprint

    with db_session_factory() as session:
        review = session.execute(
            text(
                "select * from learning.essay_review_versions "
                "where review_version_id = cast(:review_id as uuid)"
            ),
            {"review_id": str(queued.review_id)},
        ).mappings().one()
        assert review["status"] == "completed"
        assert review["output_fingerprint"] == canonical_fingerprint
        assert review["grounding_report"]["outputFingerprint"] == canonical_fingerprint

        ledger = session.execute(
            text(
                "select * from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).mappings().one()
        assert ledger["status"] == "succeeded"
        assert ledger["output_fingerprint"] == provider_result.output_fingerprint
        assert ledger["input_tokens"] == 100
        assert ledger["cached_input_tokens"] == 40
        assert ledger["output_tokens"] == 20
        expected_cost = price_card.cost(provider.initial_usage)
        assert ledger["input_cost_cny"] == expected_cost.input_cost_cny
        assert ledger["cached_input_cost_cny"] == expected_cost.cached_input_cost_cny
        assert ledger["output_cost_cny"] == expected_cost.output_cost_cny
        assert ledger["total_cost_cny"] == expected_cost.total_cost_cny

        link = session.execute(
            text(
                "select phase, ordinal from ai_audit.essay_review_model_calls "
                "where review_version_id = cast(:review_id as uuid)"
            ),
            {"review_id": str(queued.review_id)},
        ).mappings().one()
        assert dict(link) == {"phase": "initial", "ordinal": 1}
        assert _quota_used(session, queued.user_id) == 1
        assert _reservation_status_counts(session, queued.user_id) == {
            "consumed": 1
        }


def test_multi_model_grading_bundle_persists_candidates_cost_and_final_interval(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider()
    worker = _worker(provider=provider)
    with db_session_factory() as session:
        completed = worker.process_next(session)
        assert completed is not None and completed.status == "succeeded"

    payload = EssayProviderReviewPayload.model_validate_json(
        json.dumps(provider.results[0].payload, ensure_ascii=False)
    )
    second_call_id = uuid4()
    first_candidate_id = uuid4()
    second_candidate_id = uuid4()
    grading_run_id = uuid4()
    with db_session_factory() as session:
        first_call = session.execute(
            text(
                "select * from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).mappings().one()
        session.execute(
            text(
                """
                insert into ai_audit.model_call_ledger (
                  call_id, call_key, user_id, job_id, feature, operation,
                  provider, model, prompt_version, schema_version, price_version,
                  input_rate_cny_per_million, cached_input_rate_cny_per_million,
                  output_rate_cny_per_million, input_fingerprint,
                  output_fingerprint, status, input_tokens, cached_input_tokens,
                  output_tokens, input_cost_cny, cached_input_cost_cny,
                  output_cost_cny, total_cost_cny, latency_ms, error_code,
                  started_at, completed_at
                ) select
                  cast(:second_call_id as uuid), :call_key, user_id, job_id,
                  feature, 'grounded_review_candidate_grok',
                  'grok-release-v1', 'grok-model-v1', prompt_version,
                  schema_version, price_version, input_rate_cny_per_million,
                  cached_input_rate_cny_per_million,
                  output_rate_cny_per_million, input_fingerprint,
                  output_fingerprint, status, input_tokens, cached_input_tokens,
                  output_tokens, input_cost_cny, cached_input_cost_cny,
                  output_cost_cny, total_cost_cny, latency_ms, error_code,
                  started_at, completed_at
                  from ai_audit.model_call_ledger
                 where call_id = cast(:first_call_id as uuid)
                """
            ),
            {
                "second_call_id": str(second_call_id),
                "call_key": f"essay-review:{queued.job_id}:candidate:grok",
                "first_call_id": str(first_call["call_id"]),
            },
        )
        candidates = [
            EssayGradingCandidate(
                candidateId=first_candidate_id,
                slotId="gemini",
                providerReleaseId="gemini-release-v1",
                model="gemini-model-v1",
                promptVersion="prompt-v1",
                schemaVersion="schema-v1",
                inputFingerprint=first_call["input_fingerprint"],
                outputFingerprint=first_call["output_fingerprint"],
                status="valid",
                payload=payload,
                modelCallId=first_call["call_id"],
                errorCode=None,
            ),
            EssayGradingCandidate(
                candidateId=second_candidate_id,
                slotId="grok",
                providerReleaseId="grok-release-v1",
                model="grok-model-v1",
                promptVersion="prompt-v1",
                schemaVersion="schema-v1",
                inputFingerprint=first_call["input_fingerprint"],
                outputFingerprint=first_call["output_fingerprint"],
                status="valid",
                payload=payload,
                modelCallId=second_call_id,
                errorCode=None,
            ),
        ]
        comparison = compare_essay_grading_candidates(candidates)
        resolved = resolve_essay_grading(
            candidates=candidates,
            comparison=comparison,
        )
        repository = EssayMultiModelGradingRepository()
        repository.insert_bundle(
            session,
            grading_run_id=grading_run_id,
            intended_review_version_id=queued.review_id,
            job_id=queued.job_id,
            user_id=queued.user_id,
            input_fingerprint=first_call["input_fingerprint"],
            candidates=candidates,
            resolved=resolved,
            adjudication_audit=None,
        )
        repository.bind_review(
            session,
            review_version_id=queued.review_id,
            grading_run_id=grading_run_id,
        )
        session.commit()

    with db_session_factory() as session:
        stored = EssayMultiModelGradingRepository().by_review(
            session,
            review_version_id=queued.review_id,
        )
        assert stored is not None
        assert stored["state"] == "consistent"
        assert stored["candidate_count"] == 2
        assert float(stored["final_score_recommended"]) == (
            payload.score.score_range.recommended
        )
        assert session.execute(
            text(
                "select count(*) from ai_audit.essay_grading_candidates "
                "where grading_run_id = cast(:grading_run_id as uuid)"
            ),
            {"grading_run_id": str(grading_run_id)},
        ).scalar_one() == 2


def test_formal_worker_persists_three_candidates_and_consumes_one_review(
    db_session_factory: sessionmaker[Session],
) -> None:
    providers = tuple(
        FakeGroundedProvider(
            provider_name=f"test-provider-{index}@release-v1",
            provider_release_id=f"test-provider-{index}-release-v1",
            model=f"test-model-{index}-v1",
            endpoint=f"https://provider-{index}.example/v1/chat/completions",
        )
        for index in range(3)
    )
    plan = _formal_runtime_plan(providers=cast(Any, providers))
    queued = _prepare_queued_review(
        db_session_factory,
        execution_identity=plan.execution_identity,
    )
    worker = _formal_worker(plan=plan)

    with db_session_factory() as session:
        completed = worker.process_next(session)
        assert completed is not None and completed.status == "succeeded"
        assert completed.result and completed.result["reviewStatus"] == "completed"
        run = EssayMultiModelGradingRepository().by_review(
            session,
            review_version_id=queued.review_id,
        )
        assert run is not None
        assert run["state"] == "consistent"
        assert run["failure_code"] is None
        assert session.execute(
            text(
                "select count(*) from ai_audit.essay_grading_candidates "
                "where grading_run_id = cast(:run_id as uuid)"
            ),
            {"run_id": str(run["grading_run_id"])},
        ).scalar_one() == 3
        assert session.execute(
            text(
                "select count(*) from ai_audit.essay_review_model_calls "
                "where review_version_id = cast(:review_id as uuid)"
            ),
            {"review_id": str(queued.review_id)},
        ).scalar_one() == 0
        assert _quota_used(session, queued.user_id) == 1
    assert [provider.generate_calls for provider in providers] == [1, 1, 1]


def test_formal_worker_blindly_adjudicates_minor_disagreement(
    db_session_factory: sessionmaker[Session],
) -> None:
    providers = (
        FakeGroundedProvider(
            provider_name="test-provider-a@release-v1",
            provider_release_id="test-provider-a-release-v1",
            model="test-model-a-v1",
        ),
        ShiftedScoreProvider(
            provider_name="test-provider-b@release-v1",
            provider_release_id="test-provider-b-release-v1",
            model="test-model-b-v1",
        ),
        FakeGroundedProvider(
            provider_name="test-provider-c@release-v1",
            provider_release_id="test-provider-c-release-v1",
            model="test-model-c-v1",
        ),
    )
    adjudicator = FakeBlindAdjudicator()
    plan = _formal_runtime_plan(providers=providers, adjudicator=adjudicator)
    queued = _prepare_queued_review(
        db_session_factory,
        execution_identity=plan.execution_identity,
    )

    with db_session_factory() as session:
        completed = _formal_worker(plan=plan).process_next(session)
        assert completed is not None and completed.status == "succeeded"
        run = EssayMultiModelGradingRepository().by_review(
            session,
            review_version_id=queued.review_id,
        )
        assert run is not None and run["state"] == "minor_disagreement"
        assert session.execute(
            text(
                "select count(*) from ai_audit.essay_grading_adjudications "
                "where grading_run_id = cast(:run_id as uuid)"
            ),
            {"run_id": str(run["grading_run_id"])},
        ).scalar_one() == 1
    assert adjudicator.calls == 1


def test_formal_worker_persists_metered_adjudication_failure_and_refunds_quota(
    db_session_factory: sessionmaker[Session],
) -> None:
    providers = (
        FakeGroundedProvider(
            provider_name="test-provider-a@release-v1",
            provider_release_id="test-provider-a-release-v1",
            model="test-model-a-v1",
        ),
        ShiftedScoreProvider(
            provider_name="test-provider-b@release-v1",
            provider_release_id="test-provider-b-release-v1",
            model="test-model-b-v1",
        ),
        FakeGroundedProvider(
            provider_name="test-provider-c@release-v1",
            provider_release_id="test-provider-c-release-v1",
            model="test-model-c-v1",
        ),
    )
    adjudicator = FakeBlindAdjudicator(behavior="metered_error")
    plan = _formal_runtime_plan(providers=providers, adjudicator=adjudicator)
    queued = _prepare_queued_review(
        db_session_factory,
        execution_identity=plan.execution_identity,
    )

    with db_session_factory() as session:
        failed = _formal_worker(plan=plan).process_next(session)
        assert failed is not None and failed.status == "failed"
        run = EssayMultiModelGradingRepository().by_review(
            session,
            review_version_id=queued.review_id,
        )
        assert run is not None
        assert run["state"] == "minor_disagreement"
        assert run["failure_code"] == "adjudication_response_invalid"
        assert run["selected_candidate_id"] is None
        assert session.execute(
            text(
                "select count(*) from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid) and status = 'succeeded'"
            ),
            {"job_id": str(queued.job_id)},
        ).scalar_one() == 4
        assert _quota_used(session, queued.user_id) == 0
        assert _reservation_status_counts(session, queued.user_id) == {"refunded": 1}
    assert adjudicator.calls == 1


def test_formal_worker_refunds_when_fewer_than_two_candidates_are_valid(
    db_session_factory: sessionmaker[Session],
) -> None:
    providers = (
        FakeGroundedProvider(
            provider_name="test-provider-a@release-v1",
            provider_release_id="test-provider-a-release-v1",
            model="test-model-a-v1",
            initial="invalid",
        ),
        FakeGroundedProvider(
            provider_name="test-provider-b@release-v1",
            provider_release_id="test-provider-b-release-v1",
            model="test-model-b-v1",
            initial="invalid",
        ),
        FakeGroundedProvider(
            provider_name="test-provider-c@release-v1",
            provider_release_id="test-provider-c-release-v1",
            model="test-model-c-v1",
        ),
    )
    plan = _formal_runtime_plan(providers=providers)
    queued = _prepare_queued_review(
        db_session_factory,
        execution_identity=plan.execution_identity,
    )

    with db_session_factory() as session:
        failed = _formal_worker(plan=plan).process_next(session)
        assert failed is not None and failed.status == "failed"
        run = EssayMultiModelGradingRepository().by_review(
            session,
            review_version_id=queued.review_id,
        )
        assert run is not None and run["state"] == "unable_to_score"
        assert _quota_used(session, queued.user_id) == 0
        assert _reservation_status_counts(session, queued.user_id) == {"refunded": 1}


def test_formal_worker_replays_candidate_checkpoints_without_duplicate_calls(
    db_session_factory: sessionmaker[Session],
) -> None:
    providers = tuple(
        FakeGroundedProvider(
            provider_name=f"test-provider-{index}@release-v1",
            provider_release_id=f"test-provider-{index}-release-v1",
            model=f"test-model-{index}-v1",
        )
        for index in range(3)
    )
    plan = _formal_runtime_plan(providers=cast(Any, providers))
    queued = _prepare_queued_review(
        db_session_factory,
        execution_identity=plan.execution_identity,
    )
    worker = _formal_worker(plan=plan, application=FailOnceApplication())

    with db_session_factory() as session:
        with pytest.raises(RuntimeError, match="durable provider checkpoint"):
            worker.process_next(session)
    with db_session_factory() as session:
        session.execute(
            text(
                "update platform.background_jobs set run_after = :now "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id), "now": datetime.now(UTC)},
        )
        session.commit()
    with db_session_factory() as session:
        completed = worker.process_next(session)
        assert completed is not None and completed.status == "succeeded"
        assert session.execute(
            text(
                "select count(*) from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).scalar_one() == 3
    assert [provider.generate_calls for provider in providers] == [1, 1, 1]


def test_formal_worker_does_not_redispatch_an_uncertain_candidate_call(
    db_session_factory: sessionmaker[Session],
) -> None:
    providers = (
        FakeGroundedProvider(
            provider_name="test-provider-a@release-v1",
            provider_release_id="test-provider-a-release-v1",
            model="test-model-a-v1",
        ),
        FakeGroundedProvider(
            provider_name="test-provider-b@release-v1",
            provider_release_id="test-provider-b-release-v1",
            model="test-model-b-v1",
            initial="retry_once",
        ),
        FakeGroundedProvider(
            provider_name="test-provider-c@release-v1",
            provider_release_id="test-provider-c-release-v1",
            model="test-model-c-v1",
        ),
    )
    plan = _formal_runtime_plan(providers=providers)
    queued = _prepare_queued_review(
        db_session_factory,
        execution_identity=plan.execution_identity,
    )
    worker = _formal_worker(plan=plan)

    with db_session_factory() as session:
        with pytest.raises(GroundedReviewProviderError, match="not received"):
            worker.process_next(session)
    with db_session_factory() as session:
        session.execute(
            text(
                "update platform.background_jobs set run_after = :now "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id), "now": datetime.now(UTC)},
        )
        session.commit()
    with db_session_factory() as session:
        completed = worker.process_next(session)
        assert completed is not None and completed.status == "succeeded"
        statuses = session.execute(
            text(
                "select slot_id, status, error_code "
                "from ai_audit.essay_grading_candidates "
                "order by slot_id"
            )
        ).all()
        assert statuses == [
            ("gemini", "valid", None),
            ("hunyuan", "valid", None),
            (
                "tokenrhythm",
                "provider_failed",
                "candidate_outcome_unknown_after_interruption",
            ),
        ]
    assert [provider.generate_calls for provider in providers] == [1, 1, 1]


def test_grounding_failure_repairs_once_and_can_complete(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider(initial="invalid", repair="valid")
    worker = _worker(provider=provider)

    with db_session_factory() as session:
        job = worker.process_next(session)
        assert job is not None
        assert job.status == "succeeded"
        assert job.result and job.result["reviewStatus"] == "completed"
        assert job.result["providerRequestIds"] == []

    assert provider.generate_calls == 1
    assert provider.repair_calls == 1
    assert len(set(provider.idempotency_keys)) == 2
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid) and status = 'succeeded'"
            ),
            {"job_id": str(queued.job_id)},
        ).scalar_one() == 2
        phases = session.execute(
            text(
                "select phase, ordinal from ai_audit.essay_review_model_calls "
                "where review_version_id = cast(:review_id as uuid) order by ordinal"
            ),
            {"review_id": str(queued.review_id)},
        ).all()
        assert phases == [("initial", 1), ("repair", 2)]
        assert _quota_used(session, queued.user_id) == 1


def test_review_stays_blocked_after_one_repair_and_refunds_quota(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider(initial="invalid", repair="invalid")
    worker = _worker(provider=provider)

    with db_session_factory() as session:
        job = worker.process_next(session)
        assert job is not None
        assert job.status == "succeeded"
        assert job.result and job.result["reviewStatus"] == "blocked"

    with db_session_factory() as session:
        review = session.execute(
            text(
                "select * from learning.essay_review_versions "
                "where review_version_id = cast(:review_id as uuid)"
            ),
            {"review_id": str(queued.review_id)},
        ).mappings().one()
        assert review["status"] == "blocked"
        assert review["output_fingerprint"] is None
        assert review["dimensions"] == []
        assert review["point_coverage"] == []
        assert review["findings"] == []
        assert review["readiness"]["blockingCodes"] == [
            "quote_text_anchor_invalid"
        ]
        assert _quota_used(session, queued.user_id) == 0
        assert _reservation_status_counts(session, queued.user_id) == {
            "refunded": 1
        }
        assert session.execute(
            text(
                "select count(*) from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).scalar_one() == 2

        first_job_payload = _job_payload(session, queued.job_id)

    with db_session_factory() as session:
        retried = _workflow().regenerate_review_job(
            session,
            user_id=queued.user_id,
            command_id=f"regenerate:{uuid4()}",
            source_job_id=queued.job_id,
            commerce_service=_commerce(session),
        )
        session.commit()
        assert retried.job_id != queued.job_id
        assert retried.review_version_id != queued.review_id
        retry_payload = _job_payload(session, retried.job_id)
        assert retry_payload["inputFingerprint"] == first_job_payload[
            "inputFingerprint"
        ]
        assert retry_payload["executionAttemptNo"] == 2
        assert retry_payload["executionFingerprint"] != first_job_payload[
            "executionFingerprint"
        ]
        assert retry_payload["supersedesReviewVersionId"] == str(
            queued.review_id
        )
        assert _quota_used(session, queued.user_id) == 1
        assert _reservation_status_counts(session, queued.user_id) == {
            "refunded": 1,
            "reserved": 1,
        }

    retry_worker = _worker(provider=FakeGroundedProvider())
    with db_session_factory() as session:
        retried_job = retry_worker.process_next(session)
        assert retried_job is not None
        assert retried_job.status == "succeeded"
        assert retried_job.result and retried_job.result[
            "reviewStatus"
        ] == "completed"

    with db_session_factory() as session:
        retry_review = session.execute(
            text(
                "select * from learning.essay_review_versions "
                "where review_version_id = cast(:review_id as uuid)"
            ),
            {"review_id": str(retried.review_version_id)},
        ).mappings().one()
        assert retry_review["execution_attempt_no"] == 2
        assert retry_review["supersedes_review_version_id"] == queued.review_id
        assert retry_review["status"] == "completed"
        assert _quota_used(session, queued.user_id) == 1
        assert _reservation_status_counts(session, queued.user_id) == {
            "consumed": 1,
            "refunded": 1,
        }


def test_terminal_provider_failure_records_failure_and_refunds_quota(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider(initial="terminal_error")
    worker = _worker(provider=provider)

    with db_session_factory() as session:
        job = worker.process_next(session)
        assert job is not None
        assert job.status == "failed"
        assert job.last_error_code == "provider_rejected"

    with db_session_factory() as session:
        review = session.execute(
            text(
                "select status, readiness from learning.essay_review_versions "
                "where review_version_id = cast(:review_id as uuid)"
            ),
            {"review_id": str(queued.review_id)},
        ).mappings().one()
        assert review["status"] == "failed"
        assert review["readiness"]["blockingCodes"] == ["provider_rejected"]
        assert _quota_used(session, queued.user_id) == 0
        assert _reservation_status_counts(session, queued.user_id) == {
            "refunded": 1
        }
        ledger = session.execute(
            text(
                "select status, error_code, input_tokens, output_fingerprint "
                "from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).mappings().one()
        assert ledger["status"] == "failed"
        assert ledger["error_code"] == "provider_rejected"
        assert ledger["input_tokens"] is None
        assert ledger["output_fingerprint"] is None

        failed_payload = _job_payload(session, queued.job_id)

    with db_session_factory() as session:
        retried = _workflow().regenerate_review_job(
            session,
            user_id=queued.user_id,
            command_id=f"regenerate:{uuid4()}",
            source_job_id=queued.job_id,
            commerce_service=_commerce(session),
        )
        session.commit()
        retry_payload = _job_payload(session, retried.job_id)
        assert retry_payload["inputFingerprint"] == failed_payload[
            "inputFingerprint"
        ]
        assert retry_payload["executionAttemptNo"] == 2
        assert retry_payload["supersedesReviewVersionId"] == str(
            queued.review_id
        )
        assert _quota_used(session, queued.user_id) == 1
        assert _reservation_status_counts(session, queued.user_id) == {
            "refunded": 1,
            "reserved": 1,
        }


def test_billed_invalid_provider_response_records_cost_and_safe_audit_then_refunds(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider(initial="metered_error")
    price_card = _price_card()
    worker = _worker(provider=provider, price_card=price_card)

    with db_session_factory() as session:
        job = worker.process_next(session)
        assert job is not None
        assert job.status == "failed"
        assert job.last_error_code == "provider_content_missing"

    expected_request_audit = [
        {
            "phase": "initial",
            "header": "x-test-provider-request-id",
            "fingerprint": _hash("raw-billed-provider-request-id"),
        }
    ]
    with db_session_factory() as session:
        ledger = session.execute(
            text(
                "select * from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).mappings().one()
        assert ledger["status"] == "succeeded"
        assert ledger["error_code"] is None
        assert ledger["input_tokens"] == provider.initial_usage.input_tokens
        assert ledger["cached_input_tokens"] == (
            provider.initial_usage.cached_input_tokens
        )
        assert ledger["output_tokens"] == provider.initial_usage.output_tokens
        assert ledger["output_fingerprint"] == _hash("billed-invalid-content")
        assert ledger["total_cost_cny"] == price_card.cost(
            provider.initial_usage
        ).total_cost_cny

        link = session.execute(
            text(
                "select phase, ordinal from ai_audit.essay_review_model_calls "
                "where review_version_id = cast(:review_id as uuid)"
            ),
            {"review_id": str(queued.review_id)},
        ).mappings().one()
        assert dict(link) == {"phase": "initial", "ordinal": 1}

        event = session.execute(
            text(
                "select payload from platform.outbox_events "
                "where deduplication_key = :key"
            ),
            {"key": f"essay-review-result:{queued.review_id}"},
        ).scalar_one()
        assert event["providerRequestIds"] == expected_request_audit
        serialized_event = json.dumps(event, ensure_ascii=False)
        assert "raw-billed-provider-request-id" not in serialized_event

        job_result = session.execute(
            text(
                "select result from platform.background_jobs "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).scalar_one()
        assert job_result["initial"]["providerRequestIdFingerprint"] == _hash(
            "raw-billed-provider-request-id"
        )
        assert job_result["initial"]["rejectedCode"] == (
            "provider_content_missing"
        )
        assert "raw-billed-provider-request-id" not in json.dumps(job_result)
        assert _quota_used(session, queued.user_id) == 0
        assert _reservation_status_counts(session, queued.user_id) == {
            "refunded": 1
        }


def test_returned_request_id_pair_violation_cannot_leave_ledger_running(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider(initial="invalid_audit_pair")
    worker = _worker(provider=provider)

    with db_session_factory() as session:
        job = worker.process_next(session)
        assert job is not None
        assert job.status == "failed"
        assert job.last_error_code == "provider_request_id_contract_invalid"

    with db_session_factory() as session:
        ledger = session.execute(
            text(
                "select status, input_tokens, output_tokens, total_cost_cny "
                "from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).mappings().one()
        assert ledger["status"] == "succeeded"
        assert ledger["input_tokens"] == provider.initial_usage.input_tokens
        assert ledger["output_tokens"] == provider.initial_usage.output_tokens
        assert ledger["total_cost_cny"] > 0
        assert session.execute(
            text(
                "select count(*) from ai_audit.essay_review_model_calls "
                "where review_version_id = cast(:review_id as uuid)"
            ),
            {"review_id": str(queued.review_id)},
        ).scalar_one() == 1
        assert _quota_used(session, queued.user_id) == 0
        assert _reservation_status_counts(session, queued.user_id) == {
            "refunded": 1
        }


def test_retryable_provider_failure_replays_one_logical_call_and_costs_once(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider(initial="retry_once")
    original_price = _price_card()
    worker = _worker(provider=provider, price_card=original_price)

    with db_session_factory() as session:
        first = worker.process_next(session)
        assert first is not None
        assert first.status == "queued"
        assert first.attempt_count == 1

    with db_session_factory() as session:
        running_call = session.execute(
            text(
                "select status, call_key from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).mappings().one()
        assert running_call["status"] == "running"
        call_key = str(running_call["call_key"])
        assert _reservation_status_counts(session, queued.user_id) == {
            "reserved": 1
        }
        session.execute(
            text(
                "update platform.background_jobs set run_after = :run_after "
                "where job_id = cast(:job_id as uuid)"
            ),
            {
                "job_id": str(queued.job_id),
                "run_after": datetime.now(UTC) - timedelta(seconds=1),
            },
        )
        session.commit()

    replacement_price = ModelPriceCard(
        version="test-price-v2",
        input_cny_per_million=Decimal("20"),
        cached_input_cny_per_million=Decimal("5"),
        output_cny_per_million=Decimal("40"),
    )
    worker = _worker(provider=provider, price_card=replacement_price)
    with db_session_factory() as session:
        second = worker.process_next(session)
        assert second is not None
        assert second.status == "succeeded"
        assert second.attempt_count == 2

    assert provider.generate_calls == 2
    assert provider.idempotency_keys[0] == provider.idempotency_keys[1]
    with db_session_factory() as session:
        calls = session.execute(
            text(
                "select status, call_key, price_version, "
                "input_rate_cny_per_million, "
                "cached_input_rate_cny_per_million, "
                "output_rate_cny_per_million, total_cost_cny "
                "from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).mappings().all()
        assert len(calls) == 1
        assert calls[0]["status"] == "succeeded"
        assert calls[0]["call_key"] == call_key
        assert calls[0]["price_version"] == original_price.version
        assert calls[0]["input_rate_cny_per_million"] == Decimal("2")
        assert calls[0]["cached_input_rate_cny_per_million"] == Decimal("0.5")
        assert calls[0]["output_rate_cny_per_million"] == Decimal("4")
        assert calls[0]["total_cost_cny"] == original_price.cost(
            provider.initial_usage
        ).total_cost_cny
        assert _quota_used(session, queued.user_id) == 1
        assert _reservation_status_counts(session, queued.user_id) == {
            "consumed": 1
        }


def test_exhausted_retryable_provider_call_becomes_terminal_and_refunds(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider(initial="retryable_error")
    worker = _worker(provider=provider)

    statuses: list[str] = []
    with db_session_factory() as session:
        for _ in range(3):
            result = worker.process_next(session)
            assert result is not None
            statuses.append(result.status)
            if result.status == "queued":
                session.execute(
                    text(
                        "update platform.background_jobs set run_after = :run_after "
                        "where job_id = cast(:job_id as uuid)"
                    ),
                    {
                        "job_id": str(queued.job_id),
                        "run_after": datetime.now(UTC) - timedelta(seconds=1),
                    },
                )
                session.commit()
    assert statuses == ["queued", "queued", "failed"]
    assert provider.generate_calls == 3
    assert len(set(provider.idempotency_keys)) == 1

    with db_session_factory() as session:
        ledger = session.execute(
            text(
                "select status, error_code from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).mappings().one()
        assert ledger["status"] == "failed"
        assert ledger["error_code"] == "provider_transport_failed"
        assert _quota_used(session, queued.user_id) == 0
        assert _reservation_status_counts(session, queued.user_id) == {
            "refunded": 1
        }


def test_worker_replays_durable_checkpoint_without_second_provider_charge(
    db_session_factory: sessionmaker[Session],
) -> None:
    queued = _prepare_queued_review(db_session_factory)
    provider = FakeGroundedProvider(request_id_audit=True)
    application = FailOnceApplication()
    worker = _worker(provider=provider, application=application)

    with db_session_factory() as session:
        with pytest.raises(
            RuntimeError,
            match="crash after durable provider checkpoint",
        ):
            worker.process_next(session)

    with db_session_factory() as session:
        checkpointed = session.execute(
            text(
                "select status, result from platform.background_jobs "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).mappings().one()
        assert checkpointed["status"] == "queued"
        assert set(checkpointed["result"]) == {"initial"}
        session.execute(
            text(
                "update platform.background_jobs set run_after = :run_after "
                "where job_id = cast(:job_id as uuid)"
            ),
            {
                "job_id": str(queued.job_id),
                "run_after": datetime.now(UTC) - timedelta(seconds=1),
            },
        )
        session.commit()

    with db_session_factory() as session:
        job = worker.process_next(session)
        assert job is not None
        assert job.status == "succeeded"
        assert job.result and job.result["reviewStatus"] == "completed"
        expected_request_audit = [
            {
                "phase": "initial",
                "header": "x-test-provider-request-id",
                "fingerprint": _hash("test-provider-request-id"),
            }
        ]
        assert job.result["providerRequestIds"] == expected_request_audit
        event = session.execute(
            text(
                "select payload from platform.outbox_events "
                "where deduplication_key = :key"
            ),
            {"key": f"essay-review-result:{queued.review_id}"},
        ).scalar_one()
        assert event["providerRequestIds"] == expected_request_audit

    assert provider.generate_calls == 1
    assert provider.repair_calls == 0
    assert application.calls == 2
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from ai_audit.model_call_ledger "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(queued.job_id)},
        ).scalar_one() == 1
        assert session.execute(
            text(
                "select count(*) from ai_audit.essay_review_model_calls "
                "where review_version_id = cast(:review_id as uuid)"
            ),
            {"review_id": str(queued.review_id)},
        ).scalar_one() == 1
        assert _quota_used(session, queued.user_id) == 1
        assert _reservation_status_counts(session, queued.user_id) == {
            "consumed": 1
        }


def _worker(
    *,
    provider: FakeGroundedProvider,
    price_card: ModelPriceCard | None = None,
    application: Any | None = None,
) -> GroundedEssayReviewWorker:
    return GroundedEssayReviewWorker(
        settings=Settings(
            ai_essay_review_model=provider.model,
            ai_essay_checkpoint_active_key_id="test-key-v1",
            ai_essay_checkpoint_keys_json=json.dumps(
                {"test-key-v1": b64encode(b"k" * 32).decode("ascii")}
            ),
            ai_essay_checkpoint_ttl_seconds=3600,
        ),
        provider=cast(Any, provider),
        price_card=price_card or _price_card(),
        application=cast(Any, application) if application is not None else None,
    )


def _formal_runtime_plan(
    *,
    providers: tuple[FakeGroundedProvider, FakeGroundedProvider, FakeGroundedProvider],
    adjudicator: FakeBlindAdjudicator | None = None,
) -> EssayMultiModelRuntimePlan:
    slots = tuple(
        EssayCandidateSlot(
            slot_id=slot_id,
            provider=cast(Any, provider),
            price_card=_price_card(),
            prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
            schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
        )
        for slot_id, provider in zip(
            ("gemini", "tokenrhythm", "hunyuan"),
            providers,
            strict=True,
        )
    )
    return EssayMultiModelRuntimePlan(
        release_id="test-formal-plan-v1",
        candidate_slots=cast(Any, slots),
        adjudicator_provider=cast(Any, adjudicator or FakeBlindAdjudicator()),
        adjudicator_price_card=_price_card(),
        execution_identity=EssayReviewExecutionIdentity(
            provider="essay-multi-model@test-formal-plan-v1",
            model="plan-sha256:" + "f" * 64,
        ),
    )


def _formal_worker(
    *,
    plan: EssayMultiModelRuntimePlan,
    application: Any | None = None,
) -> GroundedEssayReviewWorker:
    return GroundedEssayReviewWorker(
        settings=Settings(
            ai_essay_checkpoint_active_key_id="test-key-v1",
            ai_essay_checkpoint_keys_json=json.dumps(
                {"test-key-v1": b64encode(b"k" * 32).decode("ascii")}
            ),
            ai_essay_checkpoint_ttl_seconds=3600,
        ),
        runtime_plan=plan,
        application=cast(Any, application) if application is not None else None,
    )


def _price_card() -> ModelPriceCard:
    return ModelPriceCard(
        version="test-price-v1",
        input_cny_per_million=Decimal("2"),
        cached_input_cny_per_million=Decimal("0.5"),
        output_cny_per_million=Decimal("4"),
    )


def _prepare_queued_review(
    db_session_factory: sessionmaker[Session],
    *,
    execution_identity: EssayReviewExecutionIdentity | None = None,
) -> QueuedReview:
    catalog = _seed_catalog(db_session_factory)
    user_id = _seed_user(db_session_factory)
    workflow = _workflow(execution_identity)
    with db_session_factory() as session:
        attempt = workflow.create_attempt(
            session,
            user_id=user_id,
            command_id=f"attempt:{uuid4()}",
            command=_attempt_command(catalog),
            now=NOW,
        ).attempt
        answer = workflow.create_answer(
            session,
            user_id=user_id,
            command_id=f"answer:{uuid4()}",
            command=EssayAnswerCreateCommand(
                attempt_id=attempt.attempt_id,
                source_type="typed",
                answer_text="基层治理存在问题。",
                thinking=None,
                based_on_answer_version_id=None,
            ),
            now=NOW,
        ).answer
        review_command_id = f"review:{uuid4()}"
        queued = workflow.queue_review(
            session,
            user_id=user_id,
            command_id=review_command_id,
            command=EssayReviewQueueCommand(
                attempt_id=attempt.attempt_id,
                answer_version_id=answer.answer_version_id,
                review_scope="grounded_full",
            ),
            commerce_service=_commerce(session),
        )
        session.commit()
    return QueuedReview(
        user_id=user_id,
        attempt_id=attempt.attempt_id,
        answer_id=answer.answer_version_id,
        job_id=queued.job_id,
        review_id=queued.review_version_id,
        command_id=review_command_id,
    )


def _commerce(session: Session) -> CommerceService:
    return CommerceService(
        repository=CommerceRepository(session),
        app_store_verifier=cast(Any, object()),
        commercial_mode="paid",
        usage_access_mode="entitlement_quota",
        now=lambda: NOW,
    )


def _workflow(
    execution_identity: EssayReviewExecutionIdentity | None = None,
) -> EssayReviewWorkflowService:
    return EssayReviewWorkflowService(
        execution_identity_override=execution_identity
        or EssayReviewExecutionIdentity(
            provider=GROUNDED_REVIEW_PROVIDER_NAME,
            model="gemini-3.5-flash",
        ),
    )


def _attempt_command(catalog: CatalogSeed) -> EssayAttemptCreateCommand:
    return EssayAttemptCreateCommand(
        task_version_id=catalog.task_id,
        material_bundle_version_id=catalog.material_id,
        rubric_version_id=catalog.rubric_id,
        source_context="in_program",
        timed_status="untimed",
        elapsed_seconds=None,
    )


def _provider_result(
    *,
    context: GroundedReviewContext,
    invalid: bool,
    usage: ModelTokenUsage,
    request_id_audit: bool,
) -> GroundedReviewProviderResult:
    payload = _valid_provider_payload(context)
    if invalid:
        payload["dimensions"][0]["answerEvidenceRefs"][0][
            "normalizedEnd"
        ] = 5
    # The payload stays schema-shaped; only the raw response formatting differs.
    raw_content = json.dumps(payload, ensure_ascii=False, indent=2)
    return GroundedReviewProviderResult(
        payload=payload,
        raw_content=raw_content,
        usage=usage,
        latency_ms=17,
        output_fingerprint=_hash(raw_content),
        provider_request_id_header=(
            "x-test-provider-request-id" if request_id_audit else None
        ),
        provider_request_id_fingerprint=(
            _hash("test-provider-request-id") if request_id_audit else None
        ),
    )


def _valid_provider_payload(context: GroundedReviewContext) -> dict[str, Any]:
    answer_quote = "基层治理"
    material_quote = "基层 治理"
    answer_ref = {
        "answerVersionId": str(context.answer.answer_version_id),
        "segmentId": "answer-1",
        "quote": answer_quote,
        "quoteSha256": _hash(normalize_grounding_text(answer_quote)),
        "normalization": "nfkc_whitespace_v1",
        "normalizedStart": 0,
        "normalizedEnd": 4,
    }
    material_ref = {
        "materialBundleVersionId": str(
            context.material.material_bundle_version_id
        ),
        "materialId": "material-1",
        "segmentId": "segment-1",
        "quote": material_quote,
        "quoteSha256": _hash(normalize_grounding_text(material_quote)),
        "normalization": "nfkc_whitespace_v1",
        "normalizedStart": 0,
        "normalizedEnd": 5,
    }
    return {
        "score": {
            "maximum": 20.0,
            "scoreRange": {"lower": 10.0, "recommended": 12.0, "upper": 14.0},
            "confidence": "medium",
            "confidenceReasons": ["官方评分规程与引用均已验证"],
        },
        "dimensions": [
            {
                "criterionCode": "content",
                "label": "内容",
                "maximum": 20.0,
                "scoreRange": {
                    "lower": 10.0,
                    "recommended": 12.0,
                    "upper": 14.0,
                },
                "rationale": "要点基本完整。",
                "findingIds": ["finding-1"],
                "answerEvidenceRefs": [deepcopy(answer_ref)],
                "materialEvidenceRefs": [deepcopy(material_ref)],
            }
        ],
        "pointCoverage": [
            {
                "rubricPointId": "point-1",
                "state": "partial",
                "awardedRange": {
                    "lower": 10.0,
                    "recommended": 12.0,
                    "upper": 14.0,
                },
                "explanation": "概括了核心问题。",
                "answerEvidenceRefs": [deepcopy(answer_ref)],
                "materialEvidenceRefs": [deepcopy(material_ref)],
            }
        ],
        "claimEvidence": [],
        "findings": [
            {
                "findingId": "finding-1",
                "type": "abstraction",
                "severity": "medium",
                "diagnosis": "概括层级仍可提高。",
                "action": "将事实归纳为上位概念。",
                "attribution": "unknown",
                "answerEvidenceRefs": [deepcopy(answer_ref)],
                "materialEvidenceRefs": [deepcopy(material_ref)],
            }
        ],
        "rewritePlan": {
            "priorityFindingIds": ["finding-1"],
            "targetPointIds": ["point-1"],
            "constraints": [{"code": "word_limit", "value": "200"}],
            "steps": [
                {
                    "stepId": "step-1",
                    "instruction": "先归类后概括。",
                    "successCriterion": "表达为一个清晰上位概念。",
                }
            ],
        },
    }


def _seed_catalog(
    db_session_factory: sessionmaker[Session],
) -> CatalogSeed:
    task_id = uuid4()
    material_id = uuid4()
    rubric_id = uuid4()
    other_material_id = uuid4()
    other_rubric_id = uuid4()
    prompt = "根据材料概括基层治理问题。"
    criteria = [
        {
            "criterionCode": "content",
            "label": "内容",
            "maximum": 20.0,
            "scoringRule": "按材料要点覆盖评分。",
            "materialEvidenceRequired": True,
        }
    ]
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into product.essay_task_versions (
                  task_version_id, task_key, version_no, question_type, prompt,
                  prompt_sha256, word_limit, maximum_score, state, created_at
                ) values (
                  cast(:task_id as uuid), :task_key, 1, 'summary', :prompt,
                  :prompt_sha256, 200, 20, 'published', :created_at
                )
                """
            ),
            {
                "task_id": str(task_id),
                "task_key": f"guokao-workflow:{task_id}",
                "prompt": prompt,
                "prompt_sha256": _hash(prompt),
                "created_at": NOW,
            },
        )
        for version_no, bundle_id, bundle_text in (
            (1, material_id, "基层  治理存在问题。"),
            (2, other_material_id, "执行  效率仍需提升。"),
        ):
            session.execute(
                text(
                    """
                    insert into product.essay_material_bundle_versions (
                      material_bundle_version_id, task_version_id, task_key,
                      version_no, completeness, source_kind, content_sha256,
                      created_at
                    ) values (
                      cast(:bundle_id as uuid), cast(:task_id as uuid), :task_key,
                      :version_no, 'complete', 'official', :content_sha256,
                      :created_at
                    )
                    """
                ),
                {
                    "bundle_id": str(bundle_id),
                    "task_id": str(task_id),
                    "task_key": f"guokao-workflow:{task_id}",
                    "version_no": version_no,
                    "content_sha256": _hash(f"bundle:{bundle_id}"),
                    "created_at": NOW,
                },
            )
            session.execute(
                text(
                    """
                    insert into product.essay_material_segments (
                      material_segment_id, material_bundle_version_id,
                      material_id, segment_id, material_order, segment_order,
                      text, text_sha256
                    ) values (
                      cast(:row_id as uuid), cast(:bundle_id as uuid),
                      :material_id, :segment_id, 1, 1, :text, :text_sha256
                    )
                    """
                ),
                {
                    "row_id": str(uuid4()),
                    "bundle_id": str(bundle_id),
                    "material_id": (
                        "material-1" if bundle_id == material_id else "material-2"
                    ),
                    "segment_id": (
                        "segment-1" if bundle_id == material_id else "segment-2"
                    ),
                    "text": bundle_text,
                    "text_sha256": _hash(bundle_text),
                },
            )

        for version_no, bound_material, rubric_version, point in (
            (
                1,
                material_id,
                rubric_id,
                {
                    "rubricPointId": "point-1",
                    "criterionCode": "content",
                    "category": "problem",
                    "label": "基层治理问题",
                    "maximumCredit": 20.0,
                    "materialSegmentIds": ["segment-1"],
                    "materialEvidenceRefs": [
                        {
                            "materialBundleVersionId": str(material_id),
                            "materialId": "material-1",
                            "segmentId": "segment-1",
                            "startOffset": 0,
                            "endOffset": 2,
                            "quote": "基层",
                            "quoteSha256": _hash("基层"),
                        }
                    ],
                },
            ),
            (
                2,
                other_material_id,
                other_rubric_id,
                {
                    "rubricPointId": "point-2",
                    "criterionCode": "content",
                    "category": "problem",
                    "label": "执行效率问题",
                    "maximumCredit": 20.0,
                    "materialSegmentIds": ["segment-2"],
                    "materialEvidenceRefs": [
                        {
                            "materialBundleVersionId": str(other_material_id),
                            "materialId": "material-2",
                            "segmentId": "segment-2",
                            "startOffset": 0,
                            "endOffset": 2,
                            "quote": "执行",
                            "quoteSha256": _hash("执行"),
                        }
                    ],
                },
            ),
        ):
            session.execute(
                text(
                    """
                    insert into product.essay_rubric_versions (
                      rubric_version_id, task_version_id, task_key,
                      material_bundle_version_id, version_no, source_kind,
                      verification_status, state, total_maximum, criteria,
                      points, content_sha256, created_at
                    ) values (
                      cast(:rubric_id as uuid), cast(:task_id as uuid), :task_key,
                      cast(:material_id as uuid), :version_no, 'official',
                      'verified', 'published', 20, cast(:criteria as jsonb),
                      cast(:points as jsonb), :content_sha256, :created_at
                    )
                    """
                ),
                {
                    "rubric_id": str(rubric_version),
                    "task_id": str(task_id),
                    "task_key": f"guokao-workflow:{task_id}",
                    "material_id": str(bound_material),
                    "version_no": version_no,
                    "criteria": json.dumps(criteria, ensure_ascii=False),
                    "points": json.dumps([point], ensure_ascii=False),
                    "content_sha256": _hash(f"rubric:{rubric_version}"),
                    "created_at": NOW,
                },
            )
        session.commit()
    return CatalogSeed(
        task_id=task_id,
        material_id=material_id,
        rubric_id=rubric_id,
        other_material_id=other_material_id,
        other_rubric_id=other_rubric_id,
    )


def _seed_user(db_session_factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '申论学习者')"
            ),
            {"user_id": user_id},
        )
        session.commit()
    return user_id


def _quota_used(session: Session, user_id: str) -> int:
    return int(
        session.execute(
            text(
                "select used_count from commerce.entitlement_usage_daily "
                "where user_id = cast(:user_id as uuid) and feature = 'essay_review'"
            ),
            {"user_id": user_id},
        ).scalar_one()
    )


def _reservation_status_counts(session: Session, user_id: str) -> dict[str, int]:
    rows = session.execute(
        text(
            "select status, count(*) as count "
            "from commerce.feature_usage_reservations "
            "where user_id = cast(:user_id as uuid) "
            "and feature = 'essay_review' group by status"
        ),
        {"user_id": user_id},
    ).mappings().all()
    return {str(row["status"]): int(row["count"]) for row in rows}


def _job_payload(session: Session, job_id: UUID) -> dict[str, Any]:
    payload = session.execute(
        text(
            "select payload from platform.background_jobs "
            "where job_id = cast(:job_id as uuid)"
        ),
        {"job_id": str(job_id)},
    ).scalar_one()
    return dict(payload)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()
