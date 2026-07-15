from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.planning.xingce_completion import complete_xingce_plan_task
from app.domains.planning.xingce_evidence import record_pending_xingce_evidence
from app.domains.planning.xingce_start import start_xingce_plan_task
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository
from app.domains.xingce_learning.real_attempt.schemas import (
    AICauseCandidateState,
    AnswerCheckpoint,
    AttemptReviewProjection,
    CauseDecisionCommand,
    CauseDecisionVersionProjection,
    ReflectionCommand,
    ReflectionVersionProjection,
    SafeAsset,
    SafeMaterial,
    SafeOption,
    SafeQuestion,
    SafeQuestionBundle,
    SaveCheckpointCommand,
    StartTaskSessionCommand,
    SubmitTaskSessionCommand,
    TaskSessionProjection,
    XingceAttemptResult,
    XingceCompletionReceipt,
    XingceResultItem,
)
from app.domains.task_runtime.xingce_attempt import XingceTaskRuntimeRepository
from app.platform.outbox import OutboxRepository


SCORING_POLICY_VERSION = "xingce-objective-exact-v1"
PUBLIC_QUESTION_ASSET_HOSTS = frozenset({"kg.xingguoxian.cn"})
PUBLIC_QUESTION_ASSET_PATH_PREFIX = "/assets/"
PUBLISHED_EXPLANATION_STATUSES = frozenset({"verified", "ai_rewrite_accepted"})
RICH_TEXT_TAGS = frozenset(
    {
        "b",
        "br",
        "em",
        "i",
        "li",
        "ol",
        "p",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)
RICH_TEXT_BLOCKED_TAGS = frozenset(
    {"iframe", "object", "script", "style", "svg", "template"}
)


class XingceAttemptError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class XingceAttemptService:
    def __init__(
        self,
        *,
        repository: XingceAttemptRepository | None = None,
        runtime_repository: XingceTaskRuntimeRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._repository = repository or XingceAttemptRepository()
        self._runtime_repository = runtime_repository or XingceTaskRuntimeRepository()
        self._outbox = outbox or OutboxRepository()

    def start_or_resume(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: StartTaskSessionCommand,
        now: datetime | None = None,
    ) -> TaskSessionProjection:
        created_at = _utc(now)
        fingerprint = _fingerprint("session_start", str(command.plan_task_id), command)
        self._runtime_repository.lock_user(session, user_id=user_id)
        replay = self._replay(session, user_id, command_id, fingerprint)
        if replay is not None:
            return replay
        task = self._runtime_repository.current_plan_task(
            session,
            user_id=user_id,
            plan_task_id=str(command.plan_task_id),
            now=created_at,
        )
        if task is None:
            raise XingceAttemptError(
                "xingce_plan_task_unavailable",
                "当前行测计划任务不存在、已过期或已经结算。",
                status_code=409,
            )
        existing = self._runtime_repository.session_by_work_item(
            session,
            user_id=user_id,
            work_item_key=str(task["work_item_key"]),
        )
        if existing is not None:
            projection = self._projection(
                session, user_id=user_id, task_session=existing
            )
            self._record_command(
                session,
                user_id=user_id,
                command_id=command_id,
                command_type="session_start",
                target_id=str(existing["session_id"]),
                fingerprint=fingerprint,
                projection=projection,
                created_at=created_at,
            )
            return projection

        mode, module_name = _task_scope(task)
        source_questions = self._repository.select_frozen_questions(
            session,
            module_name=module_name,
            limit=3,
        )
        if not source_questions:
            raise XingceAttemptError(
                "xingce_content_unavailable",
                "当前计划模块没有通过内容与答案键门禁的真实题目。",
                status_code=503,
            )
        safe_bundle, scoring_bundle = _freeze_bundle(source_questions)
        session_id = str(uuid4())
        row = self._runtime_repository.insert_task_session(
            session,
            values={
                "session_id": session_id,
                "user_id": user_id,
                "source_plan_task_id": str(task["task_id"]),
                "source_plan_version_id": str(task["plan_version_id"]),
                "local_date": task["local_date"],
                "work_item_key": str(task["work_item_key"]),
                "mode": mode,
                "bundle_version_id": safe_bundle.bundle_version_id,
                "safe_bundle": _json(
                    safe_bundle.model_dump(by_alias=True, mode="json")
                ),
                "scoring_bundle": _json(scoring_bundle),
                "current_question_id": safe_bundle.questions[0].question_id,
                "checkpoint": _json({"answers": []}),
                "created_at": created_at,
            },
        )
        start_xingce_plan_task(
            session,
            user_id=user_id,
            task=task,
            task_session=row,
            command_id=command_id,
            started_at=created_at,
        )
        _append_state_event(
            self._outbox,
            session,
            event_type="task-session-state-changed@v1",
            producer="task_runtime",
            aggregate_type="task_session",
            aggregate_id=session_id,
            aggregate_revision=1,
            user_id=user_id,
            command_id=command_id,
            occurred_at=created_at,
            from_state="",
            to_state="active",
            resource_version_refs=[
                f"task-session:{session_id}:1",
                f"plan-task:{task['task_id']}",
                f"bundle:{safe_bundle.bundle_version_id}",
            ],
            reason_code="xingce_session_started",
        )
        projection = self._projection(session, user_id=user_id, task_session=row)
        self._record_command(
            session,
            user_id=user_id,
            command_id=command_id,
            command_type="session_start",
            target_id=session_id,
            fingerprint=fingerprint,
            projection=projection,
            created_at=created_at,
        )
        return projection

    def get_session(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> TaskSessionProjection:
        row = self._runtime_repository.task_session(
            session,
            user_id=user_id,
            session_id=session_id,
        )
        if row is None:
            raise _not_found()
        return self._projection(session, user_id=user_id, task_session=row)

    def save_checkpoint(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
        command_id: str,
        command: SaveCheckpointCommand,
        now: datetime | None = None,
    ) -> TaskSessionProjection:
        saved_at = _utc(now)
        fingerprint = _fingerprint("checkpoint_save", session_id, command)
        self._runtime_repository.lock_user(session, user_id=user_id)
        replay = self._replay(session, user_id, command_id, fingerprint)
        if replay is not None:
            return replay
        row = self._required_session(session, user_id, session_id, for_update=True)
        _require_revision(row, command.expected_revision, state="active")
        if command.total_elapsed_seconds < int(row["total_elapsed_seconds"]):
            raise _conflict("total elapsed time cannot decrease")
        bundle = SafeQuestionBundle.model_validate(row["safe_bundle"])
        questions = {question.question_id: question for question in bundle.questions}
        if command.current_question_id not in questions:
            raise _conflict("current question is not part of the frozen bundle")
        merged = {
            str(item["questionId"]): dict(item)
            for item in dict(row["checkpoint"])["answers"]
        }
        for answer in command.answers:
            question = questions.get(answer.question_id)
            if question is None:
                raise _conflict("checkpoint question is not part of the frozen bundle")
            valid_options = {option.option_id for option in question.options}
            if not set(answer.selected_option_ids).issubset(valid_options) or not set(
                answer.eliminated_option_ids
            ).issubset(valid_options):
                raise _conflict(
                    "checkpoint contains an option outside the frozen question"
                )
            previous_payload = merged.get(answer.question_id)
            if previous_payload is not None and answer.elapsed_seconds < int(
                previous_payload["elapsedSeconds"]
            ):
                raise _conflict("question elapsed time cannot decrease")
            payload = answer.model_dump(by_alias=True, mode="json", exclude_none=True)
            merged[answer.question_id] = payload
            previous = self._runtime_repository.latest_answer_version(
                session,
                session_id=session_id,
                question_id=answer.question_id,
            )
            payload_hash = _hash(payload)
            if previous is not None and str(previous["payload_sha256"]) == payload_hash:
                continue
            self._runtime_repository.insert_answer_version(
                session,
                values={
                    "answer_version_id": str(uuid4()),
                    "command_id": command_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "question_id": answer.question_id,
                    "version_no": int(previous["version_no"]) + 1 if previous else 1,
                    "payload": _json(payload),
                    "payload_sha256": payload_hash,
                    "supersedes_answer_version_id": (
                        str(previous["answer_version_id"]) if previous else None
                    ),
                    "created_at": saved_at,
                },
            )
        if sum(int(item["elapsedSeconds"]) for item in merged.values()) > (
            command.total_elapsed_seconds
        ):
            raise _conflict("question elapsed time cannot exceed total elapsed time")
        checkpoint = {"answers": [merged[key] for key in sorted(merged)]}
        updated = self._runtime_repository.update_checkpoint(
            session,
            session_id=session_id,
            user_id=user_id,
            expected_revision=command.expected_revision,
            current_question_id=command.current_question_id,
            checkpoint=checkpoint,
            total_elapsed_seconds=command.total_elapsed_seconds,
            updated_at=saved_at,
        )
        if updated is None:
            raise _conflict("checkpoint revision changed")
        projection = self._projection(session, user_id=user_id, task_session=updated)
        self._record_command(
            session,
            user_id=user_id,
            command_id=command_id,
            command_type="checkpoint_save",
            target_id=session_id,
            fingerprint=fingerprint,
            projection=projection,
            created_at=saved_at,
        )
        return projection

    def submit(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
        command_id: str,
        command: SubmitTaskSessionCommand,
        now: datetime | None = None,
    ) -> TaskSessionProjection:
        submitted_at = _utc(now)
        fingerprint = _fingerprint("attempt_submit", session_id, command)
        self._runtime_repository.lock_user(session, user_id=user_id)
        replay = self._replay(session, user_id, command_id, fingerprint)
        if replay is not None:
            return replay
        row = self._required_session(session, user_id, session_id, for_update=True)
        _require_revision(row, command.expected_revision, state="active")
        if command.total_elapsed_seconds < int(row["total_elapsed_seconds"]):
            raise _conflict("total elapsed time cannot decrease")
        scoring = dict(row["scoring_bundle"])
        checkpoint = dict(row["checkpoint"])
        answers = {
            str(item["questionId"]): dict(item) for item in checkpoint["answers"]
        }
        question_ids = [str(item["questionId"]) for item in scoring["questions"]]
        if set(answers) != set(question_ids):
            raise _conflict(
                "every frozen question must be answered or explicitly skipped"
            )

        result_items: list[dict[str, Any]] = []
        assessment_ids: list[str] = []
        attempt_records: list[dict[str, Any]] = []
        attempt_group_id = str(uuid4())
        for frozen in scoring["questions"]:
            question_id = str(frozen["questionId"])
            answer = answers[question_id]
            answer_version = self._runtime_repository.latest_answer_version(
                session,
                session_id=session_id,
                question_id=question_id,
            )
            if answer_version is None:
                raise _conflict("submitted answer has no immutable answer revision")
            correct_options = [str(value) for value in frozen["correctOptionIds"]]
            selected = [str(value) for value in answer["selectedOptionIds"]]
            correct = not bool(answer["skipped"]) and sorted(selected) == sorted(
                correct_options
            )
            if bool(answer["skipped"]):
                result_code = "skipped"
            elif correct and answer.get("confidence") == "low":
                result_code = "guessed_correct"
            elif correct:
                result_code = "correct"
            else:
                result_code = "incorrect"
            result_item = {
                "questionId": question_id,
                "resultCode": result_code,
                "selectedOptionIds": selected,
                "correctOptionIds": correct_options,
                "explanation": frozen.get("explanation"),
                "explanationSource": frozen.get("explanationSource"),
            }
            answer_version_id = str(answer_version["answer_version_id"])
            timing_evidence = {
                "questionElapsedSeconds": int(answer["elapsedSeconds"]),
                "totalElapsedSeconds": command.total_elapsed_seconds,
                "submittedSessionRevision": command.expected_revision + 1,
                "recordedAt": submitted_at.isoformat(),
            }
            timing_evidence_ref = _hash(timing_evidence)
            source_lineage_refs = [
                f"plan-task:{row['source_plan_task_id']}",
                f"plan-version:{row['source_plan_version_id']}",
                f"bundle:{row['bundle_version_id']}",
                f"content:{frozen['contentVersionId']}",
            ]
            item_attempt_id = _hash(
                {
                    "sessionId": session_id,
                    "questionId": question_id,
                    "answerVersionId": answer_version_id,
                    "contentVersionId": frozen["contentVersionId"],
                }
            )
            submission_hash = _hash(
                {
                    "itemAttemptId": item_attempt_id,
                    "answerPayloadHash": str(answer_version["payload_sha256"]),
                    "contentVersionId": frozen["contentVersionId"],
                    "scoringPolicyVersion": SCORING_POLICY_VERSION,
                    "timingEvidenceRef": timing_evidence_ref,
                    "sourceLineageRefs": source_lineage_refs,
                }
            )
            attempt_record_id = str(uuid4())
            attempt_record = self._runtime_repository.insert_attempt_record(
                session,
                values={
                    "attempt_record_id": attempt_record_id,
                    "attempt_group_id": attempt_group_id,
                    "item_attempt_id": item_attempt_id,
                    "submit_command_id": command_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "session_revision": command.expected_revision + 1,
                    "answer_version_id": answer_version_id,
                    "content_version_id": str(frozen["contentVersionId"]),
                    "scoring_policy_version": SCORING_POLICY_VERSION,
                    "timing_evidence_ref": timing_evidence_ref,
                    "timing_evidence": _json(timing_evidence),
                    "submission_hash": submission_hash,
                    "source_lineage_refs": _json(source_lineage_refs),
                    "created_at": submitted_at,
                },
            )
            attempt_records.append(dict(attempt_record))
            assessment_key_hash = _assessment_key_hash(
                user_id=user_id,
                attempt_record_id=attempt_record_id,
                scoring_policy_version=SCORING_POLICY_VERSION,
            )
            assessment_result_code = (
                "unanswered" if result_code == "skipped" else result_code
            )
            assessment_id = str(uuid4())
            assessment_ids.append(assessment_id)
            result_items.append(result_item)
            self._repository.insert_assessment(
                session,
                values={
                    "assessment_id": assessment_id,
                    "attempt_record_id": attempt_record_id,
                    "submit_command_id": command_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "answer_version_id": answer_version_id,
                    "content_version_id": str(frozen["contentVersionId"]),
                    "scoring_policy_version": SCORING_POLICY_VERSION,
                    "assessment_key_hash": assessment_key_hash,
                    "result_code": assessment_result_code,
                    "correct": correct,
                    "result_snapshot": _json(result_item),
                    "created_at": submitted_at,
                },
            )
        submitted = self._runtime_repository.submit_session(
            session,
            session_id=session_id,
            user_id=user_id,
            expected_revision=command.expected_revision,
            attempt_group_id=attempt_group_id,
            total_elapsed_seconds=command.total_elapsed_seconds,
            submitted_at=submitted_at,
        )
        if submitted is None:
            raise _conflict("attempt revision changed")
        review_id = str(uuid4())
        requires_cause = True
        self._repository.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": command_id,
                "review_id": review_id,
                "user_id": user_id,
                "session_id": session_id,
                "revision": 1,
                "state": "reflection_due",
                "requires_cause_decision": requires_cause,
                "reflection_version_id": None,
                "cause_candidate_version_id": None,
                "cause_decision_version_id": None,
                "supersedes_review_version_id": None,
                "created_at": submitted_at,
            },
        )
        pending = record_pending_xingce_evidence(
            session,
            repository=self._repository,
            command_id=command_id,
            user_id=user_id,
            task_session=submitted,
            assessment_ids=assessment_ids,
            created_at=submitted_at,
        )
        for attempt_record, assessment_id, result_item in zip(
            attempt_records,
            assessment_ids,
            result_items,
            strict=True,
        ):
            _append_attempt_recorded_event(
                self._outbox,
                session,
                user_id=user_id,
                command_id=command_id,
                occurred_at=submitted_at,
                session_id=session_id,
                attempt_record=attempt_record,
            )
            _append_assessment_recorded_event(
                self._outbox,
                session,
                user_id=user_id,
                command_id=command_id,
                occurred_at=submitted_at,
                assessment_id=assessment_id,
                review_id=review_id,
                attempt_record=attempt_record,
                result_code=(
                    "unanswered"
                    if result_item["resultCode"] == "skipped"
                    else str(result_item["resultCode"])
                ),
            )
        _append_state_event(
            self._outbox,
            session,
            event_type="task-session-state-changed@v1",
            producer="task_runtime",
            aggregate_type="task_session",
            aggregate_id=session_id,
            aggregate_revision=int(submitted["revision"]),
            user_id=user_id,
            command_id=command_id,
            occurred_at=submitted_at,
            from_state="active",
            to_state="submitted",
            resource_version_refs=[
                f"task-session:{session_id}:{submitted['revision']}",
                *[
                    f"attempt-record:{record['attempt_record_id']}"
                    for record in attempt_records
                ],
                f"planning-evidence:{pending['evidence_version_id']}",
            ],
            reason_code="xingce_attempt_group_submitted",
        )
        projection = self._projection(session, user_id=user_id, task_session=submitted)
        self._record_command(
            session,
            user_id=user_id,
            command_id=command_id,
            command_type="attempt_submit",
            target_id=session_id,
            fingerprint=fingerprint,
            projection=projection,
            created_at=submitted_at,
        )
        return projection

    def submit_reflection(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
        command_id: str,
        command: ReflectionCommand,
        now: datetime | None = None,
    ) -> TaskSessionProjection:
        recorded_at = _utc(now)
        fingerprint = _fingerprint("reflection_submit", review_id, command)
        self._runtime_repository.lock_user(session, user_id=user_id)
        replay = self._replay(session, user_id, command_id, fingerprint)
        if replay is not None:
            return replay
        review = self._required_review(session, user_id, review_id)
        if (
            int(review["revision"]) != command.expected_revision
            or review["state"] != "reflection_due"
        ):
            raise _conflict("reflection review revision changed")
        task_session = self._required_session(
            session,
            user_id,
            str(review["session_id"]),
            for_update=True,
        )
        payload = command.model_dump(by_alias=True, mode="json", exclude_none=True)
        reflection_id = str(uuid4())
        self._repository.insert_reflection(
            session,
            values={
                "reflection_version_id": reflection_id,
                "command_id": command_id,
                "user_id": user_id,
                "review_id": review_id,
                "session_id": str(task_session["session_id"]),
                "version_no": 1,
                "payload": _json(payload),
                "payload_sha256": _hash(payload),
                "supersedes_reflection_version_id": None,
                "created_at": recorded_at,
            },
        )
        candidate_pending_review = self._repository.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": command_id,
                "review_id": review_id,
                "user_id": user_id,
                "session_id": str(task_session["session_id"]),
                "revision": int(review["revision"]) + 1,
                "state": "cause_candidate_pending",
                "requires_cause_decision": True,
                "reflection_version_id": reflection_id,
                "cause_candidate_version_id": None,
                "cause_decision_version_id": None,
                "supersedes_review_version_id": str(review["review_version_id"]),
                "created_at": recorded_at,
            },
        )
        candidate_id = str(uuid4())
        self._repository.insert_cause_candidate(
            session,
            values={
                "candidate_version_id": candidate_id,
                "command_id": command_id,
                "user_id": user_id,
                "review_id": review_id,
                "session_id": str(task_session["session_id"]),
                "version_no": 1,
                "input_version_refs": _json(
                    [
                        f"reflection-version:{reflection_id}",
                        f"attempt-group:{task_session['attempt_group_id']}",
                    ]
                ),
                "created_at": recorded_at,
            },
        )
        self._repository.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": command_id,
                "review_id": review_id,
                "user_id": user_id,
                "session_id": str(task_session["session_id"]),
                "revision": int(review["revision"]) + 2,
                "state": "cause_decision_due",
                "requires_cause_decision": True,
                "reflection_version_id": reflection_id,
                "cause_candidate_version_id": candidate_id,
                "cause_decision_version_id": None,
                "supersedes_review_version_id": str(
                    candidate_pending_review["review_version_id"]
                ),
                "created_at": recorded_at,
            },
        )
        if task_session["state"] != "submitted":
            raise _conflict("task session is not awaiting track settlement")
        _append_state_event(
            self._outbox,
            session,
            event_type="xingce-reflection-submitted@v1",
            producer="xingce_learning",
            aggregate_type="xingce_attempt_review",
            aggregate_id=review_id,
            aggregate_revision=int(review["revision"]) + 1,
            user_id=user_id,
            command_id=command_id,
            occurred_at=recorded_at,
            from_state="reflection_due",
            to_state="cause_candidate_pending",
            resource_version_refs=[
                f"reflection-version:{reflection_id}",
                f"review:{review_id}:{int(review['revision']) + 1}",
            ],
            reason_code="user_reflection_submitted",
        )
        projection = self._projection(
            session,
            user_id=user_id,
            task_session=task_session,
        )
        self._record_command(
            session,
            user_id=user_id,
            command_id=command_id,
            command_type="reflection_submit",
            target_id=review_id,
            fingerprint=fingerprint,
            projection=projection,
            created_at=recorded_at,
        )
        return projection

    def decide_cause(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
        command_id: str,
        command: CauseDecisionCommand,
        now: datetime | None = None,
    ) -> TaskSessionProjection:
        recorded_at = _utc(now)
        fingerprint = _fingerprint("cause_decision", review_id, command)
        self._runtime_repository.lock_user(session, user_id=user_id)
        replay = self._replay(session, user_id, command_id, fingerprint)
        if replay is not None:
            return replay
        review = self._required_review(session, user_id, review_id)
        if (
            int(review["revision"]) != command.expected_revision
            or review["state"] != "cause_decision_due"
            or review["reflection_version_id"] is None
            or review["cause_candidate_version_id"] is None
        ):
            raise _conflict("cause-decision review revision changed")
        task_session = self._required_session(
            session,
            user_id,
            str(review["session_id"]),
            for_update=True,
        )
        decision_id = str(uuid4())
        self._repository.insert_cause_decision(
            session,
            values={
                "decision_version_id": decision_id,
                "command_id": command_id,
                "user_id": user_id,
                "review_id": review_id,
                "session_id": str(task_session["session_id"]),
                "version_no": 1,
                "decision": command.decision,
                "replacement_cause": command.replacement_cause,
                "supersedes_decision_version_id": None,
                "created_at": recorded_at,
            },
        )
        self._repository.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": command_id,
                "review_id": review_id,
                "user_id": user_id,
                "session_id": str(task_session["session_id"]),
                "revision": int(review["revision"]) + 1,
                "state": "immediate_verification_due",
                "requires_cause_decision": True,
                "reflection_version_id": str(review["reflection_version_id"]),
                "cause_candidate_version_id": str(review["cause_candidate_version_id"]),
                "cause_decision_version_id": decision_id,
                "supersedes_review_version_id": str(review["review_version_id"]),
                "created_at": recorded_at,
            },
        )
        if task_session["state"] != "submitted":
            raise _conflict("task session is not awaiting track settlement")
        completion = complete_xingce_plan_task(
            session,
            repository=self._repository,
            user_id=user_id,
            task_session=task_session,
            command_id=command_id,
            completed_at=recorded_at,
        )
        transitioned = self._runtime_repository.settle_session(
            session,
            session_id=str(task_session["session_id"]),
            user_id=user_id,
            updated_at=recorded_at,
        )
        if transitioned is None:
            raise _conflict("task session settlement state changed")
        _append_state_event(
            self._outbox,
            session,
            event_type="xingce-cause-decision-recorded@v1",
            producer="xingce_learning",
            aggregate_type="xingce_attempt_review",
            aggregate_id=review_id,
            aggregate_revision=int(review["revision"]) + 1,
            user_id=user_id,
            command_id=command_id,
            occurred_at=recorded_at,
            from_state="cause_decision_due",
            to_state="immediate_verification_due",
            resource_version_refs=[
                f"cause-decision-version:{decision_id}",
                f"review:{review_id}:{int(review['revision']) + 1}",
            ],
            reason_code=f"user_cause_{command.decision}",
        )
        _append_state_event(
            self._outbox,
            session,
            event_type="task-session-state-changed@v1",
            producer="task_runtime",
            aggregate_type="task_session",
            aggregate_id=str(task_session["session_id"]),
            aggregate_revision=int(transitioned["revision"]),
            user_id=user_id,
            command_id=command_id,
            occurred_at=recorded_at,
            from_state="submitted",
            to_state="settled",
            resource_version_refs=[
                f"task-session:{task_session['session_id']}:{transitioned['revision']}",
                f"review:{review_id}:{int(review['revision']) + 1}",
            ],
            reason_code="xingce_track_writer_settled",
        )
        projection = self._projection(
            session,
            user_id=user_id,
            task_session=transitioned,
            completion=completion,
        )
        self._record_command(
            session,
            user_id=user_id,
            command_id=command_id,
            command_type="cause_decision",
            target_id=review_id,
            fingerprint=fingerprint,
            projection=projection,
            created_at=recorded_at,
        )
        return projection

    def _projection(
        self,
        session: Session,
        *,
        user_id: str,
        task_session: RowMapping,
        completion: XingceCompletionReceipt | None = None,
    ) -> TaskSessionProjection:
        session_id = str(task_session["session_id"])
        assessments = self._repository.assessments(
            session,
            user_id=user_id,
            session_id=session_id,
        )
        result: XingceAttemptResult | None = None
        if assessments:
            items = [
                XingceResultItem.model_validate(dict(item["result_snapshot"]))
                for item in assessments
            ]
            codes = [item.result_code for item in items]
            aggregate_code = codes[0] if len(set(codes)) == 1 else "mixed"
            result = XingceAttemptResult(
                attemptId=str(task_session["attempt_group_id"]),
                resultCode=aggregate_code,
                correctCount=sum(
                    code in {"correct", "guessed_correct"} for code in codes
                ),
                totalCount=len(items),
                items=items,
            )
        review_projection: AttemptReviewProjection | None = None
        review = _review_for_session(session, session_id=session_id, user_id=user_id)
        if review is not None:
            reflection = self._repository.reflection(
                session,
                reflection_version_id=(
                    str(review["reflection_version_id"])
                    if review["reflection_version_id"] is not None
                    else None
                ),
            )
            decision = self._repository.cause_decision(
                session,
                decision_version_id=(
                    str(review["cause_decision_version_id"])
                    if review["cause_decision_version_id"] is not None
                    else None
                ),
            )
            candidate = self._repository.cause_candidate(
                session,
                candidate_version_id=(
                    str(review["cause_candidate_version_id"])
                    if review["cause_candidate_version_id"] is not None
                    else None
                ),
            )
            if candidate is not None and (
                candidate["state"] != "unavailable"
                or candidate["reason_code"] != "scenario_not_activated"
                or bool(candidate["charged"])
            ):
                raise XingceAttemptError(
                    "xingce_candidate_contract_invalid",
                    "行测 AI 错因候选事实不符合不可用且不计费的发布边界。",
                    status_code=500,
                )
            review_projection = AttemptReviewProjection(
                reviewId=str(review["review_id"]),
                revision=int(review["revision"]),
                state=_api_review_state(str(review["state"])),
                aiCandidate=AICauseCandidateState(),
                reflection=(
                    ReflectionVersionProjection(
                        reflectionVersionId=str(reflection["reflection_version_id"]),
                        rawText=str(reflection["payload"]["rawText"]),
                        tags=list(reflection["payload"]["tags"]),
                        strategyNote=reflection["payload"].get("strategyNote"),
                        doubtNote=reflection["payload"].get("doubtNote"),
                        versionNo=int(reflection["version_no"]),
                    )
                    if reflection is not None
                    else None
                ),
                causeDecision=(
                    CauseDecisionVersionProjection(
                        decisionVersionId=str(decision["decision_version_id"]),
                        decision=str(decision["decision"]),
                        replacementCause=decision["replacement_cause"],
                        versionNo=int(decision["version_no"]),
                    )
                    if decision is not None
                    else None
                ),
            )
        if completion is None:
            completion_payload = self._runtime_repository.completion_payload(
                session,
                user_id=user_id,
                session_id=session_id,
            )
            completion = (
                XingceCompletionReceipt.model_validate_json(_json(completion_payload))
                if completion_payload
                else None
            )
        checkpoint = dict(task_session["checkpoint"])
        return TaskSessionProjection(
            sessionId=session_id,
            planTaskId=str(task_session["source_plan_task_id"]),
            revision=int(task_session["revision"]),
            state=_api_state(
                str(task_session["state"]),
                str(review["state"]) if review is not None else None,
            ),
            mode=str(task_session["mode"]),
            bundle=SafeQuestionBundle.model_validate(task_session["safe_bundle"]),
            currentQuestionId=str(task_session["current_question_id"]),
            answers=[
                AnswerCheckpoint.model_validate(item) for item in checkpoint["answers"]
            ],
            totalElapsedSeconds=int(task_session["total_elapsed_seconds"]),
            result=result,
            review=review_projection,
            completion=completion,
        )

    def _required_session(
        self,
        session: Session,
        user_id: str,
        session_id: str,
        *,
        for_update: bool,
    ) -> RowMapping:
        row = self._runtime_repository.task_session(
            session,
            user_id=user_id,
            session_id=session_id,
            for_update=for_update,
        )
        if row is None:
            raise _not_found()
        return row

    def _required_review(
        self,
        session: Session,
        user_id: str,
        review_id: str,
    ) -> RowMapping:
        row = self._repository.current_review(
            session,
            user_id=user_id,
            review_id=review_id,
            for_update=True,
        )
        if row is None:
            raise XingceAttemptError(
                "xingce_review_not_found",
                "行测复盘不存在。",
                status_code=404,
            )
        return row

    def _replay(
        self,
        session: Session,
        user_id: str,
        command_id: str,
        fingerprint: str,
    ) -> TaskSessionProjection | None:
        existing = self._runtime_repository.command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if existing is None:
            return None
        if str(existing["request_fingerprint"]) != fingerprint:
            raise XingceAttemptError(
                "idempotency_key_reused",
                "同一幂等键不能用于不同的行测命令。",
                status_code=409,
            )
        return TaskSessionProjection.model_validate_json(
            _json(existing["response_payload"])
        )

    def _record_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command_type: str,
        target_id: str,
        fingerprint: str,
        projection: TaskSessionProjection,
        created_at: datetime,
    ) -> None:
        self._runtime_repository.record_command(
            session,
            command_id=command_id,
            user_id=user_id,
            command_type=command_type,
            target_id=target_id,
            request_fingerprint=fingerprint,
            response_payload=projection.model_dump(by_alias=True, mode="json"),
            created_at=created_at,
        )


def _append_contract_event(
    outbox: OutboxRepository,
    session: Session,
    *,
    event_type: str,
    producer: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_revision: int,
    user_id: str,
    command_id: str,
    occurred_at: datetime,
    payload: dict[str, Any],
) -> None:
    event_id = uuid4()
    envelope = {
        "eventId": str(event_id),
        "eventType": event_type,
        "aggregateId": aggregate_id,
        "aggregateRevision": aggregate_revision,
        "occurredAt": occurred_at.isoformat(),
        "producer": producer,
        "subjectRef": _subject_ref(user_id),
        "trackId": "xingce",
        "correlationId": command_id,
        "causationId": command_id,
        "payload": payload,
        "schemaVersion": 1,
    }
    outbox.append(
        session,
        event_id=event_id,
        deduplication_key=f"{event_type}:{aggregate_id}:{aggregate_revision}",
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=envelope,
    )


def _append_state_event(
    outbox: OutboxRepository,
    session: Session,
    *,
    event_type: str,
    producer: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_revision: int,
    user_id: str,
    command_id: str,
    occurred_at: datetime,
    from_state: str,
    to_state: str,
    resource_version_refs: list[str],
    reason_code: str,
) -> None:
    _append_contract_event(
        outbox,
        session,
        event_type=event_type,
        producer=producer,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_revision=aggregate_revision,
        user_id=user_id,
        command_id=command_id,
        occurred_at=occurred_at,
        payload={
            "fromState": from_state,
            "toState": to_state,
            "resourceVersionRefs": resource_version_refs,
            "reasonCode": reason_code,
        },
    )


def _append_attempt_recorded_event(
    outbox: OutboxRepository,
    session: Session,
    *,
    user_id: str,
    command_id: str,
    occurred_at: datetime,
    session_id: str,
    attempt_record: dict[str, Any],
) -> None:
    attempt_record_id = str(attempt_record["attempt_record_id"])
    answer_version_id = str(attempt_record["answer_version_id"])
    _append_contract_event(
        outbox,
        session,
        event_type="attempt-recorded@v1",
        producer="task_runtime",
        aggregate_type="attempt_record",
        aggregate_id=attempt_record_id,
        aggregate_revision=1,
        user_id=user_id,
        command_id=command_id,
        occurred_at=occurred_at,
        payload={
            "taskSessionRef": (
                f"task-session:{session_id}:{attempt_record['session_revision']}"
            ),
            "targetTrack": "xingce",
            "taskItemRef": str(attempt_record["question_id"]),
            "itemAttemptId": str(attempt_record["item_attempt_id"]),
            "attemptRecordRef": f"attempt-record:{attempt_record_id}",
            "answerRevisionRef": f"answer-version:{answer_version_id}",
            "responseArtifactRef": f"answer-version:{answer_version_id}",
            "contentVersionRef": str(attempt_record["content_version_id"]),
            "materialPackageRef": "",
            "scoringPolicyVersionRef": str(attempt_record["scoring_policy_version"]),
            "timingEvidenceRef": str(attempt_record["timing_evidence_ref"]),
            "submissionHash": str(attempt_record["submission_hash"]),
            "sourceLineageRefs": list(attempt_record["source_lineage_refs"]),
        },
    )


def _append_assessment_recorded_event(
    outbox: OutboxRepository,
    session: Session,
    *,
    user_id: str,
    command_id: str,
    occurred_at: datetime,
    assessment_id: str,
    review_id: str,
    attempt_record: dict[str, Any],
    result_code: str,
) -> None:
    attempt_record_id = str(attempt_record["attempt_record_id"])
    answer_version_id = str(attempt_record["answer_version_id"])
    assessment_key_hash = _assessment_key_hash(
        user_id=user_id,
        attempt_record_id=attempt_record_id,
        scoring_policy_version=str(attempt_record["scoring_policy_version"]),
    )
    _append_contract_event(
        outbox,
        session,
        event_type="xingce-assessment-recorded@v1",
        producer="xingce_learning",
        aggregate_type="xingce_assessment",
        aggregate_id=assessment_id,
        aggregate_revision=1,
        user_id=user_id,
        command_id=command_id,
        occurred_at=occurred_at,
        payload={
            "writerRouting": "direct_attempt",
            "attemptRecordRef": f"attempt-record:{attempt_record_id}",
            "answerRevisionRef": f"answer-version:{answer_version_id}",
            "assessmentRef": f"xingce-assessment:{assessment_id}",
            "assessmentKeyHash": assessment_key_hash,
            "reviewRef": f"xingce-review:{review_id}",
            "contentVersionRef": str(attempt_record["content_version_id"]),
            "scoringPolicyVersionRef": str(attempt_record["scoring_policy_version"]),
            "resultCode": result_code,
            "acceptanceId": "",
        },
    )


def _subject_ref(user_id: str) -> str:
    return _hash({"scope": "event-subject", "subject": user_id})


def _assessment_key_hash(
    *,
    user_id: str,
    attempt_record_id: str,
    scoring_policy_version: str,
) -> str:
    value = "\n".join(
        (
            _subject_ref(user_id),
            f"attempt-record:{attempt_record_id}",
            scoring_policy_version,
        )
    )
    return sha256(value.encode("utf-8")).hexdigest()


def _freeze_bundle(
    source_questions: list[dict[str, Any]],
) -> tuple[SafeQuestionBundle, dict[str, Any]]:
    safe_questions: list[SafeQuestion] = []
    scoring_questions: list[dict[str, Any]] = []
    materials: dict[str, SafeMaterial] = {}
    for source in source_questions:
        content_source = {
            key: source.get(key)
            for key in (
                "question_id",
                "module_name",
                "question_type",
                "stem",
                "stem_html",
                "answer",
                "material_text",
                "material_html",
                "preferred_analysis",
                "analysis_source",
                "analysis_status",
                "prefer_ai_analysis",
                "analysis_needs_review",
                "options",
                "assets",
            )
        }
        content_version_id = _hash(content_source)
        material_id: str | None = None
        source_assets = [
            {**asset, "public_url": public_url}
            for asset in source["assets"]
            if (public_url := _public_question_asset_url(asset.get("image_src")))
            is not None
        ]
        if source.get("visual_required") and not source_assets:
            raise XingceAttemptError(
                "xingce_visual_asset_unavailable",
                "这道图形题没有已发布且可由小程序安全访问的题图。",
                status_code=503,
            )
        material_html = _safe_rich_html(source.get("material_html"))
        stem_html = _safe_rich_html(source.get("stem_html"))
        if source.get("material_text") or material_html or source_assets:
            material_identity = {
                "text": source.get("material_text"),
                "html": material_html,
                "assets": source_assets,
            }
            material_id = f"material-{_hash(material_identity)[:24]}"
            if material_id not in materials:
                materials[material_id] = SafeMaterial(
                    materialId=material_id,
                    title=None,
                    text=source.get("material_text"),
                    html=material_html,
                    assets=[
                        SafeAsset(
                            assetId=str(asset["asset_id"]),
                            url=str(asset["public_url"]),
                            altText=f"{source.get('module_name') or '行测'}题目配图",
                        )
                        for asset in source_assets
                    ],
                )
        options = [
            SafeOption(
                optionId=str(option["label"]).strip().upper(),
                label=str(option["label"]),
                text=str(option["option_text"]),
                html=_safe_rich_html(option.get("option_html")),
            )
            for option in source["options"]
        ]
        correct_options = _answer_options(str(source["answer"]))
        option_ids = {option.option_id for option in options}
        if not correct_options or not set(correct_options).issubset(option_ids):
            raise XingceAttemptError(
                "xingce_answer_key_invalid",
                "真实题目的答案键与冻结选项不一致。",
                status_code=503,
            )
        safe_questions.append(
            SafeQuestion(
                questionId=str(source["question_id"]),
                contentVersionId=content_version_id,
                materialId=material_id,
                moduleName=str(source.get("module_name") or "行测"),
                stem=str(source["stem"]),
                stemHtml=stem_html,
                options=options,
            )
        )
        explanation, explanation_source = _published_explanation(source)
        scoring_questions.append(
            {
                "questionId": str(source["question_id"]),
                "contentVersionId": content_version_id,
                "correctOptionIds": correct_options,
                "explanation": explanation,
                "explanationSource": explanation_source,
            }
        )
    frozen_payload = {
        "materials": [
            item.model_dump(by_alias=True, mode="json") for item in materials.values()
        ],
        "questions": [
            item.model_dump(by_alias=True, mode="json") for item in safe_questions
        ],
        "scoring": scoring_questions,
        "scoringPolicyVersion": SCORING_POLICY_VERSION,
    }
    bundle_version_id = _hash(frozen_payload)
    bundle = SafeQuestionBundle(
        bundleVersionId=bundle_version_id,
        bundleType="shared_material" if materials else "single",
        materials=list(materials.values()),
        questions=safe_questions,
    )
    return bundle, {
        "bundleVersionId": bundle_version_id,
        "scoringPolicyVersion": SCORING_POLICY_VERSION,
        "questions": scoring_questions,
    }


def _published_explanation(source: dict[str, Any]) -> tuple[str | None, str | None]:
    status = str(source.get("analysis_status") or "").strip()
    needs_review = bool(source.get("analysis_needs_review"))
    preferred = str(source.get("preferred_analysis") or "").strip()
    if status not in PUBLISHED_EXPLANATION_STATUSES or needs_review or not preferred:
        return None, None
    return preferred, str(source.get("analysis_source") or "published")


class _SafeRichHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.open_tags: list[str] = []
        self.blocked_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized = tag.casefold()
        if self.blocked_depth:
            if normalized in RICH_TEXT_BLOCKED_TAGS:
                self.blocked_depth += 1
            return
        if normalized in RICH_TEXT_BLOCKED_TAGS:
            self.blocked_depth = 1
            return
        if normalized not in RICH_TEXT_TAGS:
            return
        safe_attrs: list[str] = []
        if normalized in {"td", "th"}:
            for name, value in attrs:
                if name.casefold() not in {"colspan", "rowspan"} or value is None:
                    continue
                candidate = value.strip()
                if candidate.isdigit() and 1 <= int(candidate) <= 100:
                    safe_attrs.append(f' {name.casefold()}="{candidate}"')
        self.output.append(f"<{normalized}{''.join(safe_attrs)}>")
        if normalized != "br":
            self.open_tags.append(normalized)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        normalized = tag.casefold()
        if normalized in self.open_tags:
            self.handle_endtag(normalized)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if self.blocked_depth:
            if normalized in RICH_TEXT_BLOCKED_TAGS:
                self.blocked_depth -= 1
            return
        if normalized not in self.open_tags:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.output.append(f"</{current}>")
            if current == normalized:
                break

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth:
            self.output.append(escape(data, quote=False))

    def finish(self) -> str | None:
        while self.open_tags:
            self.output.append(f"</{self.open_tags.pop()}>")
        value = "".join(self.output).strip()
        return value or None


def _safe_rich_html(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parser = _SafeRichHTMLParser()
    parser.feed(value)
    parser.close()
    return parser.finish()


def _task_scope(task: RowMapping) -> tuple[str, str | None]:
    if task["stage"] == "objective_baseline":
        return "baseline", None
    candidate = str(task["candidate_key"])
    module_name = candidate.split(":", 1)[1].strip() if ":" in candidate else None
    if module_name == "国考高频模块":
        module_name = None
    return "targeted", module_name


def _review_for_session(
    session: Session,
    *,
    session_id: str,
    user_id: str,
) -> RowMapping | None:
    return (
        session.execute(
            text(
                """
            select review.*
            from learning.xingce_attempt_review_versions review
            where review.session_id = cast(:session_id as uuid)
              and review.user_id = cast(:user_id as uuid)
              and not exists (
                select 1 from learning.xingce_attempt_review_versions successor
                where successor.supersedes_review_version_id = review.review_version_id
              )
            """
            ),
            {"session_id": session_id, "user_id": user_id},
        )
        .mappings()
        .one_or_none()
    )


def _api_state(state: str, review_state: str | None) -> str:
    if state == "active" and review_state is None:
        return "answering"
    if state == "submitted" and review_state is None:
        return "submitted"
    if state == "submitted" and review_state == "reflection_due":
        return "reflection_required"
    if state == "submitted" and review_state == "cause_candidate_pending":
        return "submitted"
    if state == "submitted" and review_state == "cause_decision_due":
        return "cause_decision_required"
    if state == "settled" and review_state == "immediate_verification_due":
        return "completed"
    raise XingceAttemptError(
        "xingce_state_contract_invalid",
        "行测任务运行时与复盘状态不一致。",
        status_code=500,
    )


def _api_review_state(state: str) -> str:
    return {
        "reflection_due": "reflection_required",
        "cause_candidate_pending": "cause_candidate_pending",
        "cause_decision_due": "cause_decision_required",
        "immediate_verification_due": "immediate_verification_due",
    }[state]


def _answer_options(answer: str) -> list[str]:
    tokens = [token for token in re.split(r"[^A-Za-z0-9]+", answer.upper()) if token]
    if len(tokens) == 1 and len(tokens[0]) > 1 and tokens[0].isalpha():
        return list(tokens[0])
    return tokens


def _require_revision(row: RowMapping, expected: int, *, state: str) -> None:
    if int(row["revision"]) != expected or row["state"] != state:
        raise _conflict("task session revision changed")


def _not_found() -> XingceAttemptError:
    return XingceAttemptError(
        "xingce_session_not_found",
        "行测作答会话不存在。",
        status_code=404,
    )


def _conflict(message: str) -> XingceAttemptError:
    return XingceAttemptError(
        "xingce_revision_conflict",
        message,
        status_code=409,
    )


def _fingerprint(command_type: str, target: str, command: BaseModel) -> str:
    body = command.model_dump(by_alias=True, mode="json")
    return _hash({"commandType": command_type, "target": target, "body": body})


def _hash(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    return current.astimezone(UTC)


def _public_question_asset_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.hostname.casefold() not in PUBLIC_QUESTION_ASSET_HOSTS
        or parsed.port not in {None, 443}
        or not parsed.path.startswith(PUBLIC_QUESTION_ASSET_PATH_PREFIX)
    ):
        return None
    return candidate
