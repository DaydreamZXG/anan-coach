from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.coach_notifications.repository import (
    CoachNotificationRepository,
    in_local_window,
)
from app.domains.coach_notifications.schemas import CoachCategory, EvaluationFact
from app.domains.coach_notifications.template import CoachTemplateRelease
from app.platform.jobs import BackgroundJobRepository


COACH_SEND_JOB_TYPE = "coach_notification_send_v1"
COACH_SEND_JOB_INPUT_VERSION = "coach-notification-send@1"
POLICY_VERSION = "coach-supervision-policy@1"


@dataclass(frozen=True)
class EvaluationResult:
    user_id: str
    outcome: str
    category: CoachCategory | None
    delivery_id: str | None = None


class CoachNotificationEvaluator:
    def __init__(
        self,
        *,
        template: CoachTemplateRelease | None,
        jobs: BackgroundJobRepository | None = None,
    ) -> None:
        self._template = template
        self._jobs = jobs or BackgroundJobRepository()

    def evaluate(
        self,
        session: Session,
        *,
        user_id: str,
        now: datetime,
    ) -> EvaluationResult:
        repository = CoachNotificationRepository(session)
        if not repository.account_active(user_id=user_id):
            return EvaluationResult(
                user_id=user_id, outcome="account_inactive", category=None
            )
        preference = repository.current_preference(user_id=user_id)
        if preference is None or not bool(preference["enabled"]):
            return EvaluationResult(user_id=user_id, outcome="disabled", category=None)
        local_now = now.astimezone(ZoneInfo(str(preference["timezone"])))
        fact = self._fact(
            repository=repository,
            user_id=user_id,
            local_now=local_now,
            preference=preference,
        )
        if fact is None:
            return EvaluationResult(user_id=user_id, outcome="no_due_work", category=None)
        if repository.settled_without_retry_for_fact(
            user_id=user_id,
            local_date=fact.local_date,
            category=fact.category,
            fact_fingerprint=fact.fact_fingerprint,
        ):
            return EvaluationResult(
                user_id=user_id, outcome="already_settled", category=fact.category
            )
        if repository.pending_for_fact(
            user_id=user_id,
            local_date=fact.local_date,
            category=fact.category,
            fact_fingerprint=fact.fact_fingerprint,
        ):
            return EvaluationResult(
                user_id=user_id, outcome="already_queued", category=fact.category
            )

        seed = {
            "userId": user_id,
            "localDate": fact.local_date.isoformat(),
            "category": fact.category,
            "factFingerprint": fact.fact_fingerprint,
            "preferenceVersionId": str(preference["preference_version_id"]),
            "policyVersion": POLICY_VERSION,
        }
        suppression = self._suppression(
            repository=repository,
            user_id=user_id,
            local_now=local_now,
            preference=preference,
            fact=fact,
        )
        if suppression is not None:
            repository.create_suppressed_decision(
                decision_key=_fingerprint({**seed, "outcome": suppression}),
                user_id=user_id,
                preference_version_id=str(preference["preference_version_id"]),
                local_date=fact.local_date,
                category=fact.category,
                outcome=suppression,
                fact_fingerprint=fact.fact_fingerprint,
                fact_refs=fact.fact_refs,
                task_count=fact.task_count,
                now=now,
            )
            return EvaluationResult(
                user_id=user_id, outcome=suppression, category=fact.category
            )

        if self._template is None:
            raise AssertionError("template suppression must be resolved before queuing")

        def page_path(delivery_id: str) -> str:
            return self._template.message(
                category=fact.category,
                delivery_id=delivery_id,
                local_now=local_now,
            )[0]

        def payload(delivery_id: str) -> dict[str, dict[str, str]]:
            return self._template.message(
                category=fact.category,
                delivery_id=delivery_id,
                local_now=local_now,
            )[1]

        delivery = repository.create_queued_delivery(
            decision_key_seed=seed,
            user_id=user_id,
            preference_version_id=str(preference["preference_version_id"]),
            local_date=fact.local_date,
            category=fact.category,
            fact_fingerprint=fact.fact_fingerprint,
            fact_refs=fact.fact_refs,
            task_count=fact.task_count,
            template_release_id=self._template.release_id,
            template_id=self._template.template_id,
            page_path_factory=page_path,
            payload_factory=payload,
            now=now,
        )
        if delivery is None:
            suppression = "suppressed_no_grant"
            repository.create_suppressed_decision(
                decision_key=_fingerprint({**seed, "outcome": suppression}),
                user_id=user_id,
                preference_version_id=str(preference["preference_version_id"]),
                local_date=fact.local_date,
                category=fact.category,
                outcome=suppression,
                fact_fingerprint=fact.fact_fingerprint,
                fact_refs=fact.fact_refs,
                task_count=fact.task_count,
                now=now,
            )
            return EvaluationResult(
                user_id=user_id, outcome=suppression, category=fact.category
            )
        delivery_id = str(delivery["delivery_id"])
        self._jobs.enqueue(
            session,
            job_key=f"coach-notification:send:{delivery_id}",
            job_type=COACH_SEND_JOB_TYPE,
            input_version=COACH_SEND_JOB_INPUT_VERSION,
            payload={"deliveryId": delivery_id},
            max_attempts=5,
            run_after=now,
        )
        return EvaluationResult(
            user_id=user_id,
            outcome="queued",
            category=fact.category,
            delivery_id=delivery_id,
        )

    def _suppression(
        self,
        *,
        repository: CoachNotificationRepository,
        user_id: str,
        local_now: datetime,
        preference: RowMapping,
        fact: EvaluationFact,
    ) -> str | None:
        if self._template is None:
            return "suppressed_template_unavailable"
        start = preference["receive_start"]
        end = preference["receive_end"]
        if start is None or end is None or not in_local_window(local_now.time(), start, end):
            return "suppressed_outside_window"
        if bool(preference["quiet_enabled"]) and in_local_window(
            local_now.time(), preference["quiet_start"], preference["quiet_end"]
        ):
            return "suppressed_quiet_hours"
        if repository.accepted_count(user_id=user_id, local_date=fact.local_date) >= int(
            preference["daily_limit"]
        ):
            return "suppressed_daily_limit"
        if self._template is None:
            raise AssertionError("template availability must be checked before grants")
        if repository.available_grants(
            user_id=user_id, template_id=self._template.template_id
        ) == 0:
            return "suppressed_no_grant"
        return None

    def _fact(
        self,
        *,
        repository: CoachNotificationRepository,
        user_id: str,
        local_now: datetime,
        preference: RowMapping,
    ) -> EvaluationFact | None:
        tasks = repository.current_incomplete_tasks(
            user_id=user_id, local_date=local_now.date()
        )
        categorized = [(task, _category(task)) for task in tasks]
        for category in ("mock", "reflection", "retest", "task"):
            if not _category_enabled(preference, category):
                continue
            selected = [task for task, current in categorized if current == category]
            if selected:
                refs = [_task_ref(task) for task in selected]
                return _evaluation_fact(
                    category=category,
                    local_date=local_now.date(),
                    refs=refs,
                    task_count=len(selected),
                )
        if local_now.weekday() == 6 and bool(preference["weekly_summary_enabled"]):
            week_start = local_now.date() - timedelta(days=6)
            rows = repository.weekly_plan_refs(
                user_id=user_id, week_start=week_start, week_end=local_now.date()
            )
            if rows:
                refs = [
                    {
                        "entityType": "daily_plan_version",
                        "entityId": str(row["plan_version_id"]),
                        "versionNo": int(row["version_no"]),
                    }
                    for row in rows
                ]
                return _evaluation_fact(
                    category="weekly_summary",
                    local_date=local_now.date(),
                    refs=refs,
                    task_count=0,
                )
        return None


def _category(task: RowMapping) -> CoachCategory:
    kind = str(task["kind"])
    stage = str(task["stage"])
    if kind == "mock_exam":
        return "mock"
    if kind == "reflection" or "review" in stage or stage == "essay_rewrite":
        return "reflection"
    if kind == "verification" or stage in {"objective_verification", "essay_transfer"}:
        return "retest"
    return "task"


def _category_enabled(preference: RowMapping, category: str) -> bool:
    column = {
        "task": "task_reminders_enabled",
        "reflection": "reflection_reminders_enabled",
        "retest": "retest_reminders_enabled",
        "mock": "mock_reminders_enabled",
    }[category]
    return bool(preference[column])


def _task_ref(task: RowMapping) -> dict[str, str | int]:
    ref: dict[str, str | int] = {
        "entityType": "daily_plan_task",
        "entityId": str(task["task_id"]),
        "planVersionId": str(task["plan_version_id"]),
    }
    if task["execution_version_id"] is not None:
        ref["executionVersionId"] = str(task["execution_version_id"])
    return ref


def _evaluation_fact(
    *,
    category: CoachCategory,
    local_date: Any,
    refs: list[dict[str, str | int]],
    task_count: int,
) -> EvaluationFact:
    fingerprint = _fingerprint(
        {"category": category, "localDate": local_date.isoformat(), "refs": refs}
    )
    return EvaluationFact(
        category=category,
        localDate=local_date,
        factFingerprint=fingerprint,
        factRefs=refs,
        taskCount=task_count,
    )


def _fingerprint(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
