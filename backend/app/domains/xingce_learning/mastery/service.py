from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.task_content.schemas import SafeQuestionBundle
from app.domains.xingce_learning.mastery.repository import (
    XingceMasteryRepository,
    json_value,
)
from app.domains.xingce_learning.mastery.scheduler import (
    FSRS_PARAMETERS,
    SCHEDULER_ALGORITHM_VERSION,
    SCHEDULER_POLICY_VERSION,
    VerificationEvidence,
    advance_schedule,
    initial_card,
)
from app.domains.xingce_learning.mastery.schemas import (
    MasteryQueueAction,
    MasteryQueueBucket,
    MasteryQueueItem,
    MasteryQueuePage,
    MasteryQueueSummary,
)
from app.platform.outbox import OutboxRepository


class XingceMasteryConflict(RuntimeError):
    pass


class XingceMasteryCursorError(ValueError):
    pass


class XingceMasteryService:
    def __init__(
        self,
        *,
        repository: XingceMasteryRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._repository = repository or XingceMasteryRepository()
        self._outbox = outbox or OutboxRepository()

    def queue(
        self,
        session: Session,
        *,
        user_id: str,
        bucket: MasteryQueueBucket,
        cursor: str | None,
        page_size: int,
        now: datetime,
    ) -> MasteryQueuePage:
        cursor_at, cursor_id = _decode_cursor(cursor, expected_bucket=bucket)
        counts = self._repository.queue_counts(session, user_id=user_id, now=now)
        rows = self._repository.queue_page(
            session,
            user_id=user_id,
            bucket=bucket,
            now=now,
            cursor_at=cursor_at,
            cursor_id=cursor_id,
            limit=page_size + 1,
        )
        has_more = len(rows) > page_size
        page_rows = rows[:page_size]
        items = [_queue_item(bucket=bucket, row=row) for row in page_rows]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            cursor_time = (
                last["due_at"] if bucket == "due_verification" else last["created_at"]
            )
            next_cursor = _encode_cursor(
                bucket=bucket,
                cursor_at=cursor_time,
                item_id=str(last["item_id"]),
            )
        return MasteryQueuePage(
            bucket=bucket,
            summary=MasteryQueueSummary(
                pendingReflectionCount=int(counts["pending_reflection_count"]),
                dueVerificationCount=int(counts["due_verification_count"]),
                stableMasteryCount=int(counts["stable_mastery_count"]),
                invalidatedCount=int(counts["invalidated_count"]),
            ),
            items=items,
            nextCursor=next_cursor,
            generatedAt=now,
        )

    def due_source(
        self,
        session: Session,
        *,
        user_id: str,
        mastery_version_id: str,
        now: datetime,
    ) -> RowMapping | None:
        return self._repository.current_due_version(
            session,
            user_id=user_id,
            mastery_version_id=mastery_version_id,
            now=now,
        )

    def record_cause_decision(
        self,
        session: Session,
        *,
        user_id: str,
        task_session: RowMapping,
        review: RowMapping,
        command_id: str,
        recorded_at: datetime,
    ) -> RowMapping:
        if not self._repository.session_content_is_eligible(
            session,
            user_id=user_id,
            session_id=str(task_session["session_id"]),
        ):
            raise XingceMasteryConflict(
                "mastery evidence contains withdrawn or invalidated content"
            )
        if str(task_session["mode"]) == "verification":
            return self._advance_verification(
                session,
                user_id=user_id,
                task_session=task_session,
                review=review,
                command_id=command_id,
                recorded_at=recorded_at,
            )
        return self._create_reflected(
            session,
            user_id=user_id,
            task_session=task_session,
            review=review,
            command_id=command_id,
            recorded_at=recorded_at,
        )

    def _create_reflected(
        self,
        session: Session,
        *,
        user_id: str,
        task_session: RowMapping,
        review: RowMapping,
        command_id: str,
        recorded_at: datetime,
    ) -> RowMapping:
        mastery_id = str(uuid4())
        mastery_version_id = str(uuid4())
        bundle = SafeQuestionBundle.model_validate(task_session["safe_bundle"])
        module_names = {question.module_name for question in bundle.questions}
        module_name = next(iter(module_names)) if len(module_names) == 1 else None
        question_ids = sorted(question.question_id for question in bundle.questions)
        evidence_refs = _review_evidence_refs(task_session, review)
        row = self._repository.insert_version(
            session,
            values={
                "mastery_version_id": mastery_version_id,
                "mastery_id": mastery_id,
                "command_id": command_id,
                "user_id": user_id,
                "version_no": 1,
                "state": "reflected",
                "verification_stage": "immediate",
                "due_at": recorded_at,
                "rating": None,
                "module_name": module_name,
                "scheduler_card": json_value(
                    initial_card(mastery_id=mastery_id, due_at=recorded_at)
                ),
                "scheduler_review_log": None,
                "scheduler_parameters": json_value(list(FSRS_PARAMETERS)),
                "scheduler_algorithm_version": SCHEDULER_ALGORITHM_VERSION,
                "scheduler_policy_version": SCHEDULER_POLICY_VERSION,
                "used_question_ids": json_value(question_ids),
                "source_review_id": str(review["review_id"]),
                "source_review_version_id": str(review["review_version_id"]),
                "source_session_id": str(task_session["session_id"]),
                "evidence_refs": json_value(evidence_refs),
                "supersedes_mastery_version_id": None,
                "created_at": recorded_at,
            },
        )
        self._append_event(
            session,
            user_id=user_id,
            command_id=command_id,
            row=row,
            from_state="",
            reason_code="confirmed_cause_requires_immediate_verification",
            occurred_at=recorded_at,
        )
        return row

    def _advance_verification(
        self,
        session: Session,
        *,
        user_id: str,
        task_session: RowMapping,
        review: RowMapping,
        command_id: str,
        recorded_at: datetime,
    ) -> RowMapping:
        source_version_id = task_session["source_mastery_version_id"]
        if source_version_id is None:
            raise XingceMasteryConflict("verification session has no frozen mastery source")
        current = self._repository.current_due_version(
            session,
            user_id=user_id,
            mastery_version_id=str(source_version_id),
            now=recorded_at,
            for_update=True,
        )
        if current is None or str(current["mastery_id"]) != str(
            task_session["source_mastery_id"]
        ):
            raise XingceMasteryConflict(
                "verification source is stale, not due, or owned by another learner"
            )
        evidence_rows = self._repository.verification_evidence(
            session,
            user_id=user_id,
            session_id=str(task_session["session_id"]),
            reflection_version_id=str(review["reflection_version_id"]),
        )
        evidence = [
            VerificationEvidence(
                result_code=str(item["result_code"]),
                confidence=(str(item["confidence"]) if item["confidence"] else None),
                disposition=str(item["disposition"]),  # type: ignore[arg-type]
                method_correct_calculation_error=bool(
                    item["method_correct_calculation_error"]
                ),
            )
            for item in evidence_rows
        ]
        advanced = advance_schedule(
            card_payload=dict(current["scheduler_card"]),
            current_stage=str(current["verification_stage"]),  # type: ignore[arg-type]
            evidence=evidence,
            reviewed_at=recorded_at,
            parameters=tuple(
                float(value) for value in current["scheduler_parameters"]
            ),
            policy_version=str(current["scheduler_policy_version"]),
        )
        bundle = SafeQuestionBundle.model_validate(task_session["safe_bundle"])
        used_question_ids = sorted(
            set(current["used_question_ids"])
            | {question.question_id for question in bundle.questions}
        )
        mastery_version_id = str(uuid4())
        row = self._repository.insert_version(
            session,
            values={
                "mastery_version_id": mastery_version_id,
                "mastery_id": str(current["mastery_id"]),
                "command_id": command_id,
                "user_id": user_id,
                "version_no": int(current["version_no"]) + 1,
                "state": advanced.state,
                "verification_stage": advanced.stage,
                "due_at": advanced.due_at,
                "rating": int(advanced.rating),
                "module_name": current["module_name"],
                "scheduler_card": json_value(advanced.card),
                "scheduler_review_log": json_value(advanced.review_log),
                "scheduler_parameters": json_value(
                    list(current["scheduler_parameters"])
                ),
                "scheduler_algorithm_version": str(
                    current["scheduler_algorithm_version"]
                ),
                "scheduler_policy_version": str(current["scheduler_policy_version"]),
                "used_question_ids": json_value(used_question_ids),
                "source_review_id": str(review["review_id"]),
                "source_review_version_id": str(review["review_version_id"]),
                "source_session_id": str(task_session["session_id"]),
                "evidence_refs": json_value(
                    [
                        f"mastery-version:{current['mastery_version_id']}",
                        *_review_evidence_refs(task_session, review),
                    ]
                ),
                "supersedes_mastery_version_id": str(current["mastery_version_id"]),
                "created_at": recorded_at,
            },
        )
        self._append_event(
            session,
            user_id=user_id,
            command_id=command_id,
            row=row,
            from_state=str(current["state"]),
            reason_code=advanced.reason_code,
            occurred_at=recorded_at,
        )
        return row

    def _append_event(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        row: RowMapping,
        from_state: str,
        reason_code: str,
        occurred_at: datetime,
    ) -> None:
        event_id = uuid4()
        version_id = str(row["mastery_version_id"])
        version_no = int(row["version_no"])
        self._outbox.append(
            session,
            event_id=event_id,
            deduplication_key=f"xingce-mastery:{version_id}:{version_no}",
            aggregate_type="xingce_mastery",
            aggregate_id=str(row["mastery_id"]),
            event_type="xingce-mastery-state-changed@v1",
            payload={
                "eventId": str(event_id),
                "eventType": "xingce-mastery-state-changed@v1",
                "aggregateId": str(row["mastery_id"]),
                "aggregateRevision": version_no,
                "occurredAt": occurred_at.isoformat(),
                "producer": "xingce_learning",
                "subjectRef": _subject_ref(user_id),
                "trackId": "xingce",
                "correlationId": command_id,
                "causationId": command_id,
                "payload": {
                    "fromState": from_state,
                    "toState": str(row["state"]),
                    "verificationStage": str(row["verification_stage"]),
                    "dueAt": row["due_at"].isoformat() if row["due_at"] else None,
                    "resourceVersionRefs": [f"mastery-version:{version_id}"],
                    "reasonCode": reason_code,
                },
                "schemaVersion": 1,
            },
        )


def _review_evidence_refs(
    task_session: RowMapping,
    review: RowMapping,
) -> list[str]:
    refs = [
        f"task-session:{task_session['session_id']}:{task_session['revision']}",
        f"attempt-group:{task_session['attempt_group_id']}",
        f"review-version:{review['review_version_id']}",
        f"reflection-version:{review['reflection_version_id']}",
        f"cause-candidate-version:{review['cause_candidate_version_id']}",
        f"cause-decision-version:{review['cause_decision_version_id']}",
    ]
    return refs


def _subject_ref(user_id: str) -> str:
    payload = json.dumps(
        {"scope": "event-subject", "subject": user_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _queue_item(*, bucket: MasteryQueueBucket, row: RowMapping) -> MasteryQueueItem:
    if bucket == "pending_reflection":
        session_id = str(row["session_id"])
        review_id = str(row["review_id"])
        return MasteryQueueItem(
            itemId=str(row["item_id"]),
            bucket=bucket,
            state=str(row["state"]),
            moduleName=str(row["module_name"]) if row["module_name"] else None,
            createdAt=row["created_at"],
            questionCount=int(row["question_count"]),
            evidenceRefs=[str(value) for value in row["evidence_refs"]],
            action=MasteryQueueAction(
                state="ready",
                startUrl=(
                    f"/pages/reflection/index?sessionId={session_id}&reviewId={review_id}"
                ),
            ),
            sessionId=session_id,
            reviewId=review_id,
        )

    plan_task_id = str(row["plan_task_id"]) if row["plan_task_id"] else None
    action = MasteryQueueAction(state="not_applicable")
    if bucket == "due_verification":
        action = (
            MasteryQueueAction(
                state="ready",
                planTaskId=plan_task_id,
                startUrl=(
                    "/pages/xingce-task-intro/index?mode=verification&planTaskId="
                    f"{plan_task_id}"
                ),
            )
            if plan_task_id
            else MasteryQueueAction(state="awaiting_plan")
        )
    return MasteryQueueItem(
        itemId=str(row["item_id"]),
        bucket=bucket,
        state=str(row["state"]),
        verificationStage=str(row["verification_stage"]),
        moduleName=str(row["module_name"]) if row["module_name"] else None,
        dueAt=row["due_at"],
        createdAt=row["created_at"],
        questionCount=int(row["question_count"]),
        evidenceRefs=[str(value) for value in row["evidence_refs"]],
        action=action,
        masteryId=str(row["mastery_id"]),
        masteryVersionId=str(row["mastery_version_id"]),
        invalidationReason="content_withdrawn" if bucket == "invalidated" else None,
    )


def _encode_cursor(
    *,
    bucket: MasteryQueueBucket,
    cursor_at: datetime,
    item_id: str,
) -> str:
    payload = json.dumps(
        {"bucket": bucket, "at": cursor_at.isoformat(), "id": item_id},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    value: str | None,
    *,
    expected_bucket: MasteryQueueBucket,
) -> tuple[datetime | None, str | None]:
    if value is None:
        return None, None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(urlsafe_b64decode(padded.encode("ascii")))
        if payload["bucket"] != expected_bucket:
            raise ValueError("cursor bucket changed")
        cursor_at = datetime.fromisoformat(str(payload["at"]))
        if cursor_at.tzinfo is None or cursor_at.utcoffset() is None:
            raise ValueError("cursor timestamp has no timezone")
        cursor_id = str(UUID(str(payload["id"])))
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise XingceMasteryCursorError("invalid mastery queue cursor") from exc
    return cursor_at, cursor_id
