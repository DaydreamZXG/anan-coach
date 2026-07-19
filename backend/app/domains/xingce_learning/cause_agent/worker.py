from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
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
    validate_grounded_output,
)
from app.domains.xingce_learning.cause_agent.provider import (
    CauseAgentProvider,
    CauseAgentProviderConfigurationError,
    CauseAgentProviderError,
    CauseAgentProviderResult,
)
from app.domains.xingce_learning.cause_agent.repository import CauseAgentRepository
from app.domains.xingce_learning.cause_agent.service import CAUSE_AGENT_JOB_TYPE
from app.domains.xingce_learning.cause_agent.tools import CauseAgentTools
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository
from app.platform.jobs import BackgroundJob, BackgroundJobRepository, JobContractError
from app.platform.outbox import OutboxRepository


class CauseAgentWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        provider: CauseAgentProvider | Any | None = None,
        price_card: ModelPriceCard | None = None,
        repository: CauseAgentRepository | None = None,
        attempt_repository: XingceAttemptRepository | None = None,
        jobs: BackgroundJobRepository | None = None,
        ledger: ModelCallLedgerRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider or CauseAgentProvider(settings)
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
        session.commit()
        return count

    def process_next(self, session: Session) -> BackgroundJob | None:
        job = self._jobs.claim_next(session, job_types=(CAUSE_AGENT_JOB_TYPE,))
        if job is None:
            session.rollback()
            return None
        session.commit()
        try:
            return self._process_claim(session, job=job)
        except Exception:
            session.rollback()
            current = self._jobs.by_id(session, job_id=job.job_id)
            if current is not None and current.status == "running" and current.claim_token:
                failed = self._jobs.fail(
                    session,
                    job_id=current.job_id,
                    claim_token=current.claim_token,
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
            return self._jobs.succeed(
                session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={"runId": str(run["run_id"]), "state": str(run["state"])},
            )
        try:
            evidence = self._load_or_execute_tools(session, job=job, run=run)
        except AgentRuntimeViolation as exc:
            self._refund_reservation_if_open(session, job=job)
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
        except AgentRuntimeViolation as exc:
            self._refund_reservation_if_open(session, job=job)
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
            self._refund_reservation_if_open(session, job=job)
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
            )
        }
        evidence = CauseAgentEvidence(
            attempt=results["load_attempt_evidence"],
            reflection=results["load_user_reflection"],
            explanations=results["load_published_question_explanation"],
            taxonomy=results["load_cause_taxonomy"],
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
        self._provider.validate_configuration()
        call_key = f"xingce-cause:{run['run_id']}:round:{round_no}"
        input_fingerprint = fingerprint(
            {
                "runInputFingerprint": str(run["input_fingerprint"]),
                "evidence": evidence.model_dump(by_alias=True, mode="json"),
                "followupAnswer": followup_answer,
            }
        )
        record = self._ledger.begin(
            session,
            call_key=call_key,
            user_id=str(run["user_id"]),
            job_id=job.job_id,
            feature="xingce_cause_analysis",
            operation="cause_candidate_generate",
            provider=str(run["provider"]),
            model=str(run["model"]),
            prompt_version=str(run["prompt_version"]),
            schema_version=str(run["schema_version"]),
            price_card=self._price_card,
            input_fingerprint=input_fingerprint,
            attribution=AIGovernanceRepository().attribution(
                session,
                environment=self._settings.environment,
                scenario_key="xingce_cause_analysis",
                operation="cause_candidate_generate",
                provider=str(run["provider"]),
                model=str(run["model"]),
                prompt_version=str(run["prompt_version"]),
                schema_version=str(run["schema_version"]),
                evidence_refs=tuple(
                    generic_evidence_ref(
                        kind="xingce_cause_evidence",
                        source_id=value,
                        version=value,
                    )
                    for value in sorted(evidence.allowed_evidence_refs)
                ),
                retrieval_bundle_id=None,
                reservation_id=UUID(str(job.payload["reservationId"])),
                reservation_source=str(job.payload["reservationSource"]),
            ),
            processor_release_id=self._provider.provider_release_id,
            processor_endpoint_fingerprint=endpoint_fingerprint(
                self._settings.ai_provider_base_url
            ),
            processing_purpose="cause_candidate_generate",
            processing_data_categories=("learning_evidence", "learner_answer"),
        )
        session.commit()
        try:
            result: CauseAgentProviderResult = self._provider.generate(
                evidence=evidence,
                followup_answer=followup_answer,
                idempotency_key=call_key,
            )
        except CauseAgentProviderError as exc:
            if exc.usage is not None and exc.output_fingerprint is not None:
                if record.status == "running":
                    self._ledger.succeed(
                        session,
                        call_id=record.call_id,
                        usage=exc.usage,
                        output_fingerprint=exc.output_fingerprint,
                        latency_ms=exc.latency_ms,
                    )
                self._consume_reservation(session, job=job)
                session.commit()
            elif record.status == "running" and not exc.retryable:
                self._ledger.fail(
                    session,
                    call_id=record.call_id,
                    error_code=exc.code,
                    latency_ms=exc.latency_ms,
                )
                self._refund_reservation_if_open(session, job=job)
                session.commit()
            raise
        if record.status == "succeeded":
            if record.output_fingerprint != result.output_fingerprint:
                raise CauseAgentProviderError(
                    "provider_idempotency_violation",
                    "cause-agent provider changed a replayed response",
                    retryable=False,
                )
        else:
            self._ledger.succeed(
                session,
                call_id=record.call_id,
                usage=result.usage,
                output_fingerprint=result.output_fingerprint,
                latency_ms=result.latency_ms,
            )
        self._consume_reservation(session, job=job)
        current_job = self._jobs.by_id(session, job_id=job.job_id)
        base = current_job.result if current_job and current_job.result else {}
        self._jobs.checkpoint(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result={
                **base,
                "phase": "provider_completed",
                "providerOutput": result.output.model_dump(by_alias=True, mode="json"),
                "outputFingerprint": result.output_fingerprint,
                "modelCallId": str(record.call_id),
                "providerRequestIdHeader": result.provider_request_id_header,
                "providerRequestIdFingerprint": (
                    result.provider_request_id_fingerprint
                ),
                "quotaState": "consumed",
            },
        )
        session.commit()
        return result.output, result.output_fingerprint

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
        model_call_delta: int = 1,
    ) -> BackgroundJob:
        current_job = self._jobs.by_id(session, job_id=job.job_id) or job
        current_run = self._repository.current_run(
            session,
            run_id=str(run["run_id"]),
            user_id=str(run["user_id"]),
            for_update=True,
        )
        if current_run is None:
            raise JobContractError("cause-agent run disappeared before publication")
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
        result_checkpoint = current_job.result
        quota_state = (
            str(result_checkpoint.get("quotaState"))
            if isinstance(result_checkpoint, dict)
            and result_checkpoint.get("quotaState")
            in {"consumed", "reserved", "not_charged"}
            else "reserved"
        )
        payload["quotaState"] = quota_state
        candidate_state = published_state or output.kind
        if termination_reason is not None:
            payload["terminationReason"] = termination_reason
        reason_code = (
            termination_reason
            or (output.reason_code if isinstance(output, EvidenceInsufficient) else None)
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
                "entitlement_policy_version": str(
                    job.payload["entitlementPolicyVersion"]
                ),
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
                model_call_count=int(current_run["model_call_count"])
                + model_call_delta,
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
        final = self._jobs.succeed(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result={
                **(current_job.result or {}),
                "candidateVersionId": str(candidate["candidate_version_id"]),
                "state": candidate_state,
            },
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
        output = EvidenceInsufficient(
            reasonCode=code,
            explanation="解题 Agent 未能在冻结证据和预算内形成可确认候选。",
            missingEvidence=["需要用户自行补充或选择暂不归因"],
            evidenceRefs=evidence_refs,
        )
        terminal_payload = {
            "contractVersion": "cause-candidate@v2",
            **output.model_dump(by_alias=True, mode="json"),
            "terminationReason": code,
        }
        model_call_delta = int(
            session.execute(
                text(
                    "select exists (select 1 from ai_audit.model_call_ledger "
                    "where job_id = cast(:job_id as uuid))"
                ),
                {"job_id": str(job.job_id)},
            ).scalar_one()
        )
        return self._publish_output(
            session,
            job=job,
            run=run,
            output=output,
            output_fingerprint=fingerprint(terminal_payload),
            published_state="terminated",
            termination_reason=code,
            model_call_delta=model_call_delta,
        )

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
        failed = self._jobs.fail(
            session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_code=code,
            error_message=code,
            retryable=retryable,
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
            payload["definitionReleaseId"]
            != CAUSE_AGENT_DEFINITION.definition_release_id
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

    def _refund_reservation_if_open(
        self,
        session: Session,
        *,
        job: BackgroundJob,
    ) -> None:
        source = str(job.payload["reservationSource"])
        reservation_key = str(job.payload["reservationKey"])
        if source == "catalog":
            status = session.execute(
                text(
                    "select status from commerce.feature_usage_reservations "
                    "where reservation_key = :reservation_key"
                ),
                {"reservation_key": reservation_key},
            ).scalar_one_or_none()
        elif source == "community_reward":
            status = session.execute(
                text(
                    "select coalesce(event.event_type, 'reserved') "
                    "from commerce.community_reward_ai_usage_reservations reservation "
                    "left join commerce.community_reward_ai_usage_reservation_events event "
                    "on event.reservation_id = reservation.reservation_id "
                    "where reservation.reservation_key = :reservation_key"
                ),
                {"reservation_key": reservation_key},
            ).scalar_one_or_none()
        else:
            raise JobContractError("cause-agent reservation source is unsupported")
        if status == "reserved":
            CommerceRepository(session).refund_feature_usage(
                reservation_key=reservation_key,
                commit=False,
            )
            if job.claim_token is not None:
                current = self._jobs.by_id(session, job_id=job.job_id)
                base = current.result if current is not None and current.result else {}
                self._jobs.checkpoint(
                    session,
                    job_id=job.job_id,
                    claim_token=job.claim_token,
                    result={**base, "quotaState": "not_charged"},
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


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
