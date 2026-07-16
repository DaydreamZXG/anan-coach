from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Protocol
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domains.ai.agent_runtime.contracts import (
    CAUSE_AGENT_DEFINITION,
    fingerprint,
)
from app.domains.xingce_learning.cause_agent.contracts import (
    ENTITLEMENT_POLICY_VERSION,
)
from app.domains.xingce_learning.cause_agent.provider import CauseAgentProvider
from app.domains.xingce_learning.cause_agent.repository import CauseAgentRepository
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository
from app.platform.jobs import BackgroundJobRepository
from app.platform.outbox import OutboxRepository


CAUSE_AGENT_JOB_TYPE = "xingce_cause_agent_v1"
CAUSE_AGENT_JOB_INPUT_VERSION = "cause-agent-run@v1"


class CauseProviderIdentity(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def provider_release_id(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    @property
    def schema_version(self) -> str: ...

    def validate_configuration(self) -> None: ...


class CauseAgentWorkflowError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CauseAgentWorkflow:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        provider: CauseProviderIdentity | None = None,
        repository: CauseAgentRepository | None = None,
        attempt_repository: XingceAttemptRepository | None = None,
        jobs: BackgroundJobRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider or CauseAgentProvider(self._settings)
        self._repository = repository or CauseAgentRepository()
        self._attempts = attempt_repository or XingceAttemptRepository()
        self._jobs = jobs or BackgroundJobRepository()
        self._outbox = outbox or OutboxRepository()

    def start_after_reflection(
        self,
        session: Session,
        *,
        user_id: str,
        task_session: RowMapping,
        review: RowMapping,
        reflection_version_id: str,
        command_id: str,
        created_at: datetime,
    ) -> RowMapping:
        self._repository.ensure_definition(
            session,
            definition=CAUSE_AGENT_DEFINITION,
            created_at=created_at,
        )
        if self._all_correct_high_confidence(session, user_id, task_session):
            return self._publish_without_model(
                session,
                user_id=user_id,
                task_session=task_session,
                review=review,
                reflection_version_id=reflection_version_id,
                command_id=command_id,
                created_at=created_at,
            )
        self._provider.validate_configuration()
        run_id = str(uuid4())
        input_refs = [
            f"reflection-version:{reflection_version_id}",
            f"attempt-group:{task_session['attempt_group_id']}",
            f"task-session:{task_session['session_id']}:{task_session['revision']}",
        ]
        input_fingerprint = fingerprint(
            {
                "definition": CAUSE_AGENT_DEFINITION.fingerprint,
                "inputVersionRefs": input_refs,
                "provider": self._provider.provider_name,
                "providerReleaseId": self._provider.provider_release_id,
                "model": self._provider.model,
            }
        )
        payload = self._job_payload(
            run_id=run_id,
            user_id=user_id,
            review_id=str(review["review_id"]),
            session_id=str(task_session["session_id"]),
            reflection_version_id=reflection_version_id,
            input_fingerprint=input_fingerprint,
            followup_round=0,
            command_id=command_id,
        )
        job = self._jobs.enqueue(
            session,
            job_key=f"xingce-cause:{run_id}:round:0",
            job_type=CAUSE_AGENT_JOB_TYPE,
            input_version=CAUSE_AGENT_JOB_INPUT_VERSION,
            payload=payload,
            max_attempts=3,
            run_after=created_at,
        )
        run = self._repository.insert_run_version(
            session,
            values=self._run_values(
                run_id=run_id,
                version_no=1,
                user_id=user_id,
                review_id=str(review["review_id"]),
                session_id=str(task_session["session_id"]),
                reflection_version_id=reflection_version_id,
                state="queued",
                job_id=str(job.job_id),
                input_refs=input_refs,
                input_fingerprint=input_fingerprint,
                followup_round=0,
                supersedes=None,
                created_at=created_at,
            ),
        )
        queued_review = self._attempts.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": command_id,
                "review_id": str(review["review_id"]),
                "user_id": user_id,
                "session_id": str(task_session["session_id"]),
                "revision": int(review["revision"]) + 1,
                "state": "cause_candidate_pending",
                "requires_cause_decision": True,
                "reflection_version_id": reflection_version_id,
                "cause_candidate_version_id": None,
                "cause_decision_version_id": None,
                "supersedes_review_version_id": str(review["review_version_id"]),
                "created_at": created_at,
            },
        )
        self._append_event(
            session,
            run_id=run_id,
            user_id=user_id,
            review_id=str(review["review_id"]),
            command_id=command_id,
            event_type="xingce-cause-agent-queued@v1",
            revision=int(run["version_no"]),
            occurred_at=created_at,
            payload={"jobId": str(job.job_id), "quotaState": "not_metered"},
        )
        return queued_review

    def submit_followup(
        self,
        session: Session,
        *,
        user_id: str,
        task_session: RowMapping,
        review: RowMapping,
        candidate: RowMapping,
        command_id: str,
        answer: str,
        created_at: datetime,
    ) -> RowMapping:
        run = self._repository.current_run_for_review(
            session,
            user_id=user_id,
            review_id=str(review["review_id"]),
        )
        if run is None or str(run["state"]) != "followup_required":
            raise CauseAgentWorkflowError("cause_followup_run_not_current")
        if int(run["followup_round"]) >= CAUSE_AGENT_DEFINITION.budget.max_followup_rounds:
            raise CauseAgentWorkflowError("cause_followup_budget_exhausted")
        payload = dict(candidate["payload"])
        question = str(payload.get("question", "")).strip()
        if not question:
            raise CauseAgentWorkflowError("cause_followup_question_missing")
        self._repository.insert_followup(
            session,
            values={
                "followup_version_id": str(uuid4()),
                "command_id": command_id,
                "user_id": user_id,
                "review_id": str(review["review_id"]),
                "session_id": str(task_session["session_id"]),
                "run_id": str(run["run_id"]),
                "candidate_version_id": str(candidate["candidate_version_id"]),
                "version_no": 1,
                "question": question,
                "answer": answer,
                "payload_sha256": fingerprint({"question": question, "answer": answer}),
                "created_at": created_at,
            },
        )
        followup_round = 1
        input_refs = list(run["input_version_refs"]) + [
            f"cause-candidate-version:{candidate['candidate_version_id']}",
            f"cause-followup:{fingerprint(answer)}",
        ]
        input_fingerprint = fingerprint(
            {
                "previousInputFingerprint": str(run["input_fingerprint"]),
                "followupAnswer": answer,
                "round": followup_round,
            }
        )
        job_payload = self._job_payload(
            run_id=str(run["run_id"]),
            user_id=user_id,
            review_id=str(review["review_id"]),
            session_id=str(task_session["session_id"]),
            reflection_version_id=str(review["reflection_version_id"]),
            input_fingerprint=input_fingerprint,
            followup_round=followup_round,
            command_id=command_id,
            prior_evidence=self._prior_evidence(session, run),
        )
        job = self._jobs.enqueue(
            session,
            job_key=f"xingce-cause:{run['run_id']}:round:{followup_round}",
            job_type=CAUSE_AGENT_JOB_TYPE,
            input_version=CAUSE_AGENT_JOB_INPUT_VERSION,
            payload=job_payload,
            max_attempts=3,
            run_after=created_at,
        )
        queued = self._repository.insert_run_version(
            session,
            values=self._run_values(
                run_id=str(run["run_id"]),
                version_no=int(run["version_no"]) + 1,
                user_id=user_id,
                review_id=str(review["review_id"]),
                session_id=str(task_session["session_id"]),
                reflection_version_id=str(review["reflection_version_id"]),
                state="queued",
                job_id=str(job.job_id),
                input_refs=input_refs,
                input_fingerprint=input_fingerprint,
                followup_round=followup_round,
                supersedes=str(run["run_version_id"]),
                created_at=created_at,
                candidate_version_id=str(candidate["candidate_version_id"]),
                model_call_count=int(run["model_call_count"]),
                tool_call_count=int(run["tool_call_count"]),
            ),
        )
        result = self._attempts.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": command_id,
                "review_id": str(review["review_id"]),
                "user_id": user_id,
                "session_id": str(task_session["session_id"]),
                "revision": int(review["revision"]) + 1,
                "state": "cause_candidate_pending",
                "requires_cause_decision": True,
                "reflection_version_id": str(review["reflection_version_id"]),
                "cause_candidate_version_id": str(candidate["candidate_version_id"]),
                "cause_decision_version_id": None,
                "supersedes_review_version_id": str(review["review_version_id"]),
                "created_at": created_at,
            },
        )
        self._append_event(
            session,
            run_id=str(run["run_id"]),
            user_id=user_id,
            review_id=str(review["review_id"]),
            command_id=command_id,
            event_type="xingce-cause-followup-submitted@v1",
            revision=int(queued["version_no"]),
            occurred_at=created_at,
            payload={"jobId": str(job.job_id), "followupRound": followup_round},
        )
        return result

    def _publish_without_model(
        self,
        session: Session,
        *,
        user_id: str,
        task_session: RowMapping,
        review: RowMapping,
        reflection_version_id: str,
        command_id: str,
        created_at: datetime,
    ) -> RowMapping:
        run_id = str(uuid4())
        candidate_id = str(uuid4())
        input_refs = [
            f"reflection-version:{reflection_version_id}",
            f"attempt-group:{task_session['attempt_group_id']}",
        ]
        payload = {
            "contractVersion": "cause-candidate@v2",
            "kind": "evidence_insufficient",
            "reasonCode": "all_correct_high_confidence",
            "explanation": "本组全部高信心答对，没有足够错误证据生成错因候选。",
            "missingEvidence": ["真实错误、超时、蒙对或主动放弃证据"],
            "evidenceRefs": input_refs,
        }
        self._attempts.insert_cause_candidate(
            session,
            values={
                "candidate_version_id": candidate_id,
                "command_id": command_id,
                "user_id": user_id,
                "review_id": str(review["review_id"]),
                "session_id": str(task_session["session_id"]),
                "version_no": 1,
                "state": "evidence_insufficient",
                "reason_code": "all_correct_high_confidence",
                "input_version_refs": _json(input_refs),
                "agent_run_id": run_id,
                "payload": _json(payload),
                "output_fingerprint": fingerprint(payload),
                "entitlement_policy_version": ENTITLEMENT_POLICY_VERSION,
                "supersedes_candidate_version_id": None,
                "created_at": created_at,
            },
        )
        self._repository.insert_run_version(
            session,
            values=self._run_values(
                run_id=run_id,
                version_no=1,
                user_id=user_id,
                review_id=str(review["review_id"]),
                session_id=str(task_session["session_id"]),
                reflection_version_id=reflection_version_id,
                state="evidence_insufficient",
                job_id=None,
                input_refs=input_refs,
                input_fingerprint=fingerprint(input_refs),
                followup_round=0,
                supersedes=None,
                created_at=created_at,
                candidate_version_id=candidate_id,
                progress_fingerprint=fingerprint(payload),
            ),
        )
        return self._attempts.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": command_id,
                "review_id": str(review["review_id"]),
                "user_id": user_id,
                "session_id": str(task_session["session_id"]),
                "revision": int(review["revision"]) + 1,
                "state": "cause_evidence_insufficient",
                "requires_cause_decision": True,
                "reflection_version_id": reflection_version_id,
                "cause_candidate_version_id": candidate_id,
                "cause_decision_version_id": None,
                "supersedes_review_version_id": str(review["review_version_id"]),
                "created_at": created_at,
            },
        )

    def _all_correct_high_confidence(
        self,
        session: Session,
        user_id: str,
        task_session: RowMapping,
    ) -> bool:
        assessments = self._attempts.assessments(
            session,
            user_id=user_id,
            session_id=str(task_session["session_id"]),
        )
        answers = {
            str(item["questionId"]): dict(item)
            for item in dict(task_session["checkpoint"])["answers"]
        }
        return bool(assessments) and all(
            str(item["result_code"]) == "correct"
            and answers[str(item["question_id"])].get("confidence") == "high"
            for item in assessments
        )

    def _job_payload(
        self,
        *,
        run_id: str,
        user_id: str,
        review_id: str,
        session_id: str,
        reflection_version_id: str,
        input_fingerprint: str,
        followup_round: int,
        command_id: str,
        prior_evidence: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "contractVersion": CAUSE_AGENT_JOB_INPUT_VERSION,
            "runId": run_id,
            "userId": user_id,
            "reviewId": review_id,
            "sessionId": session_id,
            "reflectionVersionId": reflection_version_id,
            "definitionReleaseId": CAUSE_AGENT_DEFINITION.definition_release_id,
            "definitionSha256": CAUSE_AGENT_DEFINITION.fingerprint,
            "inputFingerprint": input_fingerprint,
            "followupRound": followup_round,
            "commandId": command_id,
            "provider": self._provider.provider_name,
            "providerReleaseId": self._provider.provider_release_id,
            "model": self._provider.model,
            "promptVersion": self._provider.prompt_version,
            "schemaVersion": self._provider.schema_version,
            "entitlementPolicyVersion": ENTITLEMENT_POLICY_VERSION,
        }
        if prior_evidence is not None:
            payload["priorEvidence"] = prior_evidence
        return payload

    def _prior_evidence(
        self,
        session: Session,
        run: RowMapping,
    ) -> dict[str, object]:
        if run["job_id"] is None:
            raise CauseAgentWorkflowError("cause_followup_prior_job_missing")
        from uuid import UUID

        job = self._jobs.by_id(session, job_id=UUID(str(run["job_id"])))
        evidence = job.result.get("evidence") if job and job.result else None
        if not isinstance(evidence, dict):
            raise CauseAgentWorkflowError("cause_followup_prior_evidence_missing")
        return evidence

    def _run_values(
        self,
        *,
        run_id: str,
        version_no: int,
        user_id: str,
        review_id: str,
        session_id: str,
        reflection_version_id: str,
        state: str,
        job_id: str | None,
        input_refs: list[str],
        input_fingerprint: str,
        followup_round: int,
        supersedes: str | None,
        created_at: datetime,
        candidate_version_id: str | None = None,
        progress_fingerprint: str | None = None,
        model_call_count: int = 0,
        tool_call_count: int = 0,
        consecutive_no_progress: int = 0,
        termination_reason: str | None = None,
    ) -> dict[str, object]:
        definition_payload = CAUSE_AGENT_DEFINITION.model_dump(
            by_alias=True,
            mode="json",
        )
        return {
            "run_version_id": str(uuid4()),
            "run_id": run_id,
            "version_no": version_no,
            "user_id": user_id,
            "review_id": review_id,
            "session_id": session_id,
            "reflection_version_id": reflection_version_id,
            "definition_release_id": CAUSE_AGENT_DEFINITION.definition_release_id,
            "state": state,
            "job_id": job_id,
            "input_version_refs": _json(input_refs),
            "input_fingerprint": input_fingerprint,
            "provider": self._provider.provider_name,
            "model": self._provider.model,
            "provider_release_id": self._provider.provider_release_id,
            "prompt_version": self._provider.prompt_version,
            "schema_version": self._provider.schema_version,
            "toolset_snapshot": _json(definition_payload["tools"]),
            "budget_snapshot": _json(definition_payload["budget"]),
            "progress_fingerprint": progress_fingerprint,
            "model_call_count": model_call_count,
            "tool_call_count": tool_call_count,
            "consecutive_no_progress": consecutive_no_progress,
            "followup_round": followup_round,
            "termination_reason": termination_reason,
            "candidate_version_id": candidate_version_id,
            "supersedes_run_version_id": supersedes,
            "created_at": created_at,
        }

    def _append_event(
        self,
        session: Session,
        *,
        run_id: str,
        user_id: str,
        review_id: str,
        command_id: str,
        event_type: str,
        revision: int,
        occurred_at: datetime,
        payload: dict[str, object],
    ) -> None:
        event_id = uuid4()
        self._outbox.append(
            session,
            event_id=event_id,
            deduplication_key=f"{event_type}:{run_id}:{revision}",
            aggregate_type="xingce_cause_agent",
            aggregate_id=run_id,
            event_type=event_type,
            payload={
                "eventId": str(event_id),
                "eventType": event_type,
                "aggregateId": run_id,
                "aggregateRevision": revision,
                "occurredAt": occurred_at.isoformat(),
                "producer": "xingce_learning",
                "trackId": "xingce",
                "correlationId": command_id,
                "causationId": command_id,
                "payload": {"reviewId": review_id, **payload},
                "schemaVersion": 1,
            },
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
