from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from math import sqrt
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.contracts.guokao_xingce import (
    GUOKAO_XINGCE_CATALOG_VERSION,
    GUOKAO_XINGCE_VICE_PROVINCIAL_BLUEPRINT,
)
from app.domains.knowledge.service import KnowledgeService
from app.domains.mock_exam.repository import (
    AssignedQuestionRecord,
    MockExamRepository,
    MockExamSessionRecord,
    MockExamTrajectoryWriteConflict,
)
from app.domains.mock_exam.schemas import (
    MockExamCheckpoint,
    MockExamCheckpointReceipt,
    MockExamCheckpointRequest,
    MockExamModuleBlueprint,
    MockExamModuleReview,
    MockExamPerformanceRange,
    MockExamReview,
    MockExamSession,
    MockExamStartRequest,
    MockExamSubmitRequest,
    MockExamSubmitResponse,
    MockExamWrongCluster,
    MockExamTrajectoryEventCommand,
    MockExamTrajectoryEventReceipt,
)
from app.domains.planning.mock_completion import (
    bind_and_start_mock_plan_task,
    complete_mock_plan_task,
)
from app.domains.progression.service import ProgressionService
from app.domains.task_content.schemas import SafeQuestionBundle
from app.domains.task_content.service import (
    TaskContentContractError,
    freeze_learner_bundle,
)
from app.domains.xingce_learning.content_version import question_content_version_id
from app.domains.xingce_learning.real_attempt.schemas import (
    QuestionDisputeReceipt,
    ReportQuestionDisputeCommand,
)


WEEKLY_MOCK_TASK_ID = "weekly-guokao-mock-exam"
MOCK_DURATION_MINUTES = 120
MOCK_SCORING_POLICY_VERSION = "mock-question-weighted-performance-index-v2"
GUOKAO_MOCK_BLUEPRINT = GUOKAO_XINGCE_VICE_PROVINCIAL_BLUEPRINT


class MockExamUnavailableError(RuntimeError):
    pass


class MockExamNotFoundError(LookupError):
    pass


class MockExamSubmissionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "mock_submission_invalid") -> None:
        super().__init__(message)
        self.code = code


class MockExamCheckpointError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "mock_checkpoint_invalid",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class MockExamDisputeError(RuntimeError):
    pass


def _trajectory_sequence_conflict(
    *,
    expected_event_seq: int,
    received_event_seq: int,
) -> MockExamCheckpointError:
    return MockExamCheckpointError(
        "作答轨迹已经在其他设备更新，请同步后继续。",
        code="mock_trajectory_sequence_conflict",
        details={
            "expectedEventSeq": expected_event_seq,
            "receivedEventSeq": received_event_seq,
        },
    )


class MockExamService:
    def __init__(
        self,
        *,
        repository: MockExamRepository,
        knowledge_service: KnowledgeService,
        progression_service: ProgressionService,
    ) -> None:
        self._repository = repository
        # Kept in the constructor for dependency compatibility. Learner delivery never
        # calls KnowledgeService.get_question because QuestionDetail contains answers.
        self._knowledge_service = knowledge_service
        self._progression_service = progression_service

    def create_weekly_mock(
        self,
        *,
        user_id: str,
        command: MockExamStartRequest | None = None,
        command_id: str | None = None,
    ) -> MockExamSession:
        started_at = datetime.now(UTC)
        plan_task_id = (
            str(command.plan_task_id)
            if command is not None and command.plan_task_id is not None
            else None
        )
        self._repository.lock_user(user_id=user_id)
        existing = self._repository.get_assigned_session(user_id=user_id)
        if existing is not None:
            if plan_task_id is not None:
                existing = bind_and_start_mock_plan_task(
                    self._repository.session,
                    mock_repository=self._repository,
                    user_id=user_id,
                    mock_session=existing,
                    plan_task_id=plan_task_id,
                    command_id=command_id or f"plan-task:{plan_task_id}",
                    started_at=started_at,
                )
            response = self._session_projection(existing)
            self._repository.commit()
            return response

        question_ids: list[str] = []
        blueprint_payload: list[dict[str, object]] = []
        for module_name, count in GUOKAO_MOCK_BLUEPRINT:
            module_question_ids = self._repository.select_module_question_ids(
                user_id=user_id,
                module_name=module_name,
                limit=count,
            )
            if len(module_question_ids) != count:
                raise MockExamUnavailableError(
                    f"Not enough executable guokao questions for {module_name}: "
                    f"{len(module_question_ids)}/{count}"
                )
            question_ids.extend(module_question_ids)
            blueprint_payload.append(
                {"moduleName": module_name, "questionCount": count}
            )
        bundle, scoring_snapshot = self._freeze_questions(question_ids)
        paper_fingerprint = _hash(
            {
                "bundleVersionId": bundle.bundle_version_id,
                "questionIds": question_ids,
                "blueprint": blueprint_payload,
                "durationMinutes": MOCK_DURATION_MINUTES,
                "scoringPolicyVersionId": MOCK_SCORING_POLICY_VERSION,
            }
        )
        paper_version_id = self._repository.get_or_create_paper_version(
            paper_key=f"weekly-guokao:{paper_fingerprint[:24]}",
            title="国考行测 135 题整卷模考",
            duration_minutes=MOCK_DURATION_MINUTES,
            question_ids=question_ids,
            blueprint=blueprint_payload,
            learner_bundle_version_id=bundle.bundle_version_id,
            scoring_policy_version_id=MOCK_SCORING_POLICY_VERSION,
            exposure_policy={
                "policyVersion": "mock-exposure@v1",
                "lookbackDays": 60,
                "excludeRecentPractice": True,
                "excludeRecentMock": True,
            },
            equivalence_metadata={
                "status": "structural_index",
                "blueprintVersion": "guokao-xingce-135-six-module@v2",
                "moduleCatalogVersion": GUOKAO_XINGCE_CATALOG_VERSION,
                "officialScoreComparable": False,
            },
            content_fingerprint=paper_fingerprint,
            published_at=started_at,
        )
        checkpoint = {
            "currentQuestionId": bundle.questions[0].question_id,
            "answers": [],
        }
        session_id = self._repository.create_session(
            user_id=user_id,
            question_ids=question_ids,
            blueprint=blueprint_payload,
            checkpoint=checkpoint,
            learner_bundle=bundle.model_dump(by_alias=True, mode="json"),
            scoring_snapshot=scoring_snapshot,
            paper_version_id=paper_version_id,
            scoring_policy_version_id=MOCK_SCORING_POLICY_VERSION,
            started_at=started_at,
            deadline_at=started_at + timedelta(minutes=MOCK_DURATION_MINUTES),
        )
        record = self._required_session(user_id=user_id, session_id=session_id)
        if plan_task_id is not None:
            record = bind_and_start_mock_plan_task(
                self._repository.session,
                mock_repository=self._repository,
                user_id=user_id,
                mock_session=record,
                plan_task_id=plan_task_id,
                command_id=command_id or f"plan-task:{plan_task_id}",
                started_at=started_at,
            )
        self._repository.commit()
        return self._session_projection(record)

    def get_session(self, *, user_id: str, session_id: str) -> MockExamSession:
        return self._session_projection(
            self._required_session(user_id=user_id, session_id=session_id)
        )

    def save_checkpoint(
        self,
        *,
        user_id: str,
        session_id: str,
        idempotency_key: str,
        request: MockExamCheckpointRequest,
        now: datetime | None = None,
    ) -> MockExamCheckpointReceipt:
        updated_at = (now or datetime.now(UTC)).astimezone(UTC)
        request_hash = _request_hash("checkpoint", session_id, request)
        self._repository.lock_user(user_id=user_id)
        replay = self._command_replay(
            user_id=user_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            command_type="checkpoint",
            request_hash=request_hash,
        )
        if replay is not None:
            response = MockExamCheckpointReceipt.model_validate(replay)
            self._repository.commit()
            return response

        record = self._required_session(user_id=user_id, session_id=session_id)
        if record.status != "assigned":
            raise MockExamCheckpointError(
                "Only assigned mock exams can be changed", code="mock_exam_not_editable"
            )
        bundle = _bundle_from_record(record)
        questions = {question.question_id: question for question in bundle.questions}
        if request.current_question_id not in questions:
            raise MockExamCheckpointError("Current question is outside assigned bundle")
        merged = {
            str(item["questionId"]): dict(item)
            for item in record.checkpoint.get("answers", [])
        }
        for answer in request.answers:
            question = questions.get(answer.question_id)
            if question is None:
                raise MockExamCheckpointError("Checkpoint question is not assigned")
            valid_options = {option.option_id for option in question.options}
            if (
                answer.selected_answer is not None
                and answer.selected_answer not in valid_options
            ):
                raise MockExamCheckpointError("Selected answer is not a frozen option")
            if not set(answer.eliminated_option_ids).issubset(valid_options):
                raise MockExamCheckpointError(
                    "Eliminated answer is not a frozen option"
                )
            merged[answer.question_id] = answer.model_dump(by_alias=True, mode="json")
        checkpoint = {
            "currentQuestionId": request.current_question_id,
            "answers": [merged[key] for key in sorted(merged)],
        }
        saved = self._repository.save_checkpoint(
            session_id=session_id,
            user_id=user_id,
            expected_revision=request.expected_revision,
            checkpoint=checkpoint,
            updated_at=updated_at,
        )
        if saved is None:
            current = self._required_session(user_id=user_id, session_id=session_id)
            if current.status != "assigned":
                raise MockExamCheckpointError(
                    "Mock exam is no longer editable", code="mock_exam_not_editable"
                )
            if current.deadline_at <= updated_at:
                raise MockExamCheckpointError(
                    "Mock exam checkpoint deadline passed",
                    code="mock_exam_deadline_passed",
                )
            raise MockExamCheckpointError(
                "Mock exam revision changed before checkpoint",
                code="mock_revision_conflict",
            )
        response = _checkpoint_receipt(saved)
        payload = response.model_dump(by_alias=True, mode="json")
        self._repository.record_command(
            user_id=user_id,
            idempotency_key=idempotency_key,
            command_type="checkpoint",
            session_id=session_id,
            request_hash=request_hash,
            response_payload=payload,
            created_at=updated_at,
        )
        self._repository.commit()
        return response

    def submit_mock(
        self,
        *,
        user_id: str,
        session_id: str,
        request: MockExamSubmitRequest,
        idempotency_key: str | None = None,
    ) -> MockExamSubmitResponse:
        if idempotency_key is not None and request.expected_revision is None:
            raise MockExamSubmissionError(
                "Idempotent mock submission requires expectedRevision"
            )
        request_hash = _request_hash("submit", session_id, request)
        self._repository.lock_user(user_id=user_id)
        if idempotency_key is not None:
            replay = self._command_replay(
                user_id=user_id,
                session_id=session_id,
                idempotency_key=idempotency_key,
                command_type="submit",
                request_hash=request_hash,
            )
            if replay is not None:
                response = MockExamSubmitResponse.model_validate(replay)
                self._repository.commit()
                return response

        mock_session = self._required_session(user_id=user_id, session_id=session_id)
        if mock_session.status != "assigned":
            raise MockExamSubmissionError("Only assigned mock exams can be submitted")
        assigned_ids = mock_session.question_ids
        answers = {item.question_id: item.selected_answer for item in request.answers}
        if set(answers) != set(assigned_ids):
            raise MockExamSubmissionError(
                "Mock exam submission must include every assigned question exactly once"
            )
        assigned_questions = _frozen_assigned_questions(mock_session)
        if len(assigned_questions) != len(assigned_ids):
            raise MockExamSubmissionError("Assigned question set is no longer complete")
        by_id = {question.question_id: question for question in assigned_questions}
        for question_id, selected_answer in answers.items():
            if (
                selected_answer is not None
                and selected_answer not in by_id[question_id].valid_option_ids
            ):
                raise MockExamSubmissionError(
                    f"Selected answer is outside option set for {question_id}"
                )

        review = self._build_review(
            user_id=user_id,
            session_id=session_id,
            assigned_questions=assigned_questions,
            answers=answers,
        )
        answer_payload = [
            {"questionId": question_id, "selectedAnswer": answers[question_id]}
            for question_id in assigned_ids
        ]
        expected_revision = request.expected_revision or mock_session.revision
        submitted = self._repository.submit_session(
            session_id=session_id,
            user_id=user_id,
            answers=answer_payload,
            review_payload=review.model_dump(by_alias=True, mode="json"),
            expected_revision=expected_revision,
        )
        if not submitted:
            current = self._required_session(user_id=user_id, session_id=session_id)
            if current.status != "assigned":
                raise MockExamSubmissionError(
                    "Mock exam is no longer editable", code="mock_exam_not_editable"
                )
            raise MockExamSubmissionError(
                "Mock exam revision changed before submit",
                code="mock_submission_conflict",
            )
        submitted_at = datetime.now(UTC)
        self._record_submitted_trajectory(
            user_id=user_id,
            record=mock_session,
            submitted_at=submitted_at,
        )
        committed_session = self._required_session(
            user_id=user_id,
            session_id=session_id,
        )
        complete_mock_plan_task(
            self._repository.session,
            user_id=user_id,
            mock_session=committed_session,
            command_id=(
                idempotency_key
                or f"{session_id}:{expected_revision}"
            ),
            completed_at=submitted_at,
        )
        response = MockExamSubmitResponse(
            session_id=session_id,
            status="submitted",
            review=review,
        )
        if idempotency_key is not None:
            self._repository.record_command(
                user_id=user_id,
                idempotency_key=idempotency_key,
                command_type="submit",
                session_id=session_id,
                request_hash=request_hash,
                response_payload=response.model_dump(by_alias=True, mode="json"),
                created_at=datetime.now(UTC),
            )
        self._repository.commit()
        return response

    def record_trajectory_event(
        self,
        *,
        user_id: str,
        session_id: str,
        command_id: str,
        command: MockExamTrajectoryEventCommand,
        now: datetime | None = None,
    ) -> MockExamTrajectoryEventReceipt:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        request_fingerprint = _hash(
            {
                "sessionId": session_id,
                "event": command.model_dump(by_alias=True, mode="json"),
            }
        )
        self._repository.lock_user(user_id=user_id)
        existing = self._repository.trajectory_event_by_command(
            user_id=user_id, command_id=command_id
        )
        if existing is not None:
            if existing["request_fingerprint"] != request_fingerprint:
                raise MockExamCheckpointError("Trajectory command ID was reused")
            return MockExamTrajectoryEventReceipt(
                trajectoryEventId=str(existing["trajectory_event_id"]),
                sessionId=str(existing["session_id"]),
                eventSeq=int(existing["event_seq"]),
                eventType=str(existing["event_type"]),
                acceptedAt=existing["created_at"],
            )
        record = self._required_session(user_id=user_id, session_id=session_id)
        if record.status != "assigned":
            raise MockExamCheckpointError(
                "Only an assigned mock exam accepts trajectory events"
            )
        if command.event_seq != record.next_trajectory_event_seq:
            raise _trajectory_sequence_conflict(
                expected_event_seq=record.next_trajectory_event_seq,
                received_event_seq=command.event_seq,
            )
        if command.session_revision > record.revision:
            raise MockExamCheckpointError(
                "Trajectory session revision is ahead of server state"
            )
        if command.client_occurred_at.astimezone(UTC) > timestamp:
            raise MockExamCheckpointError("Trajectory event cannot occur in the future")
        if (
            command.question_id is not None
            and command.question_id not in record.question_ids
        ):
            raise MockExamCheckpointError(
                "Trajectory question is outside assigned paper"
            )
        trajectory_event_id = str(uuid4())
        try:
            self._repository.record_trajectory_event(
                trajectory_event_id=trajectory_event_id,
                command_id=command_id,
                user_id=user_id,
                session_id=session_id,
                event_seq=command.event_seq,
                event_type=command.event_type,
                question_id=command.question_id,
                client_occurred_at=command.client_occurred_at.astimezone(UTC),
                session_revision=command.session_revision,
                payload=command.payload,
                request_fingerprint=request_fingerprint,
                created_at=timestamp,
            )
        except MockExamTrajectoryWriteConflict as conflict:
            if (
                conflict.constraint_name
                == "mock_exam_trajectory_user_command_key"
            ):
                replay = self._repository.trajectory_event_by_command(
                    user_id=user_id,
                    command_id=command_id,
                )
                if (
                    replay is not None
                    and replay["request_fingerprint"] == request_fingerprint
                ):
                    return MockExamTrajectoryEventReceipt(
                        trajectoryEventId=str(replay["trajectory_event_id"]),
                        sessionId=str(replay["session_id"]),
                        eventSeq=int(replay["event_seq"]),
                        eventType=str(replay["event_type"]),
                        acceptedAt=replay["created_at"],
                    )
                raise MockExamCheckpointError(
                    "这次轨迹写入已被其他请求使用，请同步后继续。",
                    code="mock_trajectory_command_conflict",
                ) from conflict
            expected_event_seq = self._repository.next_trajectory_event_seq(
                session_id=session_id
            )
            raise _trajectory_sequence_conflict(
                expected_event_seq=expected_event_seq,
                received_event_seq=command.event_seq,
            ) from conflict
        self._repository.commit()
        return MockExamTrajectoryEventReceipt(
            trajectoryEventId=trajectory_event_id,
            sessionId=session_id,
            eventSeq=command.event_seq,
            eventType=command.event_type,
            acceptedAt=timestamp,
        )

    def _record_submitted_trajectory(
        self,
        *,
        user_id: str,
        record: MockExamSessionRecord,
        submitted_at: datetime,
    ) -> None:
        command_id = f"mock-submit:{record.session_id}:{record.revision}"
        if (
            self._repository.trajectory_event_by_command(
                user_id=user_id, command_id=command_id
            )
            is not None
        ):
            return
        payload = {
            "paperVersionId": record.paper_version_id,
            "scoringPolicyVersionId": record.scoring_policy_version_id,
            "answerCount": len(record.question_ids),
        }
        self._repository.record_trajectory_event(
            trajectory_event_id=str(uuid4()),
            command_id=command_id,
            user_id=user_id,
            session_id=record.session_id,
            event_seq=self._repository.next_trajectory_event_seq(
                session_id=record.session_id
            ),
            event_type="submitted",
            question_id=None,
            client_occurred_at=submitted_at,
            session_revision=record.revision + 1,
            payload=payload,
            request_fingerprint=_hash(payload),
            created_at=submitted_at,
        )

    def report_question_dispute(
        self,
        *,
        user_id: str,
        session_id: str,
        idempotency_key: str,
        command: ReportQuestionDisputeCommand,
        now: datetime | None = None,
    ) -> QuestionDisputeReceipt:
        self._repository.lock_user(user_id=user_id)
        existing = self._repository.question_dispute_by_idempotency_key(
            user_id=user_id, idempotency_key=idempotency_key
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
                raise MockExamDisputeError("Idempotency key was reused")
            response = _dispute_receipt(existing)
            self._repository.commit()
            return response
        record = self._required_session(user_id=user_id, session_id=session_id)
        bundle = _bundle_from_record(record)
        question = next(
            (
                item
                for item in bundle.questions
                if item.question_id == command.question_id
            ),
            None,
        )
        if (
            question is None
            or question.content_version_id != command.content_version_id
        ):
            raise MockExamDisputeError("Dispute content version is not assigned")
        answer_state = next(
            (
                dict(item)
                for item in record.checkpoint.get("answers", [])
                if item.get("questionId") == command.question_id
            ),
            None,
        )
        row = self._repository.insert_question_dispute(
            values={
                "dispute_id": str(uuid4()),
                "user_id": user_id,
                "session_id": session_id,
                "question_id": command.question_id,
                "content_version_id": command.content_version_id,
                "category": command.category,
                "description": command.description,
                "evidence_context": _json(
                    {
                        "sessionRevision": record.revision,
                        "bundleVersionId": bundle.bundle_version_id,
                        "currentQuestionId": record.checkpoint["currentQuestionId"],
                        "questionStateRef": (
                            f"sha256:{_hash(answer_state)}"
                            if answer_state is not None
                            else None
                        ),
                        "materialPosition": (
                            answer_state.get("materialPosition")
                            if answer_state is not None
                            else None
                        ),
                    }
                ),
                "idempotency_key": idempotency_key,
                "created_at": (now or datetime.now(UTC)).astimezone(UTC),
            }
        )
        self._repository.commit()
        return _dispute_receipt(row)

    def get_review(self, *, user_id: str, session_id: str) -> MockExamReview:
        session = self._repository.get_session(session_id=session_id, user_id=user_id)
        if session is None or session.review_payload is None:
            raise MockExamNotFoundError(session_id)
        return MockExamReview(**session.review_payload)

    def mark_review_viewed(self, *, user_id: str, session_id: str) -> MockExamReview:
        session = self._repository.mark_review_viewed(
            session_id=session_id, user_id=user_id
        )
        if session is None or session.review_payload is None:
            raise MockExamNotFoundError(session_id)
        payload = dict(session.review_payload)
        payload["viewed"] = True
        self._repository.commit()
        return MockExamReview(**payload)

    def _freeze_questions(
        self, question_ids: list[str]
    ) -> tuple[SafeQuestionBundle, dict[str, Any]]:
        sources = self._repository.get_frozen_question_sources(
            question_ids=question_ids
        )
        if len(sources) != len(question_ids):
            raise MockExamUnavailableError("Mock exam source set is incomplete")
        try:
            bundle, scoring_snapshot = freeze_learner_bundle(
                sources,
                scoring_policy_version=MOCK_SCORING_POLICY_VERSION,
                content_version_id=question_content_version_id,
                published_explanation=lambda source: (None, None),
            )
        except TaskContentContractError as exc:
            raise MockExamUnavailableError(exc.message) from exc
        _validate_single_choice_snapshot(scoring_snapshot)
        return bundle, scoring_snapshot

    def _session_projection(self, record: MockExamSessionRecord) -> MockExamSession:
        bundle = _bundle_from_record(record)
        checkpoint = MockExamCheckpoint.model_validate(record.checkpoint)
        return MockExamSession(
            sessionId=record.session_id,
            planTaskId=record.source_plan_task_id,
            status=record.status,
            title="国考行测 135 题整卷模考",
            durationMinutes=MOCK_DURATION_MINUTES,
            questionCount=len(record.question_ids),
            blueprint=[MockExamModuleBlueprint(**item) for item in record.blueprint],
            revision=record.revision,
            startedAt=record.started_at,
            deadlineAt=record.deadline_at,
            checkpoint=checkpoint,
            bundleVersionId=bundle.bundle_version_id,
            paperVersionId=record.paper_version_id,
            scoringPolicyVersionId=record.scoring_policy_version_id,
            scoreMetricName=record.score_metric_name,
            scoreMetricUnit=record.score_metric_unit,
            scoreMetricDefinition=record.score_metric_definition,
            nextTrajectoryEventSeq=record.next_trajectory_event_seq,
            materials=bundle.materials,
            questions=bundle.questions,
            sourceGrounding=(
                "product.questions + product.knowledge_map_nodes + "
                "learner.mock_exam_sessions"
            ),
        )

    def _required_session(
        self, *, user_id: str, session_id: str
    ) -> MockExamSessionRecord:
        record = self._repository.get_session(session_id=session_id, user_id=user_id)
        if record is None:
            raise MockExamNotFoundError(session_id)
        return record

    def _command_replay(
        self,
        *,
        user_id: str,
        session_id: str,
        idempotency_key: str,
        command_type: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        existing = self._repository.get_command(
            user_id=user_id, idempotency_key=idempotency_key
        )
        if existing is None:
            return None
        if (
            existing["command_type"] != command_type
            or str(existing["session_id"]) != session_id
            or existing["request_hash"] != request_hash
        ):
            raise MockExamCheckpointError("Idempotency key was reused")
        return dict(existing["response_payload"])

    def _build_review(
        self,
        *,
        user_id: str,
        session_id: str,
        assigned_questions: list[AssignedQuestionRecord],
        answers: dict[str, str | None],
    ) -> MockExamReview:
        record = self._repository.get_session(session_id=session_id, user_id=user_id)
        if record is None:
            raise MockExamSubmissionError("Mock paper version disappeared")
        published_modules = _published_module_names(record.blueprint)
        by_module: dict[str, dict[str, int]] = {}
        wrong_question_ids: list[str] = []
        correct_count = 0
        for question in assigned_questions:
            module = by_module.setdefault(
                question.module_name, {"correct": 0, "total": 0}
            )
            module["total"] += 1
            correct = (
                answers[question.question_id] is not None
                and answers[question.question_id] in question.correct_option_ids
            )
            if correct:
                correct_count += 1
                module["correct"] += 1
            else:
                wrong_question_ids.append(question.question_id)

        previous = self._repository.get_previous_module_accuracy(
            user_id=user_id, before_session_id=session_id
        )
        modules = []
        for module_name in published_modules:
            values = by_module.get(module_name, {"correct": 0, "total": 0})
            accuracy = (
                round(values["correct"] / values["total"], 4)
                if values["total"]
                else 0.0
            )
            previous_accuracy = previous.get(module_name)
            modules.append(
                MockExamModuleReview(
                    module_name=module_name,
                    correct=values["correct"],
                    total=values["total"],
                    accuracy=accuracy,
                    change_from_previous=(
                        round(accuracy - previous_accuracy, 4)
                        if previous_accuracy is not None
                        else None
                    ),
                )
            )
        performance_index, performance_range = _performance_index(
            modules,
            module_weights=record.score_module_weights,
        )
        wrong_clusters = [
            MockExamWrongCluster(
                node_id=str(row["node_id"]),
                display_path=str(row["display_path"]),
                wrong_count=int(row["wrong_count"]),
                method_card_ids=list(row["method_card_ids"] or []),
                fix_action=row["fix_action"] or "先回看该知识点方法卡，再做同类题。",
            )
            for row in self._repository.get_wrong_clusters(
                wrong_question_ids=wrong_question_ids
            )
        ]
        return MockExamReview(
            session_id=session_id,
            estimated_score=performance_index,
            estimate_source=_estimate_source(record.score_metric_name),
            performanceIndex=performance_index,
            performanceRange=performance_range,
            metricName=record.score_metric_name,
            metricUnit=record.score_metric_unit,
            metricDefinition=record.score_metric_definition,
            intervalDefinition=(
                "各模块使用 95% Wilson 二项区间后按已发布策略权重合成；"
                "只表达本卷答题样本不确定性，不是官方成绩区间。"
            ),
            paperVersionId=record.paper_version_id,
            scoringPolicyVersionId=record.scoring_policy_version_id,
            submitted_at=datetime.now(UTC).isoformat(),
            viewed=False,
            modules=modules,
            wrong_clusters=wrong_clusters,
            next_week_reason=_next_week_reason(modules, wrong_clusters),
        )


def _bundle_from_record(record: MockExamSessionRecord) -> SafeQuestionBundle:
    payload = record.learner_bundle
    if (
        not isinstance(payload, dict)
        or not payload
        or not {"bundleVersionId", "materials", "questions"}.issubset(payload)
    ):
        raise MockExamUnavailableError(
            "Assigned session has no immutable learner-safe bundle"
        )
    try:
        bundle = SafeQuestionBundle.model_validate(payload)
    except ValidationError as exc:
        raise MockExamUnavailableError(
            "Assigned session learner bundle is malformed"
        ) from exc
    if [question.question_id for question in bundle.questions] != record.question_ids:
        raise MockExamUnavailableError(
            "Frozen bundle does not match assigned question set"
        )
    scoring = record.scoring_snapshot
    if (
        not scoring
        or scoring.get("bundleVersionId") != bundle.bundle_version_id
        or scoring.get("scoringPolicyVersion") != record.scoring_policy_version_id
        or not isinstance(scoring.get("questions"), list)
    ):
        raise MockExamUnavailableError(
            "Assigned session has no matching immutable scoring snapshot"
        )
    return bundle


def _performance_index(
    modules: list[MockExamModuleReview],
    *,
    module_weights: dict[str, float],
) -> tuple[float, MockExamPerformanceRange]:
    module_names = {item.module_name for item in modules}
    if module_names != set(module_weights):
        raise MockExamSubmissionError(
            "Performance index modules do not match the published scoring policy"
        )
    if (
        any(weight <= 0 for weight in module_weights.values())
        or abs(sum(module_weights.values()) - 1.0) > 1e-6
    ):
        raise MockExamSubmissionError("Published scoring policy weights are invalid")
    recommended = (
        sum(item.accuracy * module_weights[item.module_name] for item in modules) * 100
    )
    weighted_bounds = [
        (
            _wilson_interval(item.correct, item.total),
            module_weights[item.module_name],
        )
        for item in modules
    ]
    lower = sum(bound[0] * weight for bound, weight in weighted_bounds) * 100
    upper = sum(bound[1] * weight for bound, weight in weighted_bounds) * 100
    score = round(recommended, 1)
    return score, MockExamPerformanceRange(
        lower=round(lower, 1), recommended=score, upper=round(upper, 1)
    )


def _published_module_names(blueprint: list[dict[str, Any]]) -> tuple[str, ...]:
    names: list[str] = []
    for item in blueprint:
        module_name = item.get("moduleName")
        question_count = item.get("questionCount")
        if (
            not isinstance(module_name, str)
            or not module_name.strip()
            or not isinstance(question_count, int)
            or question_count <= 0
        ):
            raise MockExamSubmissionError("Published mock blueprint is invalid")
        names.append(module_name.strip())
    if not names or len(names) != len(set(names)):
        raise MockExamSubmissionError("Published mock blueprint modules are invalid")
    return tuple(names)


def _estimate_source(metric_name: str) -> str:
    if metric_name == "balanced_performance_index":
        return "兼容字段：值为历史五模块等权表现指数，不是国考原始分，不使用全国分位。"
    if metric_name == "question_weighted_performance_index":
        return (
            "兼容字段：值为当前六模块按标准卷题量加权表现指数，"
            "不是国考原始分，不使用全国分位。"
        )
    raise MockExamSubmissionError("Published scoring metric is unsupported")


def _wilson_interval(correct: int, total: int) -> tuple[float, float]:
    if total <= 0:
        raise MockExamSubmissionError("Module interval requires at least one question")
    z = 1.959963984540054
    proportion = correct / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _checkpoint_receipt(record: MockExamSessionRecord) -> MockExamCheckpointReceipt:
    return MockExamCheckpointReceipt(
        sessionId=record.session_id,
        status="assigned",
        revision=record.revision,
        checkpoint=MockExamCheckpoint.model_validate(record.checkpoint),
        checkpointUpdatedAt=record.checkpoint_updated_at,
    )


def _validate_single_choice_snapshot(scoring_snapshot: dict[str, Any]) -> None:
    questions = scoring_snapshot.get("questions")
    if not isinstance(questions, list) or any(
        not isinstance(item, dict) or len(item.get("correctOptionIds", [])) != 1
        for item in questions
    ):
        raise MockExamUnavailableError(
            "Mock exam single-choice runtime requires exactly one correct option"
        )


def _frozen_assigned_questions(
    record: MockExamSessionRecord,
) -> list[AssignedQuestionRecord]:
    bundle = _bundle_from_record(record)
    safe_by_id = {question.question_id: question for question in bundle.questions}
    scoring_items = record.scoring_snapshot["questions"]
    if not all(isinstance(item, dict) for item in scoring_items):
        raise MockExamSubmissionError("Frozen scoring snapshot is malformed")
    scoring_by_id = {str(item.get("questionId")): item for item in scoring_items}
    if set(scoring_by_id) != set(record.question_ids):
        raise MockExamSubmissionError("Frozen scoring snapshot question set changed")
    result: list[AssignedQuestionRecord] = []
    for question_id in record.question_ids:
        question = safe_by_id[question_id]
        scoring = scoring_by_id[question_id]
        correct_option_ids = frozenset(
            str(item).strip().upper()
            for item in scoring.get("correctOptionIds", [])
            if str(item).strip()
        )
        valid_options = frozenset(option.option_id for option in question.options)
        if (
            scoring.get("contentVersionId") != question.content_version_id
            or not correct_option_ids
            or not correct_option_ids.issubset(valid_options)
        ):
            raise MockExamSubmissionError(
                "Frozen scoring snapshot does not match learner content version"
            )
        result.append(
            AssignedQuestionRecord(
                question_id=question_id,
                module_name=question.module_name,
                correct_option_ids=correct_option_ids,
                valid_option_ids=valid_options,
            )
        )
    return result


def _dispute_receipt(row: dict[str, Any]) -> QuestionDisputeReceipt:
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


def _request_hash(command_type: str, session_id: str, request: Any) -> str:
    return _hash(
        {
            "commandType": command_type,
            "sessionId": session_id,
            "request": request.model_dump(by_alias=True, mode="json"),
        }
    )


def _hash(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _next_week_reason(
    modules: list[MockExamModuleReview],
    wrong_clusters: list[MockExamWrongCluster],
) -> str:
    weakest = min(modules, key=lambda item: (item.accuracy, item.module_name))
    if wrong_clusters:
        cluster = wrong_clusters[0]
        return (
            f"上周模考{weakest.module_name} {weakest.accuracy * 100:.1f}%，"
            f"本周加练{cluster.display_path}。"
        )
    return (
        f"上周模考{weakest.module_name} {weakest.accuracy * 100:.1f}%，"
        "本周先补最低模块。"
    )
