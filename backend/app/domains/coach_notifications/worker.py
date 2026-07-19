from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import logging
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.coach_notifications.evaluator import (
    COACH_SEND_JOB_INPUT_VERSION,
    COACH_SEND_JOB_TYPE,
    CoachNotificationEvaluator,
)
from app.domains.coach_notifications.repository import CoachNotificationRepository
from app.domains.coach_notifications.template import CoachTemplateRelease
from app.domains.coach_notifications.wechat import (
    WechatSendReceipt,
    WechatSubscribeMessageClient,
    WechatSubscribeMessageError,
)
from app.platform.jobs import BackgroundJob, BackgroundJobRepository, JobContractError


LOGGER = logging.getLogger("coach_notification_worker")


class WechatSender(Protocol):
    def send(
        self,
        *,
        open_id: str,
        template_id: str,
        page: str,
        data: dict[str, dict[str, str]],
    ) -> WechatSendReceipt: ...


class CoachNotificationWorker:
    JOB_TYPES = (COACH_SEND_JOB_TYPE,)

    def __init__(
        self,
        *,
        session: Session,
        settings: Settings,
        sender: WechatSender | None = None,
        jobs: BackgroundJobRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        _validate_settings(settings)
        template = CoachTemplateRelease.from_json(
            settings.wechat_coach_template_release_json
        )
        if template is None:
            raise ValueError(
                "WECHAT_COACH_TEMPLATE_RELEASE_JSON is required for the coach worker"
            )
        self._session = session
        self._settings = settings
        self._template = template
        self._sender = sender or WechatSubscribeMessageClient(settings)
        self._jobs = jobs or BackgroundJobRepository()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._evaluator = CoachNotificationEvaluator(template=template, jobs=self._jobs)
        self._next_recovery_at = datetime.min.replace(tzinfo=UTC)

    def run_once(self) -> bool:
        now = self._clock()
        did_work = self._recover_stale(now=now)
        repository = CoachNotificationRepository(self._session)
        due_users = repository.claim_due_users(
            now=now,
            interval=timedelta(
                seconds=self._settings.coach_notification_evaluation_interval_seconds
            ),
            limit=self._settings.coach_notification_evaluation_batch_size,
        )
        self._session.commit()
        for user_id in due_users:
            try:
                self._evaluator.evaluate(self._session, user_id=user_id, now=now)
                self._session.commit()
                did_work = True
            except Exception:
                self._session.rollback()
                LOGGER.exception(
                    "coach notification evaluation failed",
                    extra={"user_id_fingerprint": _safe_user_ref(user_id)},
                )
        return self.process_next(now=now) is not None or did_work

    def process_next(self, *, now: datetime | None = None) -> BackgroundJob | None:
        current = now or self._clock()
        job = self._jobs.claim_next(
            self._session, job_types=self.JOB_TYPES, now=current
        )
        if job is None:
            self._session.rollback()
            return None
        self._session.commit()
        if job.claim_token is None:
            raise JobContractError("claimed coach notification job has no claim token")
        if job.input_version != COACH_SEND_JOB_INPUT_VERSION:
            return self._fail_contract(job=job, now=current)
        delivery_id = str(job.payload.get("deliveryId") or "")
        repository = CoachNotificationRepository(self._session)
        delivery = repository.delivery(delivery_id=delivery_id, for_update=True)
        if delivery is None:
            return self._fail_contract(job=job, now=current)
        if delivery["state"] not in {"queued", "sending"}:
            completed = self._jobs.succeed(
                self._session,
                job_id=job.job_id,
                claim_token=job.claim_token,
                result={"deliveryId": delivery_id, "state": str(delivery["state"])},
                completed_at=current,
            )
            self._session.commit()
            return completed
        if delivery["state"] == "sending":
            return self._settle_error(
                job=job,
                delivery_id=delivery_id,
                error=WechatSubscribeMessageError(
                    code=None,
                    transient=False,
                    dispatch_uncertain=True,
                ),
            )
        if (
            str(delivery["template_release_id"]) != self._template.release_id
            or str(delivery["template_id"]) != self._template.template_id
        ):
            repository.mark_terminal(
                delivery=delivery,
                state="canceled",
                event_type="canceled",
                attempt_no=job.attempt_count,
                error_code=None,
                reason="template_release_retired",
                now=current,
            )
            return self._succeed_terminal(
                job=job, delivery_id=delivery_id, state="canceled"
            )
        if not repository.current_enabled(user_id=str(delivery["user_id"])):
            repository.mark_terminal(
                delivery=delivery,
                state="canceled",
                event_type="canceled",
                attempt_no=job.attempt_count,
                error_code=None,
                reason="user_disabled",
                now=current,
            )
            return self._succeed_terminal(job=job, delivery_id=delivery_id, state="canceled")
        open_id = repository.wechat_open_id(
            user_id=str(delivery["user_id"]),
            app_id=self._settings.wechat_mini_app_id,
        )
        if open_id is None:
            repository.mark_terminal(
                delivery=delivery,
                state="canceled",
                event_type="canceled",
                attempt_no=job.attempt_count,
                error_code=None,
                reason="wechat_identity_missing",
                now=current,
            )
            return self._succeed_terminal(job=job, delivery_id=delivery_id, state="canceled")
        repository.mark_sending(
            delivery=delivery, attempt_no=job.attempt_count, now=current
        )
        self._session.commit()
        try:
            self._sender.send(
                open_id=open_id,
                template_id=str(delivery["template_id"]),
                page=str(delivery["page_path"]),
                data=dict(delivery["payload"]),
            )
        except WechatSubscribeMessageError as exc:
            return self._settle_error(job=job, delivery_id=delivery_id, error=exc)

        repository = CoachNotificationRepository(self._session)
        current_delivery = repository.delivery(delivery_id=delivery_id, for_update=True)
        if current_delivery is None:
            return self._fail_contract(job=job, now=self._clock())
        repository.mark_accepted(
            delivery=current_delivery,
            attempt_no=job.attempt_count,
            now=self._clock(),
        )
        completed = self._jobs.succeed(
            self._session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result={"deliveryId": delivery_id, "state": "platform_accepted"},
            completed_at=self._clock(),
        )
        self._session.commit()
        return completed

    def _settle_error(
        self,
        *,
        job: BackgroundJob,
        delivery_id: str,
        error: WechatSubscribeMessageError,
    ) -> BackgroundJob:
        now = self._clock()
        repository = CoachNotificationRepository(self._session)
        delivery = repository.delivery(delivery_id=delivery_id, for_update=True)
        if delivery is None or job.claim_token is None:
            return self._fail_contract(job=job, now=now)
        if error.dispatch_uncertain:
            repository.mark_terminal(
                delivery=delivery,
                state="outcome_unknown",
                event_type="outcome_unknown",
                attempt_no=job.attempt_count,
                error_code=error.code,
                reason="wechat_dispatch_outcome_unknown",
                now=now,
            )
            retryable = False
        elif error.transient and job.attempt_count < job.max_attempts:
            repository.mark_retry(
                delivery=delivery,
                attempt_no=job.attempt_count,
                error_code=error.code,
                now=now,
            )
            retryable = True
        else:
            state = "platform_rejected" if error.code is not None else "failed"
            event_type = state
            repository.mark_terminal(
                delivery=delivery,
                state=state,
                event_type=event_type,
                attempt_no=job.attempt_count,
                error_code=error.code,
                reason=(
                    "wechat_platform_rejected"
                    if error.code is not None
                    else "wechat_send_failed"
                ),
                now=now,
            )
            retryable = False
        failed = self._jobs.fail(
            self._session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_code=(
                "wechat_dispatch_outcome_unknown"
                if error.dispatch_uncertain
                else "wechat_subscribe_message_failed"
            ),
            error_message="WechatSubscribeMessageError",
            retryable=retryable,
            retry_after=timedelta(seconds=30),
            now=now,
        )
        self._session.commit()
        return failed

    def _succeed_terminal(
        self, *, job: BackgroundJob, delivery_id: str, state: str
    ) -> BackgroundJob:
        if job.claim_token is None:
            raise JobContractError("claimed coach notification job has no claim token")
        completed = self._jobs.succeed(
            self._session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            result={"deliveryId": delivery_id, "state": state},
            completed_at=self._clock(),
        )
        self._session.commit()
        return completed

    def _fail_contract(self, *, job: BackgroundJob, now: datetime) -> BackgroundJob:
        self._session.rollback()
        if job.claim_token is None:
            raise JobContractError("claimed coach notification job has no claim token")
        failed = self._jobs.fail(
            self._session,
            job_id=job.job_id,
            claim_token=job.claim_token,
            error_code="coach_notification_job_contract_invalid",
            error_message="JobContractError",
            retryable=False,
            now=now,
        )
        self._session.commit()
        return failed

    def _recover_stale(self, *, now: datetime) -> bool:
        if now < self._next_recovery_at:
            return False
        recovered = self._jobs.requeue_stale(
            self._session,
            job_types=self.JOB_TYPES,
            stale_before=now
            - timedelta(seconds=self._settings.coach_notification_stale_claim_seconds),
            now=now,
        )
        invalidated = CoachNotificationRepository(
            self._session
        ).invalidate_retired_grants(
            template_id=self._template.template_id,
            now=now,
        )
        self._session.commit()
        self._next_recovery_at = now + timedelta(seconds=30)
        return recovered + invalidated > 0


def _validate_settings(settings: Settings) -> None:
    if settings.coach_notification_worker_poll_seconds <= 0:
        raise ValueError("COACH_NOTIFICATION_WORKER_POLL_SECONDS must be positive")
    if not 300 <= settings.coach_notification_evaluation_interval_seconds <= 3600:
        raise ValueError(
            "COACH_NOTIFICATION_EVALUATION_INTERVAL_SECONDS must be between 300 and 3600"
        )
    if not 1 <= settings.coach_notification_evaluation_batch_size <= 1000:
        raise ValueError("COACH_NOTIFICATION_EVALUATION_BATCH_SIZE must be between 1 and 1000")
    if settings.coach_notification_stale_claim_seconds < 60:
        raise ValueError("COACH_NOTIFICATION_STALE_CLAIM_SECONDS must be at least 60")


def _safe_user_ref(user_id: str) -> str:
    return sha256(user_id.encode("utf-8")).hexdigest()[:12]
