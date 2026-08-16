from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.commerce.repository import CommerceQuotaExceededError
from app.domains.planning.xingce_completion import complete_xingce_plan_task
from app.domains.planning.xingce_start import start_xingce_plan_task
from app.domains.learning_media.repository import LearningMediaRepository
from app.domains.xingce_learning.cause_agent.repository import CauseAgentRepository
from app.domains.xingce_learning.cause_agent.service import (
    CauseAgentWorkflow,
    CauseAgentWorkflowError,
)
from app.domains.xingce_learning.explanation_policy import (
    published_explanation as _published_explanation,
    stored_explanation,
)
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository
from app.domains.xingce_learning.real_attempt.completion import (
    completion_receipt,
    settle_completion_after_cause,
)
from app.domains.xingce_learning.real_attempt.evidence import (
    record_pending_xingce_evidence,
    record_settled_xingce_evidence,
)
from app.domains.xingce_learning.content_version import question_content_version_id
from app.domains.xingce_learning.mastery.service import (
    XingceMasteryConflict,
    XingceMasteryService,
)
from app.domains.xingce_learning.real_attempt.schemas import (
    AICauseCandidateState,
    AnswerCheckpoint,
    AttemptReviewProjection,
    CauseDecisionCommand,
    CauseDecisionVersionProjection,
    CauseFollowupCommand,
    ItemInterpretation,
    ReflectionCommand,
    ReflectionVersionProjection,
    QuestionDisputeReceipt,
    ReportQuestionDisputeCommand,
    SafeQuestionBundle,
    SaveCheckpointCommand,
    StartTaskSessionCommand,
    SubmitTaskSessionCommand,
    TaskSessionProjection,
    XingceAttemptResult,
    XingceCompletionReceipt,
    XingceGroupSummary,
    XingceResultItem,
    VerificationOutcomeProjection,
)
from app.domains.xingce_learning.real_attempt.scratch_evidence import (
    build_scratch_evidence_projection,
)
from app.domains.task_runtime.xingce_attempt import XingceTaskRuntimeRepository
from app.domains.task_runtime.xingce_attempt.contracts import (
    XINGCE_SESSION_GENERATION_KEY,
    XINGCE_SESSION_GENERATION_V1,
    XingceSelectionIntentError,
    selection_intent_fingerprint,
    validate_selection_intent,
    xingce_question_count,
)
from app.domains.task_content.service import (
    TaskContentContractError,
    freeze_learner_bundle,
)
from app.platform.outbox import OutboxRepository


SCORING_POLICY_VERSION = "xingce-objective-factored-v2"
RESULT_FACT_CONTRACT_VERSION = "xingce-result-facts@v1"


class XingceAttemptError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ExplicitQuestionSelection:
    questions: tuple[dict[str, Any], ...]
    selection_context: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExplicitTaskSessionStart:
    session: TaskSessionProjection
    selection_context: dict[str, Any]


class XingceAttemptService:
    def __init__(
        self,
        *,
        repository: XingceAttemptRepository | None = None,
        runtime_repository: XingceTaskRuntimeRepository | None = None,
        mastery_service: XingceMasteryService | None = None,
        cause_agent_workflow: CauseAgentWorkflow | None = None,
        cause_agent_repository: CauseAgentRepository | None = None,
        media_repository: LearningMediaRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._repository = repository or XingceAttemptRepository()
        self._runtime_repository = runtime_repository or XingceTaskRuntimeRepository()
        self._mastery_service = mastery_service or XingceMasteryService()
        self._cause_agent_workflow = cause_agent_workflow or CauseAgentWorkflow(
            attempt_repository=self._repository
        )
        self._cause_agent_repository = cause_agent_repository or CauseAgentRepository()
        self._media_repository = media_repository or LearningMediaRepository()
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
        return self._start_or_resume(
            session,
            user_id=user_id,
            command_id=command_id,
            command=command,
            explicit_source_identity=None,
            explicit_selection_loader=None,
            now=now,
        )

    def start_or_resume_from_question_book(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        plan_task_id: UUID,
        book_id: UUID,
        selection_loader: Callable[[int], ExplicitQuestionSelection],
        now: datetime | None = None,
    ) -> ExplicitTaskSessionStart:
        source_identity = f"private-question-book:{book_id}"
        projection = self._start_or_resume(
            session,
            user_id=user_id,
            command_id=command_id,
            command=StartTaskSessionCommand(planTaskId=plan_task_id),
            explicit_source_identity=source_identity,
            explicit_selection_loader=selection_loader,
            now=now,
        )
        task_session = self._runtime_repository.task_session(
            session,
            user_id=user_id,
            session_id=str(projection.session_id),
        )
        if task_session is None:
            raise XingceAttemptError(
                "xingce_task_session_not_found",
                "题本练习会话不存在，请重新开始。",
                status_code=404,
            )
        scoring_bundle = dict(task_session["scoring_bundle"])
        context = scoring_bundle.get("selectionContext")
        if not _explicit_session_source_matches(
            task_session,
            expected_source_identity=source_identity,
        ) or not isinstance(context, dict):
            raise XingceAttemptError(
                "xingce_plan_task_source_conflict",
                "这个今日任务已经由其他题目来源开始，请继续原会话或选择另一个任务。",
                status_code=409,
            )
        _question_book_lineage_refs(context, scoring_bundle=scoring_bundle)
        return ExplicitTaskSessionStart(
            session=projection,
            selection_context=dict(context),
        )

    def _start_or_resume(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        command: StartTaskSessionCommand,
        explicit_source_identity: str | None,
        explicit_selection_loader: Callable[[int], ExplicitQuestionSelection]
        | None,
        now: datetime | None,
    ) -> TaskSessionProjection:
        created_at = _utc(now)
        fingerprint = _fingerprint(
            "session_start",
            str(command.plan_task_id),
            (
                {"sourceIdentity": explicit_source_identity}
                if explicit_source_identity is not None
                else command
            ),
        )
        if (explicit_source_identity is None) != (explicit_selection_loader is None):
            raise ValueError("explicit source identity and loader must be supplied together")
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
            if explicit_source_identity is not None and not _explicit_session_source_matches(
                existing,
                expected_source_identity=explicit_source_identity,
            ):
                raise XingceAttemptError(
                    "xingce_plan_task_source_conflict",
                    "这个今日任务已经由其他题目来源开始，请继续原会话或选择另一个任务。",
                    status_code=409,
                )
            projection = self._projection(session, user_id=user_id, task_session=existing)
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
        if explicit_source_identity is not None and mode != "targeted":
            raise XingceAttemptError(
                "xingce_plan_task_incompatible",
                "题本练习需要当前的行测专项任务；诊断和复测仍按原任务完成。",
                status_code=409,
            )
        source_mastery_id: str | None = None
        source_mastery_version_id: str | None = None
        verification_stage: str | None = None
        excluded_question_ids: list[str] = []
        if mode == "verification":
            mastery_ref = _verification_mastery_ref(task)
            if mastery_ref is None:
                raise XingceAttemptError(
                    "xingce_verification_source_missing",
                    "复测计划没有冻结具体掌握版本，不能启动无归属验证。",
                    status_code=409,
                )
            source_mastery_id, source_mastery_version_id = mastery_ref
            mastery = self._mastery_service.due_source(
                session,
                user_id=user_id,
                mastery_version_id=source_mastery_version_id,
                now=created_at,
            )
            if mastery is None or str(mastery["mastery_id"]) != source_mastery_id:
                raise XingceAttemptError(
                    "xingce_verification_source_stale",
                    "复测计划绑定的掌握版本已结算、尚未到期或不属于当前用户。",
                    status_code=409,
                )
            module_name = str(mastery["module_name"]) if mastery["module_name"] else None
            verification_stage = str(mastery["verification_stage"])
            excluded_question_ids = [
                str(question_id) for question_id in mastery["used_question_ids"]
            ]
        question_count = xingce_question_count(
            mode=mode,
            estimated_minutes=int(task["estimated_minutes"]),
            module_name=module_name,
        )
        selection_context: dict[str, Any] | None = None
        if explicit_selection_loader is not None:
            explicit = explicit_selection_loader(question_count)
            source_questions = list(explicit.questions)
            selection_context = dict(explicit.selection_context)
            if not source_questions:
                raise XingceAttemptError(
                    "xingce_explicit_content_unavailable",
                    "这本题本暂时没有可直接练的已确认题，请先处理一个歧义项或等待题库发布。",
                    status_code=409,
                )
            if len(source_questions) > question_count:
                raise XingceAttemptError(
                    "xingce_explicit_selection_invalid",
                    "题本选题超过当前任务容量，请重新开始。",
                    status_code=500,
                )
        elif mode == "targeted":
            persisted_intent = task["selection_intent"]
            try:
                targeted_selection = (
                    self._repository.select_frozen_questions_for_intent(
                        session,
                        intent=persisted_intent,
                        limit=question_count,
                        user_id=user_id,
                        now=created_at,
                    )
                    if persisted_intent is not None
                    else self._repository.select_targeted_frozen_questions(
                        session,
                        module_name=module_name,
                        limit=question_count,
                        user_id=user_id,
                        now=created_at,
                    )
                )
            except XingceSelectionIntentError as exc:
                raise XingceAttemptError(
                    "xingce_selection_intent_invalid",
                    "计划冻结的个性化选题意图不完整，不能启动本次训练。",
                    status_code=500,
                ) from exc
            source_questions = (
                list(targeted_selection.questions) if targeted_selection is not None else []
            )
            if (
                targeted_selection is not None
                and persisted_intent is not None
                and targeted_selection.selection_result is not None
            ):
                selection_context = {
                    "contractVersion": "xingce-selection-context@v2",
                    "intent": validate_selection_intent(persisted_intent),
                    "result": targeted_selection.selection_result,
                    "releaseId": targeted_selection.release_id,
                    "releaseVersion": targeted_selection.release_version,
                    "sourceDataVersion": targeted_selection.source_data_version,
                    "vocabVersion": targeted_selection.vocab_version,
                    "freshUntil": targeted_selection.fresh_until.isoformat(),
                    "nodeId": targeted_selection.node_id,
                    "nodeType": targeted_selection.node_type,
                    "moduleName": targeted_selection.module_name,
                }
            elif targeted_selection is not None:
                selection_context = {
                    "contractVersion": "xingce-targeted-selection@v1",
                    "releaseId": targeted_selection.release_id,
                    "releaseVersion": targeted_selection.release_version,
                    "sourceDataVersion": targeted_selection.source_data_version,
                    "vocabVersion": targeted_selection.vocab_version,
                    "freshUntil": targeted_selection.fresh_until.isoformat(),
                    "nodeId": targeted_selection.node_id,
                    "nodeType": targeted_selection.node_type,
                    "moduleName": targeted_selection.module_name,
                }
            elif module_name is not None and persisted_intent is None:
                source_questions = self._repository.select_frozen_questions(
                    session,
                    module_name=module_name,
                    limit=question_count,
                    user_id=user_id,
                )
                if len(source_questions) == question_count:
                    selection_context = {
                        "contractVersion": "xingce-module-selection@v1",
                        "moduleName": module_name,
                    }
        else:
            source_questions = self._repository.select_frozen_questions(
                session,
                module_name=module_name,
                limit=question_count,
                user_id=user_id,
                excluded_question_ids=excluded_question_ids,
            )
        if explicit_selection_loader is None and len(source_questions) < question_count:
            raise XingceAttemptError(
                "xingce_content_unavailable",
                f"当前计划模块只有 {len(source_questions)} 道可用题，"
                f"无法完成本次 {question_count} 题训练。",
                status_code=503,
            )
        safe_bundle, scoring_bundle = _freeze_bundle(
            source_questions,
            estimated_minutes=(
                min(
                    int(task["estimated_minutes"]),
                    max(2, len(source_questions) * 2),
                )
                if explicit_selection_loader is not None
                else int(task["estimated_minutes"])
            ),
            source_mastery_version_id=source_mastery_version_id,
            verification_stage=verification_stage,
            selection_context=selection_context,
        )
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
                "safe_bundle": _json(safe_bundle.model_dump(by_alias=True, mode="json")),
                "scoring_bundle": _json(scoring_bundle),
                "source_mastery_id": source_mastery_id,
                "source_mastery_version_id": source_mastery_version_id,
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
                *(
                    [f"mastery-version:{source_mastery_version_id}"]
                    if source_mastery_version_id is not None
                    else []
                ),
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

    def report_question_dispute(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
        command_id: str,
        command: ReportQuestionDisputeCommand,
        now: datetime | None = None,
    ) -> QuestionDisputeReceipt:
        created_at = _utc(now)
        self._runtime_repository.lock_user(session, user_id=user_id)
        existing = self._repository.question_dispute_by_idempotency_key(
            session,
            user_id=user_id,
            idempotency_key=command_id,
        )
        if existing is not None:
            expected = (
                session_id,
                command.question_id,
                command.content_version_id,
                command.category,
                command.description,
            )
            actual = (
                str(existing["session_id"]),
                str(existing["question_id"]),
                str(existing["content_version_id"]),
                str(existing["category"]),
                str(existing["description"]),
            )
            if actual != expected:
                raise XingceAttemptError(
                    "idempotency_key_reused",
                    "同一幂等键不能用于不同的题目争议上报。",
                    status_code=409,
                )
            return _dispute_receipt(existing)

        task_session = self._required_session(
            session,
            user_id,
            session_id,
            for_update=False,
        )
        bundle = SafeQuestionBundle.model_validate(task_session["safe_bundle"])
        question = next(
            (item for item in bundle.questions if item.question_id == command.question_id),
            None,
        )
        if question is None or question.content_version_id != command.content_version_id:
            raise XingceAttemptError(
                "xingce_dispute_content_mismatch",
                "争议上报必须绑定当前会话中冻结的题目与内容版本。",
                status_code=409,
            )
        dispute_id = str(uuid4())
        checkpoint = dict(task_session["checkpoint"])
        answer_state = next(
            (
                dict(item)
                for item in checkpoint.get("answers", [])
                if str(item.get("questionId")) == command.question_id
            ),
            None,
        )
        evidence_context = {
            "sessionRevision": int(task_session["revision"]),
            "bundleVersionId": bundle.bundle_version_id,
            "currentQuestionId": str(task_session["current_question_id"]),
            "questionStateRef": (
                f"sha256:{_hash(answer_state)}" if answer_state is not None else None
            ),
            "materialPosition": (
                answer_state.get("materialPosition") if answer_state is not None else None
            ),
        }
        row = self._repository.insert_question_dispute(
            session,
            values={
                "dispute_id": dispute_id,
                "user_id": user_id,
                "session_id": session_id,
                "question_id": command.question_id,
                "content_version_id": command.content_version_id,
                "category": command.category,
                "description": command.description,
                "evidence_context": _json(evidence_context),
                "idempotency_key": command_id,
                "created_at": created_at,
            },
        )
        _append_contract_event(
            self._outbox,
            session,
            event_type="xingce-question-dispute-filed@v1",
            producer="xingce_learning",
            aggregate_type="xingce_question_dispute",
            aggregate_id=dispute_id,
            aggregate_revision=1,
            user_id=user_id,
            command_id=command_id,
            occurred_at=created_at,
            payload={
                "sessionId": session_id,
                "questionId": command.question_id,
                "contentVersionId": command.content_version_id,
                "category": command.category,
            },
        )
        return _dispute_receipt(row)

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
        row = self._required_session(session, user_id, session_id, for_update=True)
        replay = self._replay(session, user_id, command_id, fingerprint)
        if replay is not None:
            return replay
        _require_revision(row, command.expected_revision, state="active")
        if command.total_elapsed_seconds < int(row["total_elapsed_seconds"]):
            raise _conflict("total elapsed time cannot decrease")
        bundle = SafeQuestionBundle.model_validate(row["safe_bundle"])
        questions = {question.question_id: question for question in bundle.questions}
        if command.current_question_id not in questions:
            raise _conflict("current question is not part of the frozen bundle")
        merged = {
            str(item["questionId"]): dict(item) for item in dict(row["checkpoint"])["answers"]
        }
        for answer in command.answers:
            question = questions.get(answer.question_id)
            if question is None:
                raise _conflict("checkpoint question is not part of the frozen bundle")
            valid_options = {option.option_id for option in question.options}
            if not set(answer.selected_option_ids).issubset(valid_options) or not set(
                answer.eliminated_option_ids
            ).issubset(valid_options):
                raise _conflict("checkpoint contains an option outside the frozen question")
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
        selection_lineage_refs = _selection_lineage_refs(
            scoring,
            mode=str(row["mode"]),
        )
        factored_result_contract = scoring.get("scoringPolicyVersion") == (SCORING_POLICY_VERSION)
        if factored_result_contract:
            frozen_version = scoring.get("scoringBundleVersion")
            versioned_payload = {
                key: value for key, value in scoring.items() if key != "scoringBundleVersion"
            }
            if (
                not isinstance(scoring.get("timingPolicy"), dict)
                or not isinstance(frozen_version, str)
                or frozen_version != _hash(versioned_payload)
            ):
                raise XingceAttemptError(
                    "xingce_scoring_bundle_integrity_invalid",
                    "冻结计分包的结果事实或建议时长版本不完整。",
                    status_code=500,
                )
        checkpoint = dict(row["checkpoint"])
        answers = {str(item["questionId"]): dict(item) for item in checkpoint["answers"]}
        question_ids = [str(item["questionId"]) for item in scoring["questions"]]
        if set(answers) != set(question_ids):
            raise _conflict("every frozen question must be answered or explicitly skipped")

        safe_bundle = dict(row["safe_bundle"])
        safe_questions = {
            str(item["questionId"]): dict(item)
            for item in safe_bundle.get("questions", [])
            if isinstance(item, dict) and item.get("questionId")
        }
        safe_materials = {
            str(item["materialId"]): dict(item)
            for item in safe_bundle.get("materials", [])
            if isinstance(item, dict) and item.get("materialId")
        }

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
            answer_change_count = _selection_change_count(
                [
                    dict(version["payload"])
                    for version in self._runtime_repository.answer_versions(
                        session,
                        session_id=session_id,
                        question_id=question_id,
                    )
                ]
            )
            correct_options = [str(value) for value in frozen["correctOptionIds"]]
            selected = [str(value) for value in answer["selectedOptionIds"]]
            correct = not bool(answer["skipped"]) and sorted(selected) == sorted(correct_options)
            active_abandon = bool(answer["skipped"])
            if active_abandon:
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
                # Figma 14:2「有改答记录」：来自不可变答案版本历史的真实计数。
                "answerChangeCount": answer_change_count,
                "explanation": frozen.get("explanation"),
                "explanationSource": frozen.get("explanationSource"),
            }
            advisory_seconds = frozen.get("advisorySeconds")
            if factored_result_contract:
                if not isinstance(advisory_seconds, int) or advisory_seconds < 1:
                    raise XingceAttemptError(
                        "xingce_timing_policy_invalid",
                        "冻结计分包缺少有效的单题建议时长。",
                        status_code=500,
                    )
                elapsed_seconds = int(answer["elapsedSeconds"])
                result_item.update(
                    {
                        "factContractVersion": RESULT_FACT_CONTRACT_VERSION,
                        "objectiveVerdict": (
                            "unanswered"
                            if active_abandon
                            else ("correct" if correct else "incorrect")
                        ),
                        "disposition": ("active_abandon" if active_abandon else "answered"),
                        "confidenceEvidence": (
                            "unreported"
                            if active_abandon or answer.get("confidence") is None
                            else str(answer["confidence"])
                        ),
                        "timingEvidence": {
                            "status": (
                                "advisory_exceeded"
                                if elapsed_seconds > advisory_seconds
                                else "within_advisory"
                            ),
                            "elapsedSeconds": elapsed_seconds,
                            "advisorySeconds": advisory_seconds,
                        },
                    }
                )
            answer_version_id = str(answer_version["answer_version_id"])
            timing_evidence = {
                "questionElapsedSeconds": int(answer["elapsedSeconds"]),
                "totalElapsedSeconds": command.total_elapsed_seconds,
                "submittedSessionRevision": command.expected_revision + 1,
                "recordedAt": submitted_at.isoformat(),
            }
            if factored_result_contract:
                timing_evidence.update(
                    {
                        "advisorySeconds": advisory_seconds,
                        "timingStatus": result_item["timingEvidence"]["status"],
                    }
                )
            timing_evidence_ref = _hash(timing_evidence)
            source_lineage_refs = [
                f"plan-task:{row['source_plan_task_id']}",
                f"plan-version:{row['source_plan_version_id']}",
                f"bundle:{row['bundle_version_id']}",
                f"content:{frozen['contentVersionId']}",
                *selection_lineage_refs,
            ]
            item_attempt_id = _hash(
                {
                    "sessionId": session_id,
                    "questionId": question_id,
                    "answerVersionId": answer_version_id,
                    "contentVersionId": frozen["contentVersionId"],
                }
            )
            attempt_record_id = str(uuid4())
            safe_question = safe_questions.get(question_id)
            if safe_question is None:
                raise XingceAttemptError(
                    "xingce_safe_bundle_integrity_invalid",
                    "冻结安全题包缺少提交题目。",
                    status_code=500,
                )
            material_id = safe_question.get("materialId")
            scratch_projection, scratch_fingerprint = (
                build_scratch_evidence_projection(
                    projection_id=attempt_record_id,
                    session_id=session_id,
                    question_id=question_id,
                    content_version_id=str(frozen["contentVersionId"]),
                    answer_version_id=answer_version_id,
                    answer_payload_sha256=str(answer_version["payload_sha256"]),
                    answer=answer,
                    question=safe_question,
                    material=(
                        safe_materials.get(str(material_id))
                        if material_id is not None
                        else None
                    ),
                    result=result_item,
                    formula_versions=self._runtime_repository.formula_versions_for_question(
                        session,
                        user_id=user_id,
                        session_id=session_id,
                        question_id=question_id,
                    ),
                    created_at=submitted_at,
                )
            )
            scratch_ref = (
                f"scratch-evidence-projection:{attempt_record_id}:"
                f"{scratch_fingerprint}"
            )
            source_lineage_refs.append(scratch_ref)
            submission_hash = _hash(
                {
                    "itemAttemptId": item_attempt_id,
                    "answerPayloadHash": str(answer_version["payload_sha256"]),
                    "contentVersionId": frozen["contentVersionId"],
                    "scoringPolicyVersion": SCORING_POLICY_VERSION,
                    "timingEvidenceRef": timing_evidence_ref,
                    "sourceLineageRefs": source_lineage_refs,
                    "scratchEvidenceFingerprint": scratch_fingerprint,
                }
            )
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
                    "scratch_evidence_projection": _json(scratch_projection),
                    "scratch_evidence_fingerprint": scratch_fingerprint,
                    "created_at": submitted_at,
                },
            )
            attempt_records.append(dict(attempt_record))
            assessment_key_hash = _assessment_key_hash(
                user_id=user_id,
                attempt_record_id=attempt_record_id,
                scoring_policy_version=SCORING_POLICY_VERSION,
            )
            assessment_result_code = "unanswered" if result_code == "skipped" else result_code
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
        # 只有“复测 + 每题客观答对”可以直接结算。猜对仍然需要用户
        # 补充过程证据；普通训练也保留原话复盘，不把这条快路扩大成假精准。
        all_correct = bool(result_items) and all(
            item["resultCode"] in {"correct", "guessed_correct"} for item in result_items
        )
        auto_settle_verification = _can_auto_settle_objective_verification(
            mode=submitted["mode"],
            result_items=result_items,
        )
        requires_cause = not all_correct
        review = self._repository.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": command_id,
                "review_id": review_id,
                "user_id": user_id,
                "session_id": session_id,
                "revision": 1,
                "state": (
                    "mastery_recorded"
                    if auto_settle_verification
                    else "reflection_due"
                ),
                "requires_cause_decision": requires_cause,
                "reflection_version_id": None,
                "cause_candidate_version_id": None,
                "cause_decision_version_id": None,
                "supersedes_review_version_id": None,
                "created_at": submitted_at,
            },
        )
        pending = (
            None
            if auto_settle_verification
            else record_pending_xingce_evidence(
                session,
                repository=self._repository,
                command_id=command_id,
                user_id=user_id,
                task_session=submitted,
                assessment_ids=assessment_ids,
                created_at=submitted_at,
            )
        )
        plan_completion = complete_xingce_plan_task(
            session,
            user_id=user_id,
            task_session=submitted,
            command_id=command_id,
            completed_at=submitted_at,
        )
        completion = completion_receipt(plan_completion)
        evidence = (
            record_settled_xingce_evidence(
                session,
                repository=self._repository,
                command_id=command_id,
                user_id=user_id,
                task_session=submitted,
                assessment_ids=assessment_ids,
                execution_version_id=plan_completion.execution_version_id,
                created_at=submitted_at,
            )
            if auto_settle_verification
            else pending
        )
        if evidence is None:
            raise RuntimeError("Xingce submission evidence was not recorded")
        transitioned = submitted
        mastery = None
        if auto_settle_verification:
            try:
                mastery = self._mastery_service.record_objective_verification(
                    session,
                    user_id=user_id,
                    task_session=submitted,
                    review=review,
                    command_id=command_id,
                    recorded_at=submitted_at,
                )
            except XingceMasteryConflict as exc:
                raise XingceAttemptError(
                    "xingce_mastery_source_conflict",
                    "复测绑定的掌握状态已经变化，请刷新今日计划后重试。",
                    status_code=409,
                ) from exc
            transitioned = self._runtime_repository.settle_session(
                session,
                session_id=session_id,
                user_id=user_id,
                updated_at=submitted_at,
            )
            if transitioned is None:
                raise _conflict("task session settlement state changed")
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
                *[f"attempt-record:{record['attempt_record_id']}" for record in attempt_records],
                f"planning-evidence:{evidence['evidence_version_id']}",
            ],
            reason_code="xingce_attempt_group_submitted",
        )
        if auto_settle_verification:
            _append_state_event(
                self._outbox,
                session,
                event_type="xingce-verification-auto-settled@v1",
                producer="xingce_learning",
                aggregate_type="xingce_attempt_review",
                aggregate_id=review_id,
                aggregate_revision=int(review["revision"]),
                user_id=user_id,
                command_id=command_id,
                occurred_at=submitted_at,
                from_state="",
                to_state="mastery_recorded",
                resource_version_refs=[
                    f"review:{review_id}:{review['revision']}",
                    f"mastery-version:{mastery['mastery_version_id']}",
                ],
                reason_code="objective_verification_all_correct_no_cause",
            )
            _append_state_event(
                self._outbox,
                session,
                event_type="task-session-state-changed@v1",
                producer="task_runtime",
                aggregate_type="task_session",
                aggregate_id=session_id,
                aggregate_revision=int(transitioned["revision"]),
                user_id=user_id,
                command_id=command_id,
                occurred_at=submitted_at,
                from_state="submitted",
                to_state="settled",
                resource_version_refs=[
                    f"task-session:{session_id}:{transitioned['revision']}",
                    f"review:{review_id}:{review['revision']}",
                ],
                reason_code="objective_verification_all_correct_no_cause",
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
        _validate_item_interpretations(
            command.item_interpretations,
            assessments=self._repository.assessments(
                session,
                user_id=user_id,
                session_id=str(task_session["session_id"]),
            ),
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
        try:
            self._media_repository.bind_available_artifacts(
                session,
                user_id=user_id,
                artifact_version_ids=[str(item) for item in command.voice_artifact_version_ids],
                entity_type="xingce_reflection",
                entity_version_id=reflection_id,
                created_at=recorded_at,
            )
        except (ValueError, IntegrityError) as exc:
            raise XingceAttemptError(
                "xingce_reflection_voice_binding_conflict",
                "语音复盘附件不可用、属于其他用户或已经绑定。",
                status_code=409,
            ) from exc
        try:
            self._cause_agent_workflow.start_after_reflection(
                session,
                user_id=user_id,
                task_session=task_session,
                review=review,
                reflection_version_id=reflection_id,
                command_id=command_id,
                created_at=recorded_at,
            )
        except CauseAgentWorkflowError as exc:
            raise XingceAttemptError(
                "xingce_cause_agent_contract_conflict",
                "解题 Agent 输入版本已经变化，请刷新复盘状态后重试。",
                status_code=409,
            ) from exc
        except CommerceQuotaExceededError as exc:
            raise XingceAttemptError(
                "xingce_cause_quota_exhausted",
                "今日行测错因分析额度已用完，本次复盘未写入。",
                status_code=429,
            ) from exc
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

    def submit_cause_followup(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
        command_id: str,
        command: CauseFollowupCommand,
        now: datetime | None = None,
    ) -> TaskSessionProjection:
        recorded_at = _utc(now)
        fingerprint = _fingerprint("cause_followup", review_id, command)
        self._runtime_repository.lock_user(session, user_id=user_id)
        replay = self._replay(session, user_id, command_id, fingerprint)
        if replay is not None:
            return replay
        review = self._required_review(session, user_id, review_id)
        if (
            int(review["revision"]) != command.expected_revision
            or review["state"] != "cause_followup_due"
            or review["cause_candidate_version_id"] is None
            or str(review["cause_candidate_version_id"]) != str(command.candidate_version_id)
        ):
            raise _conflict("cause-followup review revision changed")
        candidate = self._repository.cause_candidate(
            session,
            candidate_version_id=str(command.candidate_version_id),
        )
        if (
            candidate is None
            or str(candidate["user_id"]) != user_id
            or str(candidate["review_id"]) != review_id
            or str(candidate["state"]) != "followup_required"
        ):
            raise _conflict("cause-followup candidate is not current")
        task_session = self._required_session(
            session,
            user_id,
            str(review["session_id"]),
            for_update=True,
        )
        try:
            self._cause_agent_workflow.submit_followup(
                session,
                user_id=user_id,
                task_session=task_session,
                review=review,
                candidate=candidate,
                command_id=command_id,
                answer=command.answer,
                created_at=recorded_at,
            )
        except CauseAgentWorkflowError as exc:
            raise _conflict(exc.code) from exc
        except CommerceQuotaExceededError as exc:
            raise XingceAttemptError(
                "xingce_cause_quota_exhausted",
                "今日行测错因分析额度已用完，追问未写入。",
                status_code=429,
            ) from exc
        projection = self._projection(
            session,
            user_id=user_id,
            task_session=task_session,
        )
        self._record_command(
            session,
            user_id=user_id,
            command_id=command_id,
            command_type="cause_followup",
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
        initial_states = {
            "cause_decision_due",
            "cause_evidence_insufficient",
            "cause_agent_terminated",
        }
        revision_states = {"immediate_verification_due", "mastery_recorded"}
        is_revision = str(review["state"]) in revision_states
        if int(review["revision"]) != command.expected_revision or str(review["state"]) not in (
            initial_states | revision_states
        ):
            raise _conflict("cause-decision review revision changed")
        if review["reflection_version_id"] is None or review["cause_candidate_version_id"] is None:
            raise _conflict("cause-decision review has no frozen candidate")
        if command.decision != "skip" and str(review["cause_candidate_version_id"]) != str(
            command.candidate_version_id
        ):
            raise _conflict("cause decision must bind the current candidate version")
        task_session = self._required_session(
            session,
            user_id,
            str(review["session_id"]),
            for_update=True,
        )
        if is_revision and self._repository.verification_started_for_review(
            session,
            user_id=user_id,
            review_id=review_id,
        ):
            raise XingceAttemptError(
                "xingce_cause_decision_revision_closed",
                "复测已经开始，不能再修改本次错因确认。",
                status_code=409,
            )
        candidate = self._repository.cause_candidate(
            session,
            candidate_version_id=str(review["cause_candidate_version_id"]),
        )
        if candidate is None or str(candidate["user_id"]) != user_id:
            raise _conflict("cause-decision candidate is unavailable")
        if command.decision == "confirm" and str(candidate["state"]) != "candidate_ready":
            raise _conflict("only a ready candidate can be confirmed")
        if command.decision == "skip" and str(candidate["state"]) != "unavailable":
            raise _conflict("legacy skip applies only to historical unavailable candidates")
        previous_decision = self._repository.cause_decision(
            session,
            decision_version_id=(
                str(review["cause_decision_version_id"])
                if review["cause_decision_version_id"] is not None
                else None
            ),
        )
        decision_id = str(uuid4())
        confirmed_cause_id = str(uuid4()) if command.decision in {"confirm", "replace"} else None
        self._repository.insert_cause_decision(
            session,
            values={
                "decision_version_id": decision_id,
                "command_id": command_id,
                "user_id": user_id,
                "review_id": review_id,
                "session_id": str(task_session["session_id"]),
                "version_no": (
                    int(previous_decision["version_no"]) + 1 if previous_decision is not None else 1
                ),
                "decision": command.decision,
                "replacement_cause": command.replacement_cause,
                "candidate_version_id": (
                    str(command.candidate_version_id)
                    if command.candidate_version_id is not None
                    else None
                ),
                "confirmed_cause_version_id": confirmed_cause_id,
                "supersedes_decision_version_id": (
                    str(previous_decision["decision_version_id"])
                    if previous_decision is not None
                    else None
                ),
                "created_at": recorded_at,
            },
        )
        if confirmed_cause_id is not None:
            candidate_payload = dict(candidate["payload"])
            evidence_refs = list(candidate_payload.get("evidenceRefs", [])) or [
                f"reflection-version:{review['reflection_version_id']}",
                f"cause-candidate-version:{candidate['candidate_version_id']}",
            ]
            self._cause_agent_repository.insert_confirmed_cause(
                session,
                values={
                    "confirmed_cause_version_id": confirmed_cause_id,
                    "confirmed_cause_id": str(uuid4()),
                    "command_id": command_id,
                    "user_id": user_id,
                    "review_id": review_id,
                    "session_id": str(task_session["session_id"]),
                    "decision_version_id": decision_id,
                    "candidate_version_id": str(candidate["candidate_version_id"]),
                    "version_no": 1,
                    "source_kind": (
                        "ai_confirmed" if command.decision == "confirm" else "user_replacement"
                    ),
                    "cause_code": (
                        str(candidate_payload["causeCode"])
                        if command.decision == "confirm"
                        else "user_replacement"
                    ),
                    # 结构化改写码：用户改写时可选的 8 码之一；未选码保持 null，
                    # 旧行为（单一 user_replacement 桶）完全不变。
                    "replacement_code": (
                        command.replacement_code if command.decision == "replace" else None
                    ),
                    "cause_text": (
                        str(candidate_payload["summary"])
                        if command.decision == "confirm"
                        else str(command.replacement_cause)
                    ),
                    "evidence_refs": _json(evidence_refs),
                    "supersedes_confirmed_cause_version_id": None,
                    "created_at": recorded_at,
                },
            )
        terminal_review_state = (
            "mastery_recorded"
            if str(task_session["mode"]) == "verification"
            else "immediate_verification_due"
        )
        decided_review = self._repository.insert_review_version(
            session,
            values={
                "review_version_id": str(uuid4()),
                "command_id": command_id,
                "review_id": review_id,
                "user_id": user_id,
                "session_id": str(task_session["session_id"]),
                "revision": int(review["revision"]) + 1,
                "state": terminal_review_state,
                "requires_cause_decision": True,
                "reflection_version_id": str(review["reflection_version_id"]),
                "cause_candidate_version_id": str(review["cause_candidate_version_id"]),
                "cause_decision_version_id": decision_id,
                "supersedes_review_version_id": str(review["review_version_id"]),
                "created_at": recorded_at,
            },
        )
        mastery = None
        completion_payload = self._runtime_repository.completion_payload(
            session,
            user_id=user_id,
            session_id=str(task_session["session_id"]),
        )
        completion = (
            XingceCompletionReceipt.model_validate(completion_payload, strict=False)
            if completion_payload is not None
            else None
        )
        transitioned = task_session
        if not is_revision:
            if task_session["state"] != "submitted":
                raise _conflict("task session is not awaiting track settlement")
            try:
                mastery = self._mastery_service.record_cause_decision(
                    session,
                    user_id=user_id,
                    task_session=task_session,
                    review=decided_review,
                    command_id=command_id,
                    recorded_at=recorded_at,
                )
            except XingceMasteryConflict as exc:
                raise XingceAttemptError(
                    "xingce_mastery_source_conflict",
                    "复测绑定的掌握状态已经变化，请刷新今日计划后重试。",
                    status_code=409,
                ) from exc
            completion = settle_completion_after_cause(
                session,
                repository=self._repository,
                runtime_repository=self._runtime_repository,
                user_id=user_id,
                task_session=task_session,
                command_id=command_id,
                recorded_at=recorded_at,
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
            from_state=str(review["state"]),
            to_state=terminal_review_state,
            resource_version_refs=[
                f"cause-decision-version:{decision_id}",
                f"review:{review_id}:{int(review['revision']) + 1}",
                *(
                    [f"mastery-version:{mastery['mastery_version_id']}"]
                    if mastery is not None
                    else []
                ),
            ],
            reason_code=f"user_cause_{command.decision}",
        )
        if not is_revision:
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
        items: list[XingceResultItem] = []
        aggregate_code: str | None = None
        if assessments:
            items = []
            for assessment in assessments:
                snapshot = dict(assessment["result_snapshot"])
                if not str(snapshot.get("explanation") or "").strip():
                    fallback, fallback_source = stored_explanation(
                        assessment.get("content_snapshot")
                    )
                    if fallback is not None:
                        snapshot["explanation"] = fallback
                        snapshot["explanationSource"] = fallback_source
                items.append(XingceResultItem.model_validate(snapshot))
            codes = [item.result_code for item in items]
            aggregate_code = codes[0] if len(set(codes)) == 1 else "mixed"
        review_projection: AttemptReviewProjection | None = None
        review = _review_for_session(session, session_id=session_id, user_id=user_id)
        reflection: RowMapping | None = None
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
            if candidate is not None and bool(candidate["charged"]):
                raise XingceAttemptError(
                    "xingce_candidate_contract_invalid",
                    "行测解题 Agent 属于训练闭环内含能力，不能发布 charged 候选。",
                    status_code=500,
                )
            candidate_projection = _candidate_projection(candidate)
            review_projection = AttemptReviewProjection(
                reviewId=str(review["review_id"]),
                revision=int(review["revision"]),
                state=_api_review_state(str(review["state"])),
                aiCandidate=candidate_projection,
                reflection=(
                    ReflectionVersionProjection(
                        reflectionVersionId=str(reflection["reflection_version_id"]),
                        rawText=str(reflection["payload"]["rawText"]),
                        tags=list(reflection["payload"]["tags"]),
                        strategyNote=reflection["payload"].get("strategyNote"),
                        doubtNote=reflection["payload"].get("doubtNote"),
                        voiceArtifactVersionIds=reflection["payload"].get(
                            "voiceArtifactVersionIds", []
                        ),
                        itemInterpretations=reflection["payload"].get("itemInterpretations", []),
                        versionNo=int(reflection["version_no"]),
                    )
                    if reflection is not None
                    else None
                ),
                causeDecision=(
                    CauseDecisionVersionProjection(
                        decisionVersionId=str(decision["decision_version_id"]),
                        candidateVersionId=decision["candidate_version_id"],
                        confirmedCauseVersionId=decision["confirmed_cause_version_id"],
                        decision=str(decision["decision"]),
                        replacementCause=decision["replacement_cause"],
                        versionNo=int(decision["version_no"]),
                    )
                    if decision is not None
                    else None
                ),
            )
        group_summary = _group_summary(
            items,
            review_state=str(review["state"]) if review is not None else None,
            reflection_payload=(dict(reflection["payload"]) if reflection is not None else None),
        )
        result: XingceAttemptResult | None = None
        if items and aggregate_code is not None:
            change_counts = [item.answer_change_count for item in items]
            result = XingceAttemptResult(
                contractVersion=("xingce-attempt-result@v2" if group_summary is not None else None),
                attemptId=str(task_session["attempt_group_id"]),
                resultCode=aggregate_code,
                correctCount=sum(
                    item.objective_verdict == "correct"
                    if item.objective_verdict is not None
                    else item.result_code in {"correct", "guessed_correct"}
                    for item in items
                ),
                totalCount=len(items),
                # 老结果快照没有改答事实时保持 None，不做推测。
                changedAnswerCount=(
                    sum(value for value in change_counts if value is not None)
                    if any(value is not None for value in change_counts)
                    else None
                ),
                items=items,
                groupSummary=group_summary,
            )
        verification_outcome = self._verification_outcome(
            session,
            user_id=user_id,
            task_session=task_session,
            items=items,
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
            answers=[AnswerCheckpoint.model_validate(item) for item in checkpoint["answers"]],
            totalElapsedSeconds=int(task_session["total_elapsed_seconds"]),
            result=result,
            verificationOutcome=verification_outcome,
            review=review_projection,
            completion=completion,
        )

    def _verification_outcome(
        self,
        session: Session,
        *,
        user_id: str,
        task_session: RowMapping,
        items: list[XingceResultItem],
    ) -> VerificationOutcomeProjection | None:
        if str(task_session["mode"]) != "verification" or not items:
            return None
        if any(
            item.fact_contract_version != RESULT_FACT_CONTRACT_VERSION
            or item.objective_verdict is None
            or item.disposition is None
            for item in items
        ):
            return None
        source_version_id = task_session["source_mastery_version_id"]
        if source_version_id is None:
            raise XingceAttemptError(
                "xingce_verification_source_missing",
                "复测结果没有绑定冻结的掌握版本。",
                status_code=500,
            )
        source = self._repository.verification_source(
            session,
            user_id=user_id,
            mastery_version_id=str(source_version_id),
        )
        scoring_context = dict(task_session["scoring_bundle"]).get("verificationContext")
        if (
            source is None
            or not isinstance(scoring_context, dict)
            or scoring_context.get("sourceMasteryVersionId") != str(source_version_id)
            or scoring_context.get("verificationStage") != str(source["verification_stage"])
        ):
            raise XingceAttemptError(
                "xingce_verification_source_invalid",
                "复测结果与冻结掌握版本或复测阶段不一致。",
                status_code=500,
            )
        has_dispute = self._repository.session_has_question_dispute(
            session,
            user_id=user_id,
            session_id=str(task_session["session_id"]),
        )
        outcome, evidence_state = _verification_status(
            items,
            has_pending_dispute=has_dispute,
            confirmed_invalidated=(
                not self._repository.session_content_is_currently_eligible(
                    session,
                    user_id=user_id,
                    session_id=str(task_session["session_id"]),
                )
            ),
        )
        return VerificationOutcomeProjection(
            outcome=outcome,
            evidenceState=evidence_state,
            sourceMasteryVersionId=str(source_version_id),
            verificationStage=str(source["verification_stage"]),
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
        return TaskSessionProjection.model_validate_json(_json(existing["response_payload"]))

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
            "taskSessionRef": (f"task-session:{session_id}:{attempt_record['session_revision']}"),
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


def _dispute_receipt(row: RowMapping) -> QuestionDisputeReceipt:
    return QuestionDisputeReceipt(
        disputeId=str(row["dispute_id"]),
        sessionId=str(row["session_id"]),
        questionId=str(row["question_id"]),
        contentVersionId=str(row["content_version_id"]),
        category=str(row["category"]),
        description=str(row["description"]),
        evidenceAttached=True,
        createdAt=row["created_at"],
    )


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


def _validate_item_interpretations(
    interpretations: list[ItemInterpretation],
    *,
    assessments: list[RowMapping],
) -> None:
    question_ids = [item.question_id for item in interpretations]
    if len(interpretations) > 3 or len(question_ids) != len(set(question_ids)):
        raise XingceAttemptError(
            "xingce_reflection_interpretation_conflict",
            "逐题解读最多三条，且每道题只能确认一次。",
            status_code=409,
        )
    by_question = {str(row["question_id"]): row for row in assessments}
    for interpretation in interpretations:
        if interpretation.interpretation != "method_correct_calculation_error":
            raise XingceAttemptError(
                "xingce_reflection_interpretation_unsupported",
                "当前逐题解读类型不受支持。",
                status_code=409,
            )
        assessment = by_question.get(interpretation.question_id)
        if assessment is None:
            raise XingceAttemptError(
                "xingce_reflection_interpretation_scope_conflict",
                "逐题解读必须绑定当前复盘会话中的题目。",
                status_code=409,
            )
        facts = dict(assessment["result_snapshot"])
        if (
            facts.get("factContractVersion") != RESULT_FACT_CONTRACT_VERSION
            or facts.get("objectiveVerdict") != "incorrect"
            or facts.get("disposition") != "answered"
        ):
            raise XingceAttemptError(
                "xingce_reflection_interpretation_not_applicable",
                "只能对客观判定错误且非主动放弃的题目确认“方法正确但计算错误”。",
                status_code=409,
            )


def _group_summary(
    items: list[XingceResultItem],
    *,
    review_state: str | None,
    reflection_payload: dict[str, Any] | None,
) -> XingceGroupSummary | None:
    if not items or any(
        item.fact_contract_version != RESULT_FACT_CONTRACT_VERSION
        or item.objective_verdict is None
        or item.disposition is None
        or item.confidence_evidence is None
        or item.timing_evidence is None
        for item in items
    ):
        return None
    guessed_correct = sum(
        item.objective_verdict == "correct" and item.confidence_evidence == "low" for item in items
    )
    genuine_correct = sum(
        item.objective_verdict == "correct" and item.confidence_evidence in {"medium", "high"}
        for item in items
    )
    active_abandon = sum(item.disposition == "active_abandon" for item in items)
    advisory_exceeded = sum(
        item.timing_evidence is not None and item.timing_evidence.status == "advisory_exceeded"
        for item in items
    )
    interpretations = (
        list(reflection_payload.get("itemInterpretations", []))
        if reflection_payload is not None
        else []
    )
    method_correct_calculation_error = sum(
        isinstance(item, dict) and item.get("interpretation") == "method_correct_calculation_error"
        for item in interpretations
    )
    if active_abandon == len(items):
        outcome = "interrupted"
    elif genuine_correct == len(items):
        outcome = "attained"
    else:
        outcome = "not_attained"
    return XingceGroupSummary(
        outcome=outcome,
        genuineCorrect=genuine_correct,
        guessedCorrect=guessed_correct,
        methodCorrectCalculationError=method_correct_calculation_error,
        advisoryExceeded=advisory_exceeded,
        activeAbandon=active_abandon,
        pendingReflection=review_state == "reflection_due",
        pendingCause=review_state
        in {
            "cause_candidate_pending",
            "cause_followup_due",
            "cause_decision_due",
            "cause_evidence_insufficient",
            "cause_agent_terminated",
        },
        immediateVerification=review_state == "immediate_verification_due",
        scheduled=review_state == "mastery_recorded",
    )


def _verification_status(
    items: list[XingceResultItem],
    *,
    has_pending_dispute: bool,
    confirmed_invalidated: bool = False,
) -> tuple[str, str]:
    if has_pending_dispute:
        return "disputed_or_invalidated", "pending_dispute"
    if confirmed_invalidated:
        return "disputed_or_invalidated", "confirmed_invalidated"
    if all(
        item.objective_verdict == "correct" and item.disposition == "answered" for item in items
    ):
        return "passed", "valid"
    return "not_passed", "valid"


def _freeze_bundle(
    source_questions: list[dict[str, Any]],
    *,
    estimated_minutes: int,
    source_mastery_version_id: str | None,
    verification_stage: str | None,
    selection_context: dict[str, Any] | None = None,
) -> tuple[SafeQuestionBundle, dict[str, Any]]:
    try:
        safe_bundle, scoring_bundle = freeze_learner_bundle(
            source_questions,
            scoring_policy_version=SCORING_POLICY_VERSION,
            content_version_id=_bound_question_content_version_id,
            published_explanation=_published_explanation,
        )
        advisory_seconds = estimated_minutes * 60 // len(source_questions)
        if advisory_seconds < 1:
            raise XingceAttemptError(
                "xingce_timing_policy_invalid",
                "计划时长无法为固定三题生成有效建议时长。",
                status_code=500,
            )
        scoring_bundle["timingPolicy"] = {
            "contractVersion": "xingce-advisory-timing@v1",
            "behavior": "advisory",
            "questionCount": len(source_questions),
            "totalAdvisorySeconds": estimated_minutes * 60,
            "perQuestionAdvisorySeconds": advisory_seconds,
        }
        for question in scoring_bundle["questions"]:
            question["advisorySeconds"] = advisory_seconds
        if source_mastery_version_id is not None or verification_stage is not None:
            if source_mastery_version_id is None or verification_stage is None:
                raise XingceAttemptError(
                    "xingce_verification_source_invalid",
                    "复测计分包缺少冻结掌握版本或复测阶段。",
                    status_code=500,
                )
            scoring_bundle["verificationContext"] = {
                "sourceMasteryVersionId": source_mastery_version_id,
                "verificationStage": verification_stage,
            }
        if selection_context is not None:
            scoring_bundle["selectionContext"] = dict(selection_context)
        scoring_bundle[XINGCE_SESSION_GENERATION_KEY] = XINGCE_SESSION_GENERATION_V1
        scoring_bundle["scoringBundleVersion"] = _hash(scoring_bundle)
        return safe_bundle, scoring_bundle
    except TaskContentContractError as exc:
        code = {
            "visual_asset_unavailable": "xingce_visual_asset_unavailable",
            "answer_key_invalid": "xingce_answer_key_invalid",
        }.get(exc.code, "xingce_content_contract_invalid")
        raise XingceAttemptError(code, exc.message, status_code=503) from exc


def _bound_question_content_version_id(source: dict[str, Any]) -> str:
    content_version_id = source.get("_content_version_id")
    if (
        not isinstance(content_version_id, str)
        or len(content_version_id) != 64
        or question_content_version_id(source) != content_version_id
    ):
        raise TaskContentContractError(
            "content_version_binding_invalid",
            "The selected question does not match its frozen content version.",
        )
    return content_version_id


def _selection_lineage_refs(
    scoring_bundle: dict[str, Any],
    *,
    mode: str,
) -> list[str]:
    context = scoring_bundle.get("selectionContext")
    if mode != "targeted":
        return []
    if context is None:
        # Pre-selection-contract sessions already froze their learner-safe
        # bundle and must remain submittable without inventing release evidence.
        return []
    if (
        not isinstance(context, dict)
    ):
        raise XingceAttemptError(
            "xingce_selection_context_invalid",
            "针对训练的知识树选择证据不完整，不能提交本次结果。",
            status_code=500,
        )
    if context.get("contractVersion") == "private-question-book-selection@v1":
        return _question_book_lineage_refs(context, scoring_bundle=scoring_bundle)
    if context.get("contractVersion") == "xingce-selection-context@v2":
        return _selection_v2_lineage_refs(context, scoring_bundle=scoring_bundle)
    if context.get("contractVersion") == "xingce-module-selection@v1":
        if (
            set(context) != {"contractVersion", "moduleName"}
            or not isinstance(context.get("moduleName"), str)
            or not context["moduleName"].strip()
        ):
            raise _invalid_selection_context()
        return [f"selection-module:{context['moduleName']}"]
    if context.get("contractVersion") != "xingce-targeted-selection@v1":
        raise XingceAttemptError(
            "xingce_selection_context_invalid",
            "针对训练的知识树选择证据不完整，不能提交本次结果。",
            status_code=500,
        )
    required = {
        "releaseId": "knowledge-map-release",
        "releaseVersion": "knowledge-map-release-version",
        "sourceDataVersion": "knowledge-map-source-data-version",
        "vocabVersion": "knowledge-map-vocab-version",
        "nodeId": "knowledge-map-node",
    }
    evidence_only_fields = ("freshUntil", "nodeType", "moduleName")
    refs: list[str] = []
    for field in (*required, *evidence_only_fields):
        value = context.get(field)
        if not isinstance(value, str) or not value.strip():
            raise XingceAttemptError(
                "xingce_selection_context_invalid",
                "针对训练的知识树选择证据不完整，不能提交本次结果。",
                status_code=500,
            )
        prefix = required.get(field)
        if prefix is not None:
            refs.append(f"{prefix}:{value}")
    if context["nodeType"] not in {"micro_skill", "knowledge_point"}:
        raise XingceAttemptError(
            "xingce_selection_context_invalid",
            "针对训练的知识树选择证据不完整，不能提交本次结果。",
            status_code=500,
        )
    try:
        fresh_until = datetime.fromisoformat(context["freshUntil"])
    except ValueError as exc:
        raise XingceAttemptError(
            "xingce_selection_context_invalid",
            "针对训练的知识树选择证据不完整，不能提交本次结果。",
            status_code=500,
        ) from exc
    if fresh_until.tzinfo is None:
        raise XingceAttemptError(
            "xingce_selection_context_invalid",
            "针对训练的知识树选择证据不完整，不能提交本次结果。",
            status_code=500,
        )
    return refs


def _explicit_session_source_matches(
    task_session: RowMapping,
    *,
    expected_source_identity: str,
) -> bool:
    scoring_bundle = task_session["scoring_bundle"]
    if not isinstance(scoring_bundle, dict):
        return False
    context = scoring_bundle.get("selectionContext")
    if not isinstance(context, dict):
        return False
    if context.get("contractVersion") != "private-question-book-selection@v1":
        return False
    book_id = context.get("bookId")
    return (
        isinstance(book_id, str)
        and expected_source_identity == f"private-question-book:{book_id}"
    )


def _question_book_lineage_refs(
    context: dict[str, Any],
    *,
    scoring_bundle: dict[str, Any],
) -> list[str]:
    required_context = {
        "contractVersion",
        "sourceKind",
        "bookId",
        "sourceVersionId",
        "artifactVersionId",
        "recognitionRevisionId",
        "totalQuestionCount",
        "selectedItems",
        "excludedCounts",
    }
    excluded_fields = {
        "needsConfirmation",
        "unmatched",
        "contentHeadChanged",
        "notDeliveryEligible",
        "contentContractInvalid",
        "duplicate",
        "overPlanLimit",
    }
    item_fields = {
        "questionUnitId",
        "contentMatchVersionId",
        "questionId",
        "contentVersionId",
        "deliveryReleaseId",
    }
    selected = context.get("selectedItems")
    excluded = context.get("excludedCounts")
    if (
        set(context) != required_context
        or context.get("contractVersion") != "private-question-book-selection@v1"
        or context.get("sourceKind") != "question_book"
        or not isinstance(selected, list)
        or not selected
        or not isinstance(excluded, dict)
        or set(excluded) != excluded_fields
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in excluded.values()
        )
        or isinstance(context.get("totalQuestionCount"), bool)
        or not isinstance(context.get("totalQuestionCount"), int)
        or context["totalQuestionCount"] != len(selected) + sum(excluded.values())
    ):
        raise _invalid_selection_context()
    for field in (
        "bookId",
        "sourceVersionId",
        "artifactVersionId",
        "recognitionRevisionId",
    ):
        try:
            UUID(str(context.get(field)))
        except (TypeError, ValueError) as exc:
            raise _invalid_selection_context() from exc
    scoring_questions = scoring_bundle.get("questions")
    if not isinstance(scoring_questions, list) or len(scoring_questions) != len(selected):
        raise _invalid_selection_context()
    seen_questions: set[str] = set()
    refs = [
        f"private-question-book:{context['bookId']}",
        f"private-question-book-version:{context['sourceVersionId']}",
        f"source-artifact-version:{context['artifactVersionId']}",
        f"recognition-revision:{context['recognitionRevisionId']}",
    ]
    for index, item in enumerate(selected):
        if not isinstance(item, dict) or set(item) != item_fields:
            raise _invalid_selection_context()
        scoring = scoring_questions[index]
        if not isinstance(scoring, dict):
            raise _invalid_selection_context()
        question_id = item.get("questionId")
        content_version_id = item.get("contentVersionId")
        question_unit_id = item.get("questionUnitId")
        if (
            not isinstance(question_id, str)
            or not question_id
            or question_id in seen_questions
            or scoring.get("questionId") != question_id
            or not isinstance(content_version_id, str)
            or len(content_version_id) != 64
            or scoring.get("contentVersionId") != content_version_id
            or not isinstance(question_unit_id, str)
            or len(question_unit_id) != 64
        ):
            raise _invalid_selection_context()
        try:
            UUID(str(item.get("contentMatchVersionId")))
            UUID(str(item.get("deliveryReleaseId")))
        except (TypeError, ValueError) as exc:
            raise _invalid_selection_context() from exc
        seen_questions.add(question_id)
        refs.extend(
            (
                f"content-match-version:{item['contentMatchVersionId']}",
                f"knowledge-map-release:{item['deliveryReleaseId']}",
            )
        )
    return refs


def _selection_v2_lineage_refs(
    context: dict[str, Any],
    *,
    scoring_bundle: dict[str, Any],
) -> list[str]:
    required_context = {
        "contractVersion",
        "intent",
        "result",
        "releaseId",
        "releaseVersion",
        "sourceDataVersion",
        "vocabVersion",
        "freshUntil",
        "nodeId",
        "nodeType",
        "moduleName",
    }
    if set(context) != required_context:
        raise _invalid_selection_context()
    try:
        intent = validate_selection_intent(context["intent"])
    except XingceSelectionIntentError as exc:
        raise _invalid_selection_context() from exc
    result = context["result"]
    required_result = {
        "contractVersion",
        "intentId",
        "hitTier",
        "hitReason",
        "fallbackReason",
        "skippedTiers",
        "difficultyPolicy",
        "questionRankingEvidence",
        "questionIds",
    }
    scoring_question_ids = [
        str(item["questionId"])
        for item in scoring_bundle.get("questions", [])
        if isinstance(item, dict) and item.get("questionId")
    ]
    if (
        not isinstance(result, dict)
        or set(result) != required_result
        or result.get("contractVersion") != "xingce-selection-result@v1"
        or result.get("intentId") != selection_intent_fingerprint(intent)
        or result.get("difficultyPolicy") != intent["difficultyPolicy"]
        or result.get("questionIds") != scoring_question_ids
        or result.get("hitTier") not in {
            "exact_knowledge_and_cause",
            "same_knowledge",
            "same_micro_skill",
            "module",
            "high_frequency_cold_start",
        }
        or result.get("hitReason") not in {
            "all_required_selectors_matched",
            "first_tier_with_sufficient_novel_supply",
        }
        or result.get("fallbackReason") != intent["fallbackReason"]
        or not isinstance(result.get("questionRankingEvidence"), list)
        or len(result["questionRankingEvidence"]) != len(scoring_question_ids)
        or not isinstance(result.get("skippedTiers"), list)
    ):
        raise _invalid_selection_context()
    if any(
        not isinstance(item, dict)
        or set(item) != {
            "questionId",
            "rank",
            "anchorState",
            "difficultyB",
            "distanceToTargetTheta",
            "stablePoolRank",
        }
        or item["questionId"] != scoring_question_ids[index]
        or item["rank"] != index + 1
        or item["anchorState"] not in {"known", "unknown"}
        for index, item in enumerate(result["questionRankingEvidence"])
    ):
        raise _invalid_selection_context()
    release = intent["knowledgeRelease"]
    for field, expected in (
        ("releaseId", release["releaseId"]),
        ("releaseVersion", release["releaseVersion"]),
        ("sourceDataVersion", release["sourceDataVersion"]),
        ("vocabVersion", release["vocabVersion"]),
        ("freshUntil", release["freshUntil"]),
        ("moduleName", intent["targetModule"]),
    ):
        if context[field] != expected:
            raise _invalid_selection_context()
    try:
        fresh_until = datetime.fromisoformat(str(context["freshUntil"]))
    except ValueError as exc:
        raise _invalid_selection_context() from exc
    if fresh_until.tzinfo is None:
        raise _invalid_selection_context()
    refs = [
        f"selection-intent:{intent['intentId']}",
        f"selection-result:{_hash(result)}",
        f"knowledge-map-release:{release['releaseId']}",
        f"knowledge-map-release-version:{release['releaseVersion']}",
        f"knowledge-map-source-data-version:{release['sourceDataVersion']}",
        f"knowledge-map-vocab-version:{release['vocabVersion']}",
    ]
    for node_field in ("targetKnowledgeNode", "targetMicroSkillNode"):
        node = intent[node_field]
        if node is not None:
            refs.append(f"knowledge-map-node:{node['nodeId']}")
    cause = intent["confirmedCauseFocus"]
    if cause is not None:
        refs.extend(
            (
                f"confirmed-cause-version:{cause['confirmedCauseVersionId']}",
                f"cause-decision-version:{cause['decisionVersionId']}",
            )
        )
    difficulty = intent["difficultyPolicy"]
    if difficulty["sourceVersionId"] is not None:
        refs.append(f"ability-shadow:{difficulty['sourceVersionId']}")
    return refs


def _invalid_selection_context() -> XingceAttemptError:
    return XingceAttemptError(
        "xingce_selection_context_invalid",
        "针对训练的个性化选择证据不完整，不能提交本次结果。",
        status_code=500,
    )


def _task_scope(task: RowMapping) -> tuple[str, str | None]:
    if task["stage"] == "objective_baseline":
        return "baseline", None
    if task["stage"] == "objective_verification":
        return "verification", None
    candidate = str(task["candidate_key"])
    module_name = candidate.split(":", 1)[1].strip() if ":" in candidate else None
    if module_name == "国考高频模块":
        module_name = None
    return "targeted", module_name


def _verification_mastery_ref(task: RowMapping) -> tuple[str, str] | None:
    refs = [
        item
        for item in task["evidence_refs"]
        if item.get("entityType") == "xingce_mastery_state_version"
        and item.get("role") == "due_verification"
    ]
    if len(refs) != 1:
        return None
    entity_id = str(refs[0].get("entityId") or "")
    version_id = str(refs[0].get("versionId") or "")
    if not entity_id or not version_id:
        return None
    return entity_id, version_id


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
        return "cause_candidate_processing"
    if state == "submitted" and review_state == "cause_followup_due":
        return "cause_followup_required"
    if state == "submitted" and review_state == "cause_decision_due":
        return "cause_decision_required"
    if state == "submitted" and review_state == "cause_evidence_insufficient":
        return "cause_evidence_insufficient"
    if state == "submitted" and review_state == "cause_agent_terminated":
        return "cause_agent_terminated"
    if state == "settled" and review_state in {
        "immediate_verification_due",
        "mastery_recorded",
    }:
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
        "cause_followup_due": "cause_followup_required",
        "cause_decision_due": "cause_decision_required",
        "cause_evidence_insufficient": "cause_evidence_insufficient",
        "cause_agent_terminated": "cause_agent_terminated",
        "immediate_verification_due": "immediate_verification_due",
        "mastery_recorded": "mastery_recorded",
    }[state]


def _candidate_projection(candidate: RowMapping | None) -> AICauseCandidateState:
    if candidate is None:
        return AICauseCandidateState(state="processing")
    state = str(candidate["state"])
    if state == "unavailable":
        return AICauseCandidateState(
            state="unavailable",
            candidateVersionId=str(candidate["candidate_version_id"]),
            reason=str(candidate["reason_code"]),
        )
    payload = dict(candidate["payload"])
    return AICauseCandidateState(
        state=state,
        candidateVersionId=str(candidate["candidate_version_id"]),
        reason=(str(candidate["reason_code"]) if candidate["reason_code"] is not None else None),
        causeCode=payload.get("causeCode"),
        summary=payload.get("summary"),
        improvement=payload.get("improvement"),
        question=payload.get("question"),
        rationale=payload.get("rationale"),
        explanation=payload.get("explanation"),
        missingEvidence=payload.get("missingEvidence", []),
        evidenceRefs=payload.get("evidenceRefs", []),
        limitations=payload.get("limitations", []),
        # 契约增量：逐题候选原样投影；老候选 payload 没有该键时保持空。
        questionCandidates=payload.get("questionCandidates", []),
        quotaState=payload.get("quotaState", "consumed"),
    )


def _require_revision(row: RowMapping, expected: int, *, state: str) -> None:
    if int(row["revision"]) != expected or row["state"] != state:
        raise _conflict("task session revision changed")


def _not_found() -> XingceAttemptError:
    return XingceAttemptError(
        "xingce_session_not_found",
        "行测作答会话不存在。",
        status_code=404,
    )


def _can_auto_settle_objective_verification(
    *,
    mode: object,
    result_items: list[dict[str, Any]],
) -> bool:
    """Keep the no-reflection fast path narrower than generic all-correct."""
    return (
        str(mode) == "verification"
        and bool(result_items)
        and all(item.get("resultCode") == "correct" for item in result_items)
    )


def _selection_change_count(payloads: list[dict[str, Any]]) -> int:
    """改答 = 一个非空选择被替换为另一个不同的非空选择。

    以不可变答案版本（升序）为准；首次选择与清空后重选同一选项不计。
    """
    previous: list[str] | None = None
    count = 0
    for payload in payloads:
        selected = sorted(str(value) for value in payload.get("selectedOptionIds", []))
        if not selected:
            continue
        if previous is not None and previous != selected:
            count += 1
        previous = selected
    return count


def _conflict(message: str) -> XingceAttemptError:
    return XingceAttemptError(
        "xingce_revision_conflict",
        message,
        status_code=409,
    )


def _fingerprint(
    command_type: str, target: str, command: BaseModel | dict[str, object]
) -> str:
    body = (
        command.model_dump(by_alias=True, mode="json")
        if isinstance(command, BaseModel)
        else command
    )
    return _hash({"commandType": command_type, "target": target, "body": body})


def _hash(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    return current.astimezone(UTC)
