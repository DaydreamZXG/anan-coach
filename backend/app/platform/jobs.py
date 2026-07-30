from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import os
from threading import Event, Thread
from typing import Any, Final, Literal, Mapping
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.platform.job_consumer_principals import JOB_CONSUMER_PRINCIPALS


class JobContractError(RuntimeError):
    """Raised when a job key or state transition violates the durable contract."""


JOB_LEASE_CONTRACT_VERSION: Final = "JOB-LEASE@v1"
DEFAULT_JOB_LEASE: Final = timedelta(minutes=15)
JOB_CONSUMER_HEARTBEAT_INTERVAL: Final = timedelta(seconds=15)
JOB_CONSUMER_HEARTBEAT_TTL: Final = timedelta(seconds=60)
LOGGER = logging.getLogger("background_job_consumer_heartbeat")


@dataclass(frozen=True, slots=True)
class JobRegistration:
    job_type: str
    queue_name: str
    producer: str
    consumer_units: tuple[str, ...]
    consumer_entrypoint: str | None
    recovery_owner: str
    lease_duration: timedelta
    queue_readiness_age: timedelta
    running_readiness_age: timedelta
    enabled_setting: str | None = None
    deployment_state: Literal["deployed", "disabled"] = "deployed"
    disabled_reason: str | None = None

    @property
    def consumer_unit(self) -> str:
        if len(self.consumer_units) != 1:
            raise JobContractError(f"{self.job_type} does not have one deployed consumer")
        return self.consumer_units[0]


JOB_REGISTRY: Final = (
    JobRegistration(
        job_type="personal_data_export_v1",
        queue_name="platform.background_jobs",
        producer="app.domains.data_rights.repository.DataRightsRepository",
        consumer_units=("gongkao-dazi-data-rights-worker",),
        consumer_entrypoint="app.workers.data_rights",
        recovery_owner="app.domains.data_rights.worker.DataRightsWorker",
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
    ),
    JobRegistration(
        job_type="account_erasure_v1",
        queue_name="platform.background_jobs",
        producer="app.domains.data_rights.repository.DataRightsRepository",
        consumer_units=("gongkao-dazi-data-rights-worker",),
        consumer_entrypoint="app.workers.data_rights",
        recovery_owner="app.domains.data_rights.worker.DataRightsWorker",
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
    ),
    JobRegistration(
        job_type="xingce_cause_agent_v1",
        queue_name="platform.background_jobs",
        producer=("app.domains.xingce_learning.cause_agent.service.CauseAgentWorkflow"),
        consumer_units=("gongkao-dazi-cause-agent-worker",),
        consumer_entrypoint="app.workers.xingce_cause_agent",
        recovery_owner=("app.domains.xingce_learning.cause_agent.worker.CauseAgentWorker"),
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
    ),
    JobRegistration(
        job_type="learning_media_scan_v1",
        queue_name="platform.background_jobs",
        producer="app.domains.learning_media.service.LearningMediaService",
        consumer_units=("gongkao-dazi-external-learning-worker",),
        consumer_entrypoint="app.workers.external_learning",
        recovery_owner="app.domains.learning_media.worker.LearningMediaWorker",
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
    ),
    JobRegistration(
        job_type="learning_media_retention_cleanup_v1",
        queue_name="platform.background_jobs",
        producer="app.domains.learning_media.service.LearningMediaService",
        consumer_units=("gongkao-dazi-external-learning-worker",),
        consumer_entrypoint="app.workers.external_learning",
        recovery_owner="app.domains.learning_media.worker.LearningMediaWorker",
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
    ),
    JobRegistration(
        job_type="offline_packet_render_v1",
        queue_name="platform.background_jobs",
        producer="app.domains.learning_packages.service.LearningPackageService",
        consumer_units=("gongkao-dazi-external-learning-worker",),
        consumer_entrypoint="app.workers.external_learning",
        recovery_owner="app.domains.learning_packages.worker.PacketRenderWorker",
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
    ),
    JobRegistration(
        job_type="external_question_answer_v1",
        queue_name="platform.background_jobs",
        producer=("app.domains.external_ingestion.answering.service.ExternalAIAnswerService"),
        consumer_units=("gongkao-dazi-external-learning-worker",),
        consumer_entrypoint="app.workers.external_learning",
        recovery_owner=("app.domains.external_ingestion.answering.worker.ExternalAIAnswerWorker"),
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
    ),
    JobRegistration(
        job_type="private_knowledge_source_delete@v1",
        queue_name="platform.background_jobs",
        producer="app.domains.private_knowledge.repository.PrivateKnowledgeRepository",
        consumer_units=("gongkao-dazi-external-learning-worker",),
        consumer_entrypoint="app.workers.external_learning",
        recovery_owner=("app.domains.private_knowledge.worker.PrivateKnowledgeDeletionWorker"),
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
    ),
    JobRegistration(
        job_type="private_knowledge_weknora_index@v1",
        queue_name="platform.background_jobs",
        producer=("app.domains.private_knowledge.projection.PrivateKnowledgeProjectionGateway"),
        consumer_units=("gongkao-dazi-external-learning-worker",),
        consumer_entrypoint="app.workers.external_learning",
        recovery_owner=("app.domains.private_knowledge.worker.PrivateKnowledgeDeletionWorker"),
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
    ),
    JobRegistration(
        job_type="essay_grounded_review_v2",
        queue_name="platform.background_jobs",
        producer=("app.domains.essay_intelligence.workflow_service.EssayReviewWorkflowService"),
        consumer_units=("gongkao-dazi-essay-review-worker",),
        consumer_entrypoint="app.workers.essay_reviews",
        recovery_owner=("app.domains.essay_intelligence.review_worker.GroundedEssayReviewWorker"),
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
        enabled_setting="ESSAY_REVIEW_WORKER_ENABLED",
    ),
    JobRegistration(
        job_type="coach_notification_send_v1",
        queue_name="platform.background_jobs",
        producer=("app.domains.coach_notifications.evaluator.CoachNotificationEvaluator"),
        consumer_units=("gongkao-dazi-coach-notification-worker",),
        consumer_entrypoint="app.workers.coach_notifications",
        recovery_owner=("app.domains.coach_notifications.worker.CoachNotificationWorker"),
        lease_duration=timedelta(minutes=5),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=5),
        enabled_setting="COACH_NOTIFICATIONS_ENABLED",
    ),
    JobRegistration(
        job_type="community_method_card_candidate_v2",
        queue_name="platform.background_jobs",
        producer="app.workers.community_method_cards.CommunityMethodCardJobScheduler",
        consumer_units=(),
        consumer_entrypoint=None,
        recovery_owner="app.workers.community_method_cards.CommunityMethodCardWorker",
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
        deployment_state="disabled",
        disabled_reason=("No approved production scheduler or deployed consumer entrypoint"),
    ),
    JobRegistration(
        job_type="community_method_card_review_v2",
        queue_name="platform.background_jobs",
        producer="app.workers.community_method_cards.CommunityMethodCardJobScheduler",
        consumer_units=(),
        consumer_entrypoint=None,
        recovery_owner="app.workers.community_method_cards.CommunityMethodCardWorker",
        lease_duration=timedelta(minutes=15),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=15),
        deployment_state="disabled",
        disabled_reason=("No approved production scheduler or deployed consumer entrypoint"),
    ),
    JobRegistration(
        job_type="ilink_object_deletion_v1",
        queue_name="messaging.ilink_object_deletion_queue",
        producer="app.domains.ilink_channel.repository.ILinkRepository",
        consumer_units=("gongkao-dazi-ilink-worker",),
        consumer_entrypoint="app.workers.ilink_channel",
        recovery_owner="app.workers.ilink_channel.ILinkWorker",
        lease_duration=timedelta(minutes=5),
        queue_readiness_age=timedelta(minutes=5),
        running_readiness_age=timedelta(minutes=5),
        enabled_setting="ILINK_CHANNEL_ENABLED",
    ),
)


def validate_job_registry(
    registrations: tuple[JobRegistration, ...] = JOB_REGISTRY,
) -> None:
    seen: set[tuple[str, str]] = set()
    seen_job_types: set[str] = set()
    for registration in registrations:
        identity = (registration.queue_name, registration.job_type)
        if identity in seen:
            raise JobContractError(f"Duplicate job registration: {identity}")
        seen.add(identity)
        if registration.job_type in seen_job_types:
            raise JobContractError(
                f"Job type has multiple queue registrations: {registration.job_type}"
            )
        seen_job_types.add(registration.job_type)
        if registration.deployment_state == "deployed":
            if len(registration.consumer_units) != 1 or registration.consumer_entrypoint is None:
                raise JobContractError(
                    f"{registration.job_type} must have exactly one deployed consumer"
                )
            if registration.disabled_reason is not None:
                raise JobContractError(f"{registration.job_type} cannot be deployed and disabled")
        elif (
            registration.consumer_units
            or registration.consumer_entrypoint is not None
            or not registration.disabled_reason
            or registration.enabled_setting is not None
        ):
            raise JobContractError(
                f"{registration.job_type} has an invalid disabled deployment contract"
            )
        if registration.consumer_units and not registration.consumer_unit.startswith(
            "gongkao-dazi-"
        ):
            raise JobContractError(f"{registration.job_type} has an invalid deployed consumer")
        if not registration.producer or not registration.recovery_owner:
            raise JobContractError(f"{registration.job_type} must bind producer and recovery owner")
        if registration.lease_duration <= timedelta(0):
            raise JobContractError(f"{registration.job_type} must have a positive lease")
        if (
            registration.queue_readiness_age <= timedelta(0)
            or registration.running_readiness_age < registration.lease_duration
        ):
            raise JobContractError(f"{registration.job_type} has invalid readiness ages")
    deployed_identities = {
        (registration.consumer_unit, registration.consumer_entrypoint)
        for registration in registrations
        if registration.deployment_state == "deployed"
    }
    principal_identities = {
        (binding.consumer_unit, binding.consumer_entrypoint) for binding in JOB_CONSUMER_PRINCIPALS
    }
    if deployed_identities != principal_identities:
        raise JobContractError(
            "deployed consumer identities and heartbeat principals must be exact"
        )
    if len({binding.database_principal for binding in JOB_CONSUMER_PRINCIPALS}) != len(
        JOB_CONSUMER_PRINCIPALS
    ) or len({binding.group_role for binding in JOB_CONSUMER_PRINCIPALS}) != len(
        JOB_CONSUMER_PRINCIPALS
    ):
        raise JobContractError(
            "each deployed consumer must have a unique heartbeat login and group"
        )


validate_job_registry()
_REGISTRATION_BY_TYPE: Final = {
    registration.job_type: registration
    for registration in JOB_REGISTRY
    if registration.queue_name == "platform.background_jobs"
}


def job_registration(*, job_type: str, queue_name: str) -> JobRegistration:
    matches = tuple(
        registration
        for registration in JOB_REGISTRY
        if registration.job_type == job_type and registration.queue_name == queue_name
    )
    if len(matches) != 1:
        raise JobContractError(f"Expected exactly one registry binding for {queue_name}:{job_type}")
    return matches[0]


def job_registration_enabled(
    registration: JobRegistration,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    if registration.deployment_state == "disabled":
        return False
    if registration.enabled_setting is None:
        return True
    source = os.environ if environ is None else environ
    value = source.get(registration.enabled_setting, "false").strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise JobContractError(f"{registration.enabled_setting} must be an explicit boolean")


@dataclass(frozen=True)
class BackgroundJob:
    job_id: UUID
    job_key: str
    job_type: str
    input_version: str
    payload: dict[str, Any]
    status: str
    attempt_count: int
    max_attempts: int
    run_after: datetime
    claimed_at: datetime | None
    claim_token: UUID | None
    lease_expires_at: datetime | None
    result: dict[str, Any] | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class JobConsumerHeartbeat:
    consumer_unit: str
    consumer_entrypoint: str
    process_instance_id: UUID
    started_at: datetime
    last_heartbeat_at: datetime


class BackgroundJobRepository:
    def record_consumer_heartbeat(
        self,
        session: Session,
        *,
        consumer_unit: str,
        consumer_entrypoint: str,
        process_instance_id: UUID,
    ) -> JobConsumerHeartbeat:
        _validate_consumer_identity(
            consumer_unit=consumer_unit,
            consumer_entrypoint=consumer_entrypoint,
        )
        row = (
            session.execute(
                text(
                    """
                    select *
                    from platform.pulse_background_job_consumer_heartbeat(
                      :consumer_unit,
                      :consumer_entrypoint,
                      cast(:process_instance_id as uuid)
                    )
                    """
                ),
                {
                    "consumer_unit": consumer_unit,
                    "consumer_entrypoint": consumer_entrypoint,
                    "process_instance_id": str(process_instance_id),
                },
            )
            .mappings()
            .one()
        )
        return _consumer_heartbeat(row)

    def requeue_stale(
        self,
        session: Session,
        *,
        job_types: tuple[str, ...],
        stale_before: datetime,
        now: datetime | None = None,
    ) -> int:
        """Release crashed claims without consuming a retry attempt.

        Domain workers use stable provider idempotency keys and durable checkpoints,
        so reclaiming the same attempt cannot silently create a new logical call.
        """
        if not job_types:
            raise JobContractError("Stale recovery must be scoped to explicit job types")
        recovered_at = now or datetime.now(UTC)
        result = session.execute(
            text(
                """
                update platform.background_jobs
                   set status = 'queued',
                       attempt_count = greatest(attempt_count - 1, 0),
                       run_after = :now,
                       claimed_at = null,
                       claim_token = null,
                       lease_expires_at = null,
                       updated_at = :now,
                       last_error_code = 'stale_claim_recovered',
                       last_error_message = null
                 where status = 'running'
                   and job_type = any(cast(:job_types as text[]))
                   and (
                     lease_expires_at <= :now
                     or (
                       lease_expires_at is null
                       and claimed_at < :stale_before
                     )
                   )
                """
            ),
            {
                "now": recovered_at,
                "stale_before": stale_before,
                "job_types": list(job_types),
            },
        )
        return int(result.rowcount or 0)

    def checkpoint(
        self,
        session: Session,
        *,
        job_id: UUID,
        claim_token: UUID,
        result: dict[str, Any],
        updated_at: datetime | None = None,
    ) -> BackgroundJob:
        transition_at = updated_at or datetime.now(UTC)
        row = (
            session.execute(
                text(
                    """
                update platform.background_jobs
                   set result = cast(:result as jsonb), updated_at = :updated_at
                 where job_id = :job_id
                   and status = 'running'
                   and claim_token = :claim_token
                   and lease_expires_at > :updated_at
                 returning *
                """
                ),
                {
                    "job_id": str(job_id),
                    "claim_token": str(claim_token),
                    "result": json.dumps(result, ensure_ascii=False, sort_keys=True),
                    "updated_at": transition_at,
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            raise JobContractError("Only the active claim can checkpoint a running job")
        return _job(row)

    def by_key(self, session: Session, *, job_key: str) -> BackgroundJob | None:
        row = (
            session.execute(
                text("select * from platform.background_jobs where job_key = :job_key"),
                {"job_key": job_key},
            )
            .mappings()
            .one_or_none()
        )
        return _job(row) if row is not None else None

    def by_id(
        self,
        session: Session,
        *,
        job_id: UUID,
        for_update: bool = False,
    ) -> BackgroundJob | None:
        lock = " for update" if for_update else ""
        row = (
            session.execute(
                text(
                    "select * from platform.background_jobs "
                    "where job_id = cast(:job_id as uuid)" + lock
                ),
                {"job_id": str(job_id)},
            )
            .mappings()
            .one_or_none()
        )
        return _job(row) if row is not None else None

    def enqueue(
        self,
        session: Session,
        *,
        job_key: str,
        job_type: str,
        input_version: str,
        payload: dict[str, Any],
        max_attempts: int = 3,
        run_after: datetime | None = None,
    ) -> BackgroundJob:
        if not job_key or not job_type or not input_version:
            raise JobContractError("job_key, job_type, and input_version are required")
        if max_attempts <= 0:
            raise JobContractError("max_attempts must be positive")
        registration = _REGISTRATION_BY_TYPE.get(job_type)
        if registration is None:
            raise JobContractError(f"{job_type} is not registered")
        if registration.deployment_state == "disabled":
            raise JobContractError(f"{job_type} is disabled: {registration.disabled_reason}")

        row = (
            session.execute(
                text(
                    """
                insert into platform.background_jobs (
                  job_id,
                  job_key,
                  job_type,
                  input_version,
                  payload,
                  max_attempts,
                  run_after
                ) values (
                  :job_id,
                  :job_key,
                  :job_type,
                  :input_version,
                  cast(:payload as jsonb),
                  :max_attempts,
                  :run_after
                )
                on conflict (job_key) do nothing
                returning *
                """
                ),
                {
                    "job_id": str(uuid4()),
                    "job_key": job_key,
                    "job_type": job_type,
                    "input_version": input_version,
                    "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    "max_attempts": max_attempts,
                    "run_after": run_after or datetime.now(UTC),
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            row = (
                session.execute(
                    text("select * from platform.background_jobs where job_key = :job_key"),
                    {"job_key": job_key},
                )
                .mappings()
                .one()
            )
            if (
                row["job_type"] != job_type
                or row["input_version"] != input_version
                or row["payload"] != payload
            ):
                raise JobContractError("An existing job key refers to different immutable input")
        return _job(row)

    def claim_next(
        self,
        session: Session,
        *,
        job_types: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> BackgroundJob | None:
        claim_at = now or datetime.now(UTC)
        lease_duration = _lease_duration(job_types)
        self._recover_before_claim(
            session,
            job_types=job_types,
            now=claim_at,
            legacy_stale_before=claim_at - lease_duration,
        )
        claim_token = uuid4()
        row = (
            session.execute(
                text(
                    """
                with candidate as (
                  select job_id
                  from platform.background_jobs
                  where status = 'queued'
                    and run_after <= :now
                    and (
                      cardinality(cast(:job_types as text[])) = 0
                      or job_type = any(cast(:job_types as text[]))
                    )
                  order by run_after, created_at
                  for update skip locked
                  limit 1
                )
                update platform.background_jobs as job
                set status = 'running',
                    attempt_count = attempt_count + 1,
                    claimed_at = :now,
                    claim_token = :claim_token,
                    lease_expires_at = :lease_expires_at,
                    updated_at = :now
                from candidate
                where job.job_id = candidate.job_id
                returning job.*
                """
                ),
                {
                    "now": claim_at,
                    "lease_expires_at": claim_at + lease_duration,
                    "job_types": list(job_types),
                    "claim_token": str(claim_token),
                },
            )
            .mappings()
            .first()
        )
        return _job(row) if row else None

    def succeed(
        self,
        session: Session,
        *,
        job_id: UUID,
        claim_token: UUID,
        result: dict[str, Any],
        completed_at: datetime | None = None,
    ) -> BackgroundJob:
        transition_at = completed_at or datetime.now(UTC)
        row = (
            session.execute(
                text(
                    """
                update platform.background_jobs
                set status = 'succeeded',
                    result = cast(:result as jsonb),
                    claim_token = null,
                    claimed_at = null,
                    lease_expires_at = null,
                    completed_at = :completed_at,
                    updated_at = :completed_at,
                    last_error_code = null,
                    last_error_message = null
                where job_id = :job_id
                  and status = 'running'
                  and claim_token = :claim_token
                  and lease_expires_at > :completed_at
                returning *
                """
                ),
                {
                    "job_id": str(job_id),
                    "claim_token": str(claim_token),
                    "result": json.dumps(result, ensure_ascii=False, sort_keys=True),
                    "completed_at": transition_at,
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            raise JobContractError("Only the active claim can complete a running job")
        return _job(row)

    def fail(
        self,
        session: Session,
        *,
        job_id: UUID,
        claim_token: UUID,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_after: timedelta = timedelta(seconds=30),
        now: datetime | None = None,
        result: dict[str, Any] | None = None,
    ) -> BackgroundJob:
        failed_at = now or datetime.now(UTC)
        current = (
            session.execute(
                text(
                    """
                select *
                from platform.background_jobs
                where job_id = :job_id
                  and status = 'running'
                  and claim_token = :claim_token
                  and lease_expires_at > :failed_at
                for update
                """
                ),
                {
                    "job_id": str(job_id),
                    "claim_token": str(claim_token),
                    "failed_at": failed_at,
                },
            )
            .mappings()
            .first()
        )
        if current is None:
            raise JobContractError("Only the active claim can fail a running job")

        will_retry = retryable and current["attempt_count"] < current["max_attempts"]
        status_value = "queued" if will_retry else "failed"
        row = (
            session.execute(
                text(
                    """
                update platform.background_jobs
                set status = :status,
                    run_after = :run_after,
                    result = coalesce(cast(:result as jsonb), result),
                    claim_token = null,
                    claimed_at = null,
                    lease_expires_at = null,
                    last_error_code = :error_code,
                    last_error_message = :error_message,
                    completed_at = :completed_at,
                    updated_at = :now
                where job_id = :job_id
                  and status = 'running'
                  and claim_token = :claim_token
                  and lease_expires_at > :now
                returning *
                """
                ),
                {
                    "job_id": str(job_id),
                    "claim_token": str(claim_token),
                    "status": status_value,
                    "run_after": failed_at + retry_after if will_retry else current["run_after"],
                    "result": (
                        json.dumps(result, ensure_ascii=False, sort_keys=True)
                        if result is not None
                        else None
                    ),
                    "error_code": error_code,
                    "error_message": error_message,
                    "completed_at": None if will_retry else failed_at,
                    "now": failed_at,
                },
            )
            .mappings()
            .one()
        )
        return _job(row)

    def reconcile_business_failure(
        self,
        session: Session,
        *,
        job_id: UUID,
        error_code: str,
        error_message: str,
        result: dict[str, Any],
        completed_at: datetime | None = None,
    ) -> BackgroundJob:
        """Repair a persisted false-success after its business run is terminal.

        This is deliberately narrower than a general terminal rewrite: callers
        may only converge queued/running/succeeded rows to failed, and an
        existing failed fact is returned unchanged.
        """

        transition_at = completed_at or datetime.now(UTC)
        current = self.by_id(session, job_id=job_id, for_update=True)
        if current is None:
            raise JobContractError("Background job does not exist")
        if current.status == "failed":
            return current
        if current.status not in {"queued", "running", "succeeded"}:
            raise JobContractError("Only an incomplete or false-success job can be reconciled")
        row = (
            session.execute(
                text(
                    """
                update platform.background_jobs
                   set status = 'failed',
                       result = cast(:result as jsonb),
                       claim_token = null,
                       claimed_at = null,
                       lease_expires_at = null,
                       completed_at = coalesce(completed_at, :completed_at),
                       updated_at = :completed_at,
                       last_error_code = :error_code,
                       last_error_message = :error_message
                 where job_id = cast(:job_id as uuid)
                   and status in ('queued', 'running', 'succeeded')
                returning *
                """
                ),
                {
                    "job_id": str(job_id),
                    "result": json.dumps(result, ensure_ascii=False, sort_keys=True),
                    "completed_at": transition_at,
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise JobContractError("Background job changed during reconciliation")
        return _job(row)

    def _recover_before_claim(
        self,
        session: Session,
        *,
        job_types: tuple[str, ...],
        now: datetime,
        legacy_stale_before: datetime,
    ) -> int:
        result = session.execute(
            text(
                """
                update platform.background_jobs
                   set status = 'queued',
                       attempt_count = greatest(attempt_count - 1, 0),
                       run_after = :now,
                       claimed_at = null,
                       claim_token = null,
                       lease_expires_at = null,
                       updated_at = :now,
                       last_error_code = 'stale_claim_recovered',
                       last_error_message = null
                 where status = 'running'
                   and (
                     cardinality(cast(:job_types as text[])) = 0
                     or job_type = any(cast(:job_types as text[]))
                   )
                   and (
                     lease_expires_at <= :now
                     or (
                       lease_expires_at is null
                       and claimed_at < :legacy_stale_before
                     )
                   )
                """
            ),
            {
                "job_types": list(job_types),
                "now": now,
                "legacy_stale_before": legacy_stale_before,
            },
        )
        return int(result.rowcount or 0)


SessionFactory = Callable[[], AbstractContextManager[Session]]


class JobConsumerHeartbeatRuntime:
    """Continuously records one deployed consumer's liveness in PostgreSQL."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        consumer_unit: str,
        consumer_entrypoint: str,
        interval: timedelta = JOB_CONSUMER_HEARTBEAT_INTERVAL,
        jobs: BackgroundJobRepository | None = None,
    ) -> None:
        if interval <= timedelta(0) or interval >= JOB_CONSUMER_HEARTBEAT_TTL:
            raise JobContractError(
                "Consumer heartbeat interval must be positive and shorter than its TTL"
            )
        _validate_consumer_identity(
            consumer_unit=consumer_unit,
            consumer_entrypoint=consumer_entrypoint,
        )
        self._session_factory = session_factory
        self._consumer_unit = consumer_unit
        self._consumer_entrypoint = consumer_entrypoint
        self._interval_seconds = interval.total_seconds()
        self._jobs = jobs or BackgroundJobRepository()
        self._process_instance_id = uuid4()
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def process_instance_id(self) -> UUID:
        return self._process_instance_id

    def pulse(self) -> JobConsumerHeartbeat:
        with self._session_factory() as session:
            heartbeat = self._jobs.record_consumer_heartbeat(
                session,
                consumer_unit=self._consumer_unit,
                consumer_entrypoint=self._consumer_entrypoint,
                process_instance_id=self._process_instance_id,
            )
            session.commit()
        return heartbeat

    def __enter__(self) -> JobConsumerHeartbeatRuntime:
        if self._thread is not None:
            raise JobContractError("Consumer heartbeat runtime cannot be restarted")
        self.pulse()
        self._thread = Thread(
            target=self._run,
            name=f"heartbeat:{self._consumer_unit}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_seconds + 1)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self.pulse()
            except Exception:
                LOGGER.exception(
                    "background job consumer heartbeat failed",
                    extra={"consumer_unit": self._consumer_unit},
                )


def _validate_consumer_identity(
    *,
    consumer_unit: str,
    consumer_entrypoint: str,
) -> None:
    matches = tuple(
        registration
        for registration in JOB_REGISTRY
        if registration.deployment_state == "deployed"
        and consumer_unit in registration.consumer_units
        and registration.consumer_entrypoint == consumer_entrypoint
    )
    if not matches:
        raise JobContractError(
            "Consumer heartbeat identity is not bound to a deployed registration"
        )


def _lease_duration(job_types: tuple[str, ...]) -> timedelta:
    registered = {
        _REGISTRATION_BY_TYPE[job_type].lease_duration
        for job_type in job_types
        if job_type in _REGISTRATION_BY_TYPE
    }
    if len(registered) > 1:
        raise JobContractError("A single claim operation cannot mix registered lease durations")
    return next(iter(registered), DEFAULT_JOB_LEASE)


def _consumer_heartbeat(row: RowMapping) -> JobConsumerHeartbeat:
    return JobConsumerHeartbeat(
        consumer_unit=str(row["consumer_unit"]),
        consumer_entrypoint=str(row["consumer_entrypoint"]),
        process_instance_id=UUID(str(row["process_instance_id"])),
        started_at=row["started_at"],
        last_heartbeat_at=row["last_heartbeat_at"],
    )


def _job(row: RowMapping) -> BackgroundJob:
    return BackgroundJob(
        job_id=row["job_id"],
        job_key=row["job_key"],
        job_type=row["job_type"],
        input_version=row["input_version"],
        payload=dict(row["payload"]),
        status=row["status"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        run_after=row["run_after"],
        claimed_at=row["claimed_at"],
        claim_token=row["claim_token"],
        lease_expires_at=row["lease_expires_at"],
        result=dict(row["result"]) if row["result"] is not None else None,
        last_error_code=row["last_error_code"],
        last_error_message=row["last_error_message"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )
