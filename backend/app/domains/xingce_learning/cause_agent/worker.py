from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.ai.agent_runtime.contracts import (
    CAUSE_AGENT_DEFINITION,
    AgentRuntimeViolation,
    fingerprint,
)
from app.domains.ai.agent_runtime.runtime import BoundedToolRuntime
from app.domains.ai.model_cost import (
    ModelCallLedgerRepository,
    ModelPriceCard,
)
from app.domains.ai.external_processing import endpoint_fingerprint
from app.domains.ai.governance.repository import (
    AIGovernanceRepository,
    generic_evidence_ref,
)
from app.domains.commerce.repository import CommerceRepository
from app.domains.xingce_learning.cause_agent.contracts import (
    CauseAgentEvidence,
    CauseAgentOutput,
    EvidenceInsufficient,
    scrub_cause_output_user_text,
    validate_grounded_output,
)
from app.domains.xingce_learning.cause_agent.provider import (
    CauseAgentProvider,
    CauseAgentProviderConfigurationError,
    CauseAgentProviderError,
    CauseAgentProviderResult,
    build_cause_agent_providers,
)
from app.domains.xingce_learning.cause_agent.repository import CauseAgentRepository
from app.domains.xingce_learning.cause_agent.service import CAUSE_AGENT_JOB_TYPE
from app.domains.xingce_learning.cause_agent.tools import CauseAgentTools
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository
from app.platform.jobs import BackgroundJob, BackgroundJobRepository, JobContractError
from app.platform.outbox import OutboxRepository


logger = logging.getLogger(__name__)


class CauseAgentWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: CauseAgentProvider | Any | None = None,
        providers: tuple[CauseAgentProvider | Any, ...] | None = None,
        price_card: ModelPriceCard | None = None,
        repository: CauseAgentRepository | None = None,
        attempt_repository: XingceAttemptRepository | None = None,
        jobs: BackgroundJobRepository | None = None,
        ledger: ModelCallLedgerRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._settings = settings
        if provider is not None and providers is not None:
            raise ValueError("pass either provider or providers, not both")
        self._providers = (
            tuple(providers)
            if providers is not None
            else (provider,)
            if provider is not None
            else build_cause_agent_providers(settings)
        )
        if not self._providers:
            raise ValueError("cause-agent requires at least one provider route")
        self._provider = self._providers[0]
        self._price_card = price_card or ModelPriceCard.from_settings(settings)
        self._repository = repository or CauseAgentRepository()
        self._attempts = attempt_repository or XingceAttemptRepository()
        self._jobs = jobs or BackgroundJobRepository()
        self._ledger = ledger or ModelCallLedgerRepository()
        self._outbox = outbox or OutboxRepository()

    def recover_stale_claims(
        self,
        session: Session,
        *,
        now: datetime | None = None,
    ) -> int:
        recovered_at = now or datetime.now(UTC)
        count = self._jobs.requeue_stale(
            session,
            job_types=(CAUSE_AGENT_JOB_TYPE,),
            stale_before=recovered_at - timedelta(minutes=15),
            now=recovered_at,
        )
        self._reconcile_persisted_terminal_jobs(session)
        session.commit()
        return count

    def reconcile_terminal_job(
        self,
        session: Session,
        *,
        job_id: UUID,
    ) -> BackgroundJob:
        """Idempotently converge a persisted terminated run and its job ledgers."""

        job = self._jobs.by_id(session, job_id=job_id)
        if job is None or job.job_type != CAUSE_AGENT_JOB_TYPE:
            raise JobContractError("cause-agent job does not exist")
        run = self._repository.current_run(
            session,
            run_id=str(job.payload["runId"]),
            user_id=str(job.payload["userId"]),
            for_update=True,
        )
        if run is None or str(run["state"]) not in {"terminated", "failed"}:
            raise JobContractError("cause-agent run is not terminal")
        final = self._reconcile_existing_terminal(
            session,
            job=job,
            run=run,
            code=str(run["termination_reason"] or "cause_agent_terminated"),
        )
        session.commit()
        return final

    def process_next(self, session: Session) -> BackgroundJob | None:
        job = self._jobs.claim_next(session, job_types=(CAUSE_AGENT_JOB_TYPE,))
        if job is None:
            session.rollback()
            return None
        session.commit()
        try:
            return self._process_claim(session, job=job)
        except Exception:
            logger.exception(
                "cause-agent worker failed unexpectedly",
                extra={"job_id": str(job.job_id), "job_type": job.job_type},
            )
            session.rollback()
            current = self._jobs.by_id(session, job_id=job.job_id)
            if (
                current is not None
                and current.status == "running"
                and current.claim_token == job.claim_token
                and job.claim_token is not None
            ):
                if current.attempt_count >= current.max_attempts:
                    run = self._repository.current_run(
                        session,
                        run_id=str(current.payload["runId"]),
                        user_id=str(current.payload["userId"]),
                        for_update=True,
                    )
                    if run is not None and str(run["state"]) in {"queued", "running"}:
                        return self._publish_termination(
                            session,
                            job=current,
                            run=run,
                            code="cause_agent_worker_unexpected",
                            evidence=None,
                        )
                    quota_state = self._settle_terminal_resources(
                        session,
                        job=current,
                        code="cause_agent_worker_unexpected",
                    )
                    failed = self._jobs.fail(
                        session,
                        job_id=current.job_id,
                        claim_token=current.claim_token,
                        error_code="cause_agent_worker_unexpected",
                        error_message="cause_agent_worker_unexpected",
                        retryable=False,
                        result={
                            **(current.result or {}),
                            "state": "failed",
                            "businessOutcome": "terminated",
                            "quotaState": quota_state,
                        },
                    )
                    session.commit()
                    return failed
                failed = self._jobs.fail(
                    session,
                    job_id=job.job_id,
                    claim_token=job.claim_token,
                    error_code="cause_agent_worker_unexpected",
                    error_message="cause_agent_worker_unexpected",
                    retryable=True,
                )
                session.commit()
                return failed
            raise

    def _process_claim(self, session: Session, *, job: BackgroundJob) -> BackgroundJob:
        if job.claim_token is None:
            raise JobContractError("cause-agent job is missing its active claim")
        payload = job.payload
        self._validate_job_contract(payload)
        run = self._repository.current_run(
            session,
            run_id=str(payload["runId"]),
            user_id=str(payload["userId"]),
            for_update=True,
        )
        if run is None or str(run["job_id"]) != str(job.job_id):
            return self._fail_job(
                session,
                job=job,
                code="cause_agent_run_not_current",
                retryable=False,
            )
        if str(run["state"]) not in {"queued", "running"}:
            if str(run["state"]) in {"terminated", "failed"}:
                final = self._reconcile_existing_terminal(
                    session,
                    job=job,
                    run=run,
                    code=str(run["termination_reason"] or "cause_agent_terminated"),
                )
            else:
                final = self._jobs.succeed(
                    session,
                    job_id=job.job_id,
                    claim_token=job.claim_token,
                    result={
                        "runId": str(run["run_id"]),
                        "state": str(run["state"]),
                        "businessOutcome": "completed",
                    },
                )
            session.commit()
            return final
        try:
            evidence = self._load_or_execute_tools(session, job=job, run=run)
        except AgentRuntimeViolation as exc:
            return self._publish_termination(
                session,
                job=job,
                run=run,
                code=exc.code,
                evidence=None,
            )
        followup_answer = self._repository.latest_followup_answer(
            session,
            user_id=str(run["user_id"]),
            review_id=str(run["review_id"]),
        )
        try:
            output, output_fingerprint = self._load_or_call_provider(
                session,
                job=job,
                run=run,
                evidence=evidence,
                followup_answer=followup_answer,
            )
            validate_grounded_output(
                output=output,
                evidence=evidence,
                followup_round=int(run["followup_round"]),
            )
            # 校验通过后、落库前，把 AI 输出里面向用户的文本中的内部 question id
            # 替换成中性说法，避免 cq_xxx 泄漏到用户看到的候选内容。
            output = scrub_cause_output_user_text(output)
        except AgentRuntimeViolation as exc:
            return self._publish_termination(
                session,
                job=job,
                run=run,
                code=exc.code,
                evidence=evidence,
            )
        except (ValueError, ValidationError):
            return self._publish_termination(
                session,
                job=job,
                run=run,
                code="cause_agent_output_not_grounded",
                evidence=evidence,
            )
        except CauseAgentProviderConfigurationError:
            if job.attempt_count < job.max_attempts:
                return self._fail_job(
                    session,
                    job=job,
                    code="cause_agent_provider_configuration_invalid",
                    retryable=True,
                )
            return self._publish_termination(
                session,
                job=job,
                run=run,
                code="cause_agent_provider_configuration_invalid",
                evidence=evidence,
            )
        except CauseAgentProviderError as exc:
            if exc.retryable and job.attempt_count < job.max_attempts:
                return self._fail_job(
                    session,
                    job=job,
                    code=exc.code,
                    retryable=True,
                )
            return self._publish_termination(
                session,
                job=job,
                run=run,
                code=exc.code,
                evidence=evidence,
            )
        return self._publish_output(
            session,
            job=job,
            run=run,
            output=output,
            output_fingerprint=output_fingerprint,
        )

    def _load_or_execute_tools(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        run: Any,
    ) -> CauseAgentEvidence:
        if isinstance(job.result, dict) and isinstance(job.result.get("evidence"), dict):
            return CauseAgentEvidence.model_validate(job.result["evidence"])
        if int(run["followup_round"]) == 1:
            prior = job.payload.get("priorEvidence")
            if not isinstance(prior, dict):
                raise AgentRuntimeViolation("cause_followup_prior_evidence_missing")
            return CauseAgentEvidence.model_validate(prior)
        scope = {
            "userId": str(run["user_id"]),
            "sessionId": str(run["session_id"]),
            "reflectionVersionId": str(run["reflection_version_id"]),
        }
        tools = CauseAgentTools(
            session=session,
            repository=self._repository,
            user_id=str(run["user_id"]),
            session_id=str(run["session_id"]),
            reflection_version_id=str(run["reflection_version_id"]),
            input_version_refs=list(run["input_version_refs"]),
        )

        def event_sink(event: dict[str, Any]) -> None:
            self._repository.insert_tool_event(
                session,
                values={
                    "event_id": str(uuid4()),
                    "call_id": event["callId"],
                    "run_id": str(run["run_id"]),
                    "run_version_id": str(run["run_version_id"]),
                    "user_id": str(run["user_id"]),
                    "review_id": str(run["review_id"]),
                    "session_id": str(run["session_id"]),
                    "sequence_no": event["sequenceNo"],
                    "event_type": event["eventType"],
                    "tool_name": event["toolName"],
                    "tool_schema_version": event["toolSchemaVersion"],
                    "tool_behavior_version": event["toolBehaviorVersion"],
                    "permission_policy_version": event["permissionPolicyVersion"],
                    "input_fingerprint": event["inputFingerprint"],
                    "output_fingerprint": event.get("outputFingerprint"),
                    "progress_fingerprint": event.get("progressFingerprint"),
                    "error_code": event.get("errorCode"),
                    "occurred_at": datetime.now(UTC),
                },
            )

        runtime = BoundedToolRuntime(
            definition=CAUSE_AGENT_DEFINITION,
            handlers=tools.handlers,
            event_sink=event_sink,
        )
        results = {
            name: runtime.execute(
                tool_name=name,
                arguments=scope,
                progress_scope={"completedTool": name},
            ).payload
            for name in (
                "load_attempt_evidence",
                "load_user_reflection",
                "load_published_question_explanation",
                "load_cause_taxonomy",
                "load_action_trace",
                "load_ink_formulas",
            )
        }
        evidence = CauseAgentEvidence(
            attempt=results["load_attempt_evidence"],
            reflection=results["load_user_reflection"],
            explanations=results["load_published_question_explanation"],
            taxonomy=results["load_cause_taxonomy"],
            action_trace=results["load_action_trace"],
            ink_formulas=results["load_ink_formulas"],
            scratch_reasoning=results["load_action_trace"].get(
                "scratchReasoning", {}
            ),
        )
        self._jobs.checkpoint(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result={
                "phase": "tools_completed",
                "evidence": evidence.model_dump(by_alias=True, mode="json"),
                "toolCallCount": runtime.tool_call_count,
            },
        )
        session.commit()
        return evidence

    def _load_or_call_provider(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        run: Any,
        evidence: CauseAgentEvidence,
        followup_answer: str | None,
    ) -> tuple[CauseAgentOutput, str]:
        current_job = self._jobs.by_id(session, job_id=job.job_id)
        checkpoint = current_job.result if current_job else None
        provider_payload = (
            checkpoint.get("providerOutput") if isinstance(checkpoint, dict) else None
        )
        if isinstance(provider_payload, dict):
            output = TypeAdapter(CauseAgentOutput).validate_python(provider_payload)
            return output, str(checkpoint["outputFingerprint"])
        round_no = int(run["followup_round"])
        if int(run["model_call_count"]) >= CAUSE_AGENT_DEFINITION.budget.max_model_calls:
            raise AgentRuntimeViolation("model_call_budget_exhausted")
        # 全对短路：attempt 里没有任何非正确结果时，没有错因可整理，不调模型。
        # 模型在“全对”时倾向硬凑 candidate_ready，这里从数据上保证一定走
        # evidence_insufficient，避免让用户被迫确认一个不存在的错因。
        result_codes = set(evidence.attempt_result_codes.values())
        if not result_codes or result_codes <= {"correct", "guessed_correct"}:
            # 全对短路：evidence_refs 来自各证据段，空证据时回退到 run 的版本引用，
            # 避免 EvidenceInsufficient 的 evidence_refs min_length=1 校验失败。
            evidence_refs = sorted(evidence.allowed_evidence_refs)[:20]
            if not evidence_refs:
                evidence_refs = list(run.get("input_version_refs") or [])
            output = EvidenceInsufficient(
                reason_code=(
                    "no_incorrect_attempt" if result_codes else "no_reviewable_attempt"
                ),
                explanation=(
                    "本次练习全部作答正确，没有需要整理错因的题目。"
                    if result_codes
                    else "本次没有可用于错因分析的真实作答；未到达的题不会被判成不会。"
                ),
                missing_evidence=[
                    (
                        "attempt includes no incorrect or timed-out answered question"
                        if result_codes
                        else "attempt includes no reviewable answered question"
                    )
                ],
                evidence_refs=evidence_refs,
            )
            output_fingerprint = fingerprint(
                {
                    "allCorrectShortCircuit": True,
                    "output": output.model_dump(by_alias=True, mode="json"),
                }
            )
            # 短路路径不调模型、不建额度预留：显式在 checkpoint 写 not_charged，
            # 否则 _publish_output 会读到 None 落到 "reserved"，额度悬挂不消费不退款。
            current_job = self._jobs.by_id(session, job_id=job.job_id)
            base = current_job.result if current_job and current_job.result else {}
            self._jobs.checkpoint(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={
                    **base,
                    "phase": "provider_completed",
                    "providerOutput": output.model_dump(by_alias=True, mode="json"),
                    "outputFingerprint": output_fingerprint,
                    "quotaState": "not_charged",
                },
            )
            # 服务层入队时已创建 reserved 预留；短路不消费，必须释放为 refunded，
            # 否则额度永久悬挂。与 _settle_terminal_resources 的 no-consumption 分支一致。
            self._settle_terminal_resources(
                session,
                job=job,
                code="no_incorrect_attempt",
            )
            session.commit()
            return output, output_fingerprint
        input_fingerprint = fingerprint(
            {
                "runInputFingerprint": str(run["input_fingerprint"]),
                "evidence": evidence.model_dump(by_alias=True, mode="json"),
                "followupAnswer": followup_answer,
            }
        )
        remaining_budget = (
            CAUSE_AGENT_DEFINITION.budget.max_model_calls
            - int(run["model_call_count"])
        )
        route_set = self._providers[: min(3, remaining_budget)]
        if not route_set:
            raise AgentRuntimeViolation("model_call_budget_exhausted")
        route_attempts: list[dict[str, str]] = []
        for index, provider in enumerate(route_set):
            provider.validate_configuration()
            route_key = str(getattr(provider, "route_key", f"route_{index + 1}"))
            call_key = (
                f"xingce-cause:{run['run_id']}:round:{round_no}:route:{route_key}"
            )
            base_url = str(
                getattr(provider, "base_url", self._settings.ai_provider_base_url)
            )
            existing = self._ledger.get_existing(
                session,
                call_key=call_key,
                user_id=str(run["user_id"]),
                job_id=job.job_id,
                feature="xingce_cause_analysis",
                operation="cause_candidate_generate",
                input_fingerprint=input_fingerprint,
            )
            record = existing or self._ledger.begin(
                session,
                call_key=call_key,
                user_id=str(run["user_id"]),
                job_id=job.job_id,
                feature="xingce_cause_analysis",
                operation="cause_candidate_generate",
                provider=provider.provider_name,
                model=provider.model,
                prompt_version=provider.prompt_version,
                schema_version=provider.schema_version,
                price_card=self._price_card,
                input_fingerprint=input_fingerprint,
                attribution=AIGovernanceRepository().attribution(
                    session,
                    environment=self._settings.environment,
                    scenario_key="xingce_cause_analysis",
                    operation="cause_candidate_generate",
                    provider=provider.provider_name,
                    model=provider.model,
                    prompt_version=provider.prompt_version,
                    schema_version=provider.schema_version,
                    evidence_refs=tuple(
                        generic_evidence_ref(
                            kind="xingce_cause_evidence",
                            source_id=value,
                            version=value,
                        )
                        for value in sorted(evidence.allowed_evidence_refs)[:50]
                    ),
                    retrieval_bundle_id=None,
                    reservation_id=UUID(str(job.payload["reservationId"])),
                    reservation_source=str(job.payload["reservationSource"]),
                    price_version=self._price_card.version,
                    # 个人测试发布不以企业式“预先登记评测发布证明”
                    # 作为模型调用门禁。调用归因、证据指纹和成本流水仍落库。
                    enforce_evaluated_deployment=False,
                ),
                processor_release_id=provider.provider_release_id,
                processor_endpoint_fingerprint=endpoint_fingerprint(base_url),
                processing_purpose="cause_candidate_generate",
                processing_data_categories=("learning_evidence", "learner_answer"),
            )
            session.commit()
            has_next_route = index + 1 < len(route_set)
            replayable_existing = (
                existing is not None
                and existing.status == "running"
                and str(
                    getattr(
                        provider,
                        "idempotency_capability",
                        "replayable_queryable",
                    )
                )
                == "replayable_queryable"
            )
            if existing is not None and not replayable_existing:
                recovered_code = str(
                    record.error_code
                    or (
                        "provider_replay_output_unavailable"
                        if record.status == "succeeded"
                        else "provider_transport_uncertain"
                    )
                )
                if record.status == "running":
                    record = self._ledger.fail_if_running(
                        session,
                        call_id=record.call_id,
                        error_code="provider_transport_uncertain",
                        latency_ms=0,
                    )
                    recovered_code = "provider_transport_uncertain"
                    session.commit()
                route_attempts.append(
                    {
                        "route": route_key,
                        "model": provider.model,
                        "status": str(record.status),
                        "errorCode": recovered_code,
                    }
                )
                failover_safe = _provider_error_allows_failover(recovered_code)
                if failover_safe and has_next_route:
                    continue
                raise CauseAgentProviderError(
                    recovered_code,
                    "cause-agent provider replay cannot produce a new result",
                    retryable=False,
                    failover_safe=failover_safe,
                )
            try:
                result: CauseAgentProviderResult = provider.generate(
                    evidence=evidence,
                    followup_answer=followup_answer,
                    idempotency_key=call_key,
                )
            except CauseAgentProviderError as exc:
                if exc.usage is not None and exc.output_fingerprint is not None:
                    self._ledger.succeed(
                        session,
                        call_id=record.call_id,
                        usage=exc.usage,
                        output_fingerprint=exc.output_fingerprint,
                        latency_ms=exc.latency_ms,
                    )
                    self._consume_reservation(session, job=job)
                    attempt_status = "response_rejected"
                elif (exc.failover_safe and has_next_route) or not exc.retryable:
                    self._ledger.fail_if_running(
                        session,
                        call_id=record.call_id,
                        error_code=exc.code,
                        latency_ms=exc.latency_ms,
                    )
                    attempt_status = "failed"
                else:
                    attempt_status = "retry_pending"
                route_attempts.append(
                    {
                        "route": route_key,
                        "model": provider.model,
                        "status": attempt_status,
                        "errorCode": exc.code,
                    }
                )
                session.commit()
                if exc.failover_safe and has_next_route:
                    continue
                if exc.usage is not None and exc.output_fingerprint is not None:
                    raise CauseAgentProviderError(
                        exc.code,
                        "cause-agent provider response was already billed and cannot be replayed",
                        retryable=False,
                        failover_safe=exc.failover_safe,
                        usage=exc.usage,
                        output_fingerprint=exc.output_fingerprint,
                        latency_ms=exc.latency_ms,
                    ) from exc
                raise
            self._ledger.succeed(
                session,
                call_id=record.call_id,
                usage=result.usage,
                output_fingerprint=result.output_fingerprint,
                latency_ms=result.latency_ms,
            )
            self._consume_reservation(session, job=job)
            route_attempts.append(
                {
                    "route": route_key,
                    "model": provider.model,
                    "status": "succeeded",
                    "errorCode": "",
                }
            )
            current_job = self._jobs.by_id(session, job_id=job.job_id)
            base = current_job.result if current_job and current_job.result else {}
            self._jobs.checkpoint(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={
                    **base,
                    "phase": "provider_completed",
                    "providerOutput": result.output.model_dump(
                        by_alias=True, mode="json"
                    ),
                    "outputFingerprint": result.output_fingerprint,
                    "modelCallId": str(record.call_id),
                    "providerRoute": route_key,
                    "providerReleaseId": provider.provider_release_id,
                    "providerModel": provider.model,
                    "providerAttempts": route_attempts,
                    "providerRequestIdHeader": result.provider_request_id_header,
                    "providerRequestIdFingerprint": (
                        result.provider_request_id_fingerprint
                    ),
                    "quotaState": "consumed",
                },
            )
            session.commit()
            return result.output, result.output_fingerprint
        raise CauseAgentProviderError(
            "provider_routes_exhausted",
            "cause-agent provider routes were exhausted",
            retryable=False,
        )

    def _publish_output(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        run: Any,
        output: CauseAgentOutput,
        output_fingerprint: str,
        published_state: str | None = None,
        termination_reason: str | None = None,
        model_call_delta: int | None = None,
    ) -> BackgroundJob:
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"xingce-cause-terminal:{job.job_id}"},
        )
        current_run = self._repository.current_run(
            session,
            run_id=str(run["run_id"]),
            user_id=str(run["user_id"]),
            for_update=True,
        )
        if current_run is None:
            raise JobContractError("cause-agent run disappeared before publication")
        if str(current_run["state"]) not in {"queued", "running"}:
            if str(current_run["state"]) in {"terminated", "failed"}:
                final = self._reconcile_existing_terminal(
                    session,
                    job=job,
                    run=current_run,
                    code=str(current_run["termination_reason"] or "cause_agent_terminated"),
                )
            else:
                current_job = self._jobs.by_id(
                    session,
                    job_id=job.job_id,
                    for_update=True,
                )
                if current_job is None:
                    raise JobContractError("cause-agent job disappeared after publication")
                if current_job.status == "succeeded":
                    final = current_job
                elif (
                    current_job.status == "running"
                    and current_job.claim_token == job.claim_token
                    and job.claim_token is not None
                ):
                    final = self._jobs.succeed(
                        session,
                        job_id=job.job_id,
                        claim_token=job.claim_token,
                        result={
                            **(current_job.result or {}),
                            "runId": str(current_run["run_id"]),
                            "state": str(current_run["state"]),
                            "businessOutcome": "completed",
                        },
                    )
                else:
                    raise JobContractError("cause-agent published run disagrees with its job")
            session.commit()
            return final
        current_job = self._jobs.by_id(session, job_id=job.job_id) or job
        if model_call_delta is None:
            model_call_delta = len(
                self._ledger.records_for_job(session, job_id=job.job_id)
            )
        candidate_state = published_state or output.kind
        if candidate_state == "terminated":
            quota_state = self._settle_terminal_resources(
                session,
                job=job,
                code=termination_reason or "cause_agent_terminated",
            )
        else:
            result_checkpoint = current_job.result
            quota_state = (
                str(result_checkpoint.get("quotaState"))
                if isinstance(result_checkpoint, dict)
                and result_checkpoint.get("quotaState") in {"consumed", "reserved", "not_charged"}
                else "reserved"
            )
        previous = self._attempts.latest_cause_candidate(
            session,
            user_id=str(run["user_id"]),
            review_id=str(run["review_id"]),
        )
        candidate_id = str(uuid4())
        payload = {
            "contractVersion": "cause-candidate@v2",
            **output.model_dump(by_alias=True, mode="json"),
        }
        payload["quotaState"] = quota_state
        if termination_reason is not None:
            payload["terminationReason"] = termination_reason
        reason_code = termination_reason or (
            output.reason_code if isinstance(output, EvidenceInsufficient) else None
        )
        candidate = self._attempts.insert_cause_candidate(
            session,
            values={
                "candidate_version_id": candidate_id,
                "command_id": str(job.payload["commandId"]),
                "user_id": str(run["user_id"]),
                "review_id": str(run["review_id"]),
                "session_id": str(run["session_id"]),
                "version_no": int(previous["version_no"]) + 1 if previous else 1,
                "state": candidate_state,
                "reason_code": reason_code,
                "input_version_refs": _json(list(run["input_version_refs"])),
                "agent_run_id": str(run["run_id"]),
                "payload": _json(payload),
                "output_fingerprint": output_fingerprint,
                "entitlement_policy_version": str(job.payload["entitlementPolicyVersion"]),
                "supersedes_candidate_version_id": (
                    str(previous["candidate_version_id"]) if previous else None
                ),
                "created_at": datetime.now(UTC),
            },
        )
        tool_count = int(current_run["tool_call_count"])
        current_tool_count = int(
            session.execute(
                text(
                    "select count(distinct call_id) "
                    "from ai_audit.agent_tool_call_events "
                    "where run_version_id = cast(:run_version_id as uuid) "
                    "and event_type = 'succeeded'"
                ),
                {"run_version_id": str(current_run["run_version_id"])},
            ).scalar_one()
        )
        terminal = self._repository.insert_run_version(
            session,
            values=_next_run_values(
                current_run,
                state=candidate_state,
                candidate_version_id=candidate_id,
                progress_fingerprint=output_fingerprint,
                model_call_count=int(current_run["model_call_count"]) + model_call_delta,
                tool_call_count=tool_count + current_tool_count,
                termination_reason=termination_reason,
            ),
        )
        review = self._attempts.current_review(
            session,
            user_id=str(run["user_id"]),
            review_id=str(run["review_id"]),
            for_update=True,
        )
        if review is None or str(review["state"]) != "cause_candidate_pending":
            raise JobContractError("cause-agent review changed before publication")
        review_state = {
            "candidate_ready": "cause_decision_due",
            "followup_required": "cause_followup_due",
            "evidence_insufficient": "cause_evidence_insufficient",
            "terminated": "cause_agent_terminated",
        }[candidate_state]
        self._attempts.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": str(job.payload["commandId"]),
                "review_id": str(review["review_id"]),
                "user_id": str(run["user_id"]),
                "session_id": str(run["session_id"]),
                "revision": int(review["revision"]) + 1,
                "state": review_state,
                "requires_cause_decision": True,
                "reflection_version_id": str(run["reflection_version_id"]),
                "cause_candidate_version_id": candidate_id,
                "cause_decision_version_id": None,
                "supersedes_review_version_id": str(review["review_version_id"]),
                "created_at": datetime.now(UTC),
            },
        )
        event_id = uuid4()
        self._outbox.append(
            session,
            event_id=event_id,
            deduplication_key=f"xingce-cause-agent:{run['run_id']}:{terminal['version_no']}",
            aggregate_type="xingce_cause_agent",
            aggregate_id=str(run["run_id"]),
            event_type="xingce-cause-agent-output-published@v1",
            payload={
                "eventId": str(event_id),
                "eventType": "xingce-cause-agent-output-published@v1",
                "aggregateId": str(run["run_id"]),
                "aggregateRevision": int(terminal["version_no"]),
                "occurredAt": datetime.now(UTC).isoformat(),
                "producer": "xingce_learning",
                "trackId": "xingce",
                "correlationId": str(job.payload["commandId"]),
                "causationId": str(job.payload["commandId"]),
                "payload": {
                    "candidateVersionId": candidate_id,
                    "state": candidate_state,
                    "quotaState": quota_state,
                },
                "schemaVersion": 1,
            },
        )
        job_result = {
            **(current_job.result or {}),
            "candidateVersionId": str(candidate["candidate_version_id"]),
            "runId": str(run["run_id"]),
            "state": candidate_state,
            "businessOutcome": ("terminated" if candidate_state == "terminated" else "completed"),
            "quotaState": quota_state,
        }
        if candidate_state == "terminated":
            if job.claim_token is None:
                raise JobContractError("cause-agent termination lost its claim")
            final = self._jobs.fail(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                error_code=termination_reason or "cause_agent_terminated",
                error_message=termination_reason or "cause_agent_terminated",
                retryable=False,
                result=job_result,
            )
        else:
            if job.claim_token is None:
                raise JobContractError("cause-agent publication lost its claim")
            final = self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result=job_result,
            )
        session.commit()
        return final

    def _publish_termination(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        run: Any,
        code: str,
        evidence: CauseAgentEvidence | None,
    ) -> BackgroundJob:
        evidence_refs = (
            sorted(evidence.allowed_evidence_refs)[:20]
            if evidence is not None
            else list(run["input_version_refs"])[:20]
        )
        provider_failure = _provider_error_is_route_failure(code)
        output = EvidenceInsufficient(
            reasonCode=code,
            explanation=(
                "模型线路暂时都没能完成判断。你的作答、用时、改选、排除和草稿证据已经保存，不需要重做。"
                if provider_failure
                else "岸岸还没有足够可核对的证据形成候选错因。"
            ),
            missingEvidence=(
                ["可以稍后刷新；也可选择性补充你自己的想法"]
                if provider_failure
                else ["可选择性补充当时的思路，或暂不归因"]
            ),
            evidenceRefs=evidence_refs,
        )
        terminal_payload = {
            "contractVersion": "cause-candidate@v2",
            **output.model_dump(by_alias=True, mode="json"),
            "terminationReason": code,
        }
        return self._publish_output(
            session,
            job=job,
            run=run,
            output=output,
            output_fingerprint=fingerprint(terminal_payload),
            published_state="terminated",
            termination_reason=code,
        )

    def _reconcile_persisted_terminal_jobs(self, session: Session) -> None:
        job_ids = (
            session.execute(
                text(
                    """
                select job.job_id
                from platform.background_jobs job
                join ai_audit.agent_run_versions run on run.job_id = job.job_id
                 and not exists (
                   select 1 from ai_audit.agent_run_versions successor
                   where successor.supersedes_run_version_id = run.run_version_id
                 )
                where job.job_type = :job_type
                  and run.state in ('terminated', 'failed')
                  and (
                    job.status <> 'failed'
                    or exists (
                      select 1 from ai_audit.model_call_ledger ledger
                      where ledger.job_id = job.job_id
                        and ledger.status = 'running'
                    )
                    or exists (
                      select 1 from commerce.feature_usage_reservations reservation
                      where reservation.job_id = job.job_id
                        and reservation.status = 'reserved'
                    )
                    or exists (
                      select 1
                      from commerce.community_reward_ai_usage_reservations reservation
                      where not exists (
                        select 1
                        from commerce.community_reward_ai_usage_reservation_events event
                        where event.reservation_id = reservation.reservation_id
                      )
                        and exists (
                          select 1 from commerce.entitlement_audit_logs binding
                          where binding.action = 'community_reward_ai_job_bound'
                            and binding.metadata ->> 'reservationId' =
                              reservation.reservation_id::text
                            and binding.metadata ->> 'jobId' = job.job_id::text
                        )
                    )
                  )
                order by job.created_at, job.job_id
                limit 100
                """
                ),
                {"job_type": CAUSE_AGENT_JOB_TYPE},
            )
            .scalars()
            .all()
        )
        for job_id in job_ids:
            job = self._jobs.by_id(session, job_id=job_id)
            if job is None:
                continue
            run = self._repository.current_run(
                session,
                run_id=str(job.payload["runId"]),
                user_id=str(job.payload["userId"]),
                for_update=True,
            )
            if run is None or str(run["state"]) not in {"terminated", "failed"}:
                continue
            self._reconcile_existing_terminal(
                session,
                job=job,
                run=run,
                code=str(run["termination_reason"] or "cause_agent_terminated"),
            )

    def _reconcile_existing_terminal(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        run: Any,
        code: str,
    ) -> BackgroundJob:
        quota_state = self._settle_terminal_resources(
            session,
            job=job,
            code=code,
        )
        current_job = self._jobs.by_id(
            session,
            job_id=job.job_id,
            for_update=True,
        )
        if current_job is None:
            raise JobContractError("cause-agent terminal job disappeared")
        if current_job.status == "failed":
            return current_job
        return self._jobs.reconcile_business_failure(
            session,
            job_id=current_job.job_id,
            error_code=code,
            error_message=code,
            result={
                **(current_job.result or {}),
                "runId": str(run["run_id"]),
                "state": str(run["state"]),
                "businessOutcome": "terminated",
                "quotaState": quota_state,
            },
        )

    def _settle_terminal_resources(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        code: str,
    ) -> str:
        records = self._ledger.records_for_job(
            session,
            job_id=job.job_id,
            for_update=True,
        )
        settled_records = []
        for ledger in records:
            if ledger.status == "running":
                ledger = self._ledger.fail_if_running(
                    session,
                    call_id=ledger.call_id,
                    error_code=code,
                    latency_ms=0,
                )
            settled_records.append(ledger)
        should_consume = any(
            ledger.status == "succeeded" for ledger in settled_records
        )

        commerce = CommerceRepository(session)
        reservation = commerce.feature_usage_reservation_by_key(
            reservation_key=str(job.payload["reservationKey"]),
            for_update=True,
        )
        if reservation is None:
            raise JobContractError("cause-agent quota reservation does not exist")
        if (
            str(reservation.reservation_id) != str(job.payload["reservationId"])
            or reservation.source != str(job.payload["reservationSource"])
            or (reservation.job_id is not None and reservation.job_id != job.job_id)
        ):
            raise JobContractError("cause-agent quota reservation identity changed")

        if should_consume:
            if reservation.status == "refunded":
                raise JobContractError("succeeded model call has a refunded reservation")
            if reservation.status == "reserved":
                commerce.consume_feature_usage(
                    reservation_key=reservation.reservation_key,
                    commit=False,
                )
            return "consumed"

        if reservation.status == "consumed":
            raise JobContractError("failed or absent model call has a consumed reservation")
        if reservation.status == "reserved":
            commerce.refund_feature_usage(
                reservation_key=reservation.reservation_key,
                commit=False,
            )
        return "not_charged"

    def _fail_job(
        self,
        session: Session,
        *,
        job: BackgroundJob,
        code: str,
        retryable: bool,
    ) -> BackgroundJob:
        if job.claim_token is None:
            raise JobContractError("cause-agent job lost its claim")
        result: dict[str, Any] | None = None
        if not retryable:
            quota_state = self._settle_terminal_resources(
                session,
                job=job,
                code=code,
            )
            result = {
                **(job.result or {}),
                "state": "failed",
                "businessOutcome": "terminated",
                "quotaState": quota_state,
            }
        failed = self._jobs.fail(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_code=code,
            error_message=code,
            retryable=retryable,
            result=result,
        )
        session.commit()
        return failed

    def _validate_job_contract(self, payload: dict[str, Any]) -> None:
        required = {
            "runId",
            "userId",
            "reviewId",
            "sessionId",
            "reflectionVersionId",
            "definitionReleaseId",
            "definitionSha256",
            "inputFingerprint",
            "followupRound",
            "commandId",
            "provider",
            "providerReleaseId",
            "model",
            "promptVersion",
            "schemaVersion",
            "entitlementPolicyVersion",
            "reservationId",
            "reservationKey",
            "reservationSource",
        }
        if not required.issubset(payload):
            raise JobContractError("cause-agent job payload is incomplete")
        if (
            payload["definitionReleaseId"] != CAUSE_AGENT_DEFINITION.definition_release_id
            or payload["definitionSha256"] != CAUSE_AGENT_DEFINITION.fingerprint
            or payload["provider"] != self._provider.provider_name
            or payload["providerReleaseId"] != self._provider.provider_release_id
            or payload["model"] != self._provider.model
            or payload["promptVersion"] != self._provider.prompt_version
            or payload["schemaVersion"] != self._provider.schema_version
        ):
            raise JobContractError("cause-agent queued release differs from worker release")

    def _consume_reservation(self, session: Session, *, job: BackgroundJob) -> None:
        CommerceRepository(session).consume_feature_usage(
            reservation_key=str(job.payload["reservationKey"]),
            commit=False,
        )


def _next_run_values(
    current: Any,
    *,
    state: str,
    candidate_version_id: str,
    progress_fingerprint: str,
    model_call_count: int,
    tool_call_count: int,
    termination_reason: str | None = None,
) -> dict[str, object]:
    return {
        "run_version_id": str(uuid4()),
        "run_id": str(current["run_id"]),
        "version_no": int(current["version_no"]) + 1,
        "user_id": str(current["user_id"]),
        "review_id": str(current["review_id"]),
        "session_id": str(current["session_id"]),
        "reflection_version_id": str(current["reflection_version_id"]),
        "definition_release_id": str(current["definition_release_id"]),
        "state": state,
        "job_id": str(current["job_id"]) if current["job_id"] else None,
        "input_version_refs": _json(list(current["input_version_refs"])),
        "input_fingerprint": str(current["input_fingerprint"]),
        "provider": str(current["provider"]),
        "model": str(current["model"]),
        "provider_release_id": str(current["provider_release_id"]),
        "prompt_version": str(current["prompt_version"]),
        "schema_version": str(current["schema_version"]),
        "toolset_snapshot": _json(list(current["toolset_snapshot"])),
        "budget_snapshot": _json(dict(current["budget_snapshot"])),
        "progress_fingerprint": progress_fingerprint,
        "model_call_count": model_call_count,
        "tool_call_count": tool_call_count,
        "consecutive_no_progress": int(current["consecutive_no_progress"]),
        "followup_round": int(current["followup_round"]),
        "termination_reason": termination_reason,
        "candidate_version_id": candidate_version_id,
        "supersedes_run_version_id": str(current["run_version_id"]),
        "created_at": datetime.now(UTC),
    }


def _provider_error_allows_failover(code: str) -> bool:
    if code in {
        "provider_transport_uncertain",
        "provider_response_invalid",
        "provider_replay_output_unavailable",
    }:
        return True
    if not code.startswith("provider_http_"):
        return False
    try:
        status_code = int(code.removeprefix("provider_http_"))
    except ValueError:
        return False
    return status_code in {408, 429} or status_code >= 500


def _provider_error_is_route_failure(code: str) -> bool:
    return code.startswith("provider_") or code in {
        "cause_agent_provider_configuration_invalid",
        "provider_routes_exhausted",
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
