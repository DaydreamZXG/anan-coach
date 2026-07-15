from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.domains.knowledge.service import KnowledgeService
from app.domains.mock_exam.repository import (
    AssignedQuestionRecord,
    MockExamRepository,
    MockExamSessionRecord,
)
from app.domains.mock_exam.schemas import (
    MockExamCheckpoint,
    MockExamCheckpointReceipt,
    MockExamCheckpointRequest,
    MockExamModuleBlueprint,
    MockExamModuleReview,
    MockExamReview,
    MockExamSession,
    MockExamSubmitRequest,
    MockExamSubmitResponse,
    MockExamWrongCluster,
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
MOCK_SCORING_POLICY_VERSION = "mock-objective-exact-v1"
GUOKAO_MOCK_BLUEPRINT: list[tuple[str, int]] = [
    ("常识判断", 20),
    ("言语理解与表达", 40),
    ("数量关系", 15),
    ("判断推理", 40),
    ("资料分析", 20),
]


class MockExamUnavailableError(RuntimeError):
    pass


class MockExamNotFoundError(LookupError):
    pass


class MockExamSubmissionError(RuntimeError):
    pass


class MockExamCheckpointError(RuntimeError):
    pass


class MockExamDisputeError(RuntimeError):
    pass


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

    def create_weekly_mock(self, *, user_id: str) -> MockExamSession:
        self._repository.lock_user(user_id=user_id)
        existing = self._repository.get_assigned_session(user_id=user_id)
        if existing is not None:
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
        started_at = datetime.now(UTC)
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
            started_at=started_at,
            deadline_at=started_at + timedelta(minutes=MOCK_DURATION_MINUTES),
        )
        record = self._required_session(user_id=user_id, session_id=session_id)
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
            raise MockExamCheckpointError("Only assigned mock exams can be changed")
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
            if answer.selected_answer is not None and answer.selected_answer not in valid_options:
                raise MockExamCheckpointError("Selected answer is not a frozen option")
            if not set(answer.eliminated_option_ids).issubset(valid_options):
                raise MockExamCheckpointError("Eliminated answer is not a frozen option")
            merged[answer.question_id] = answer.model_dump(
                by_alias=True, mode="json"
            )
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
            raise MockExamCheckpointError(
                "Checkpoint revision changed, session ended, or deadline passed"
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
        answers = {
            item.question_id: item.selected_answer for item in request.answers
        }
        if set(answers) != set(assigned_ids):
            raise MockExamSubmissionError(
                "Mock exam submission must include every assigned question exactly once"
            )
        assigned_questions = _frozen_assigned_questions(mock_session)
        if len(assigned_questions) != len(assigned_ids):
            raise MockExamSubmissionError("Assigned question set is no longer complete")
        by_id = {question.question_id: question for question in assigned_questions}
        for question_id, selected_answer in answers.items():
            if selected_answer is not None and selected_answer not in by_id[
                question_id
            ].valid_option_ids:
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
            raise MockExamSubmissionError("Mock exam revision changed before submit")
        self._progression_service.record_mock_exam(
            user_id=user_id,
            estimated_score=review.estimated_score,
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
        if question is None or question.content_version_id != command.content_version_id:
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
        session = self._repository.get_session(
            session_id=session_id, user_id=user_id
        )
        if session is None or session.review_payload is None:
            raise MockExamNotFoundError(session_id)
        return MockExamReview(**session.review_payload)

    def mark_review_viewed(
        self, *, user_id: str, session_id: str
    ) -> MockExamReview:
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
        record = self._repository.get_session(
            session_id=session_id, user_id=user_id
        )
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
        for module_name, _ in GUOKAO_MOCK_BLUEPRINT:
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
        estimated_score = round(correct_count / len(assigned_questions) * 100, 1)
        wrong_clusters = [
            MockExamWrongCluster(
                node_id=str(row["node_id"]),
                display_path=str(row["display_path"]),
                wrong_count=int(row["wrong_count"]),
                method_card_ids=list(row["method_card_ids"] or []),
                fix_action=row["fix_action"]
                or "先回看该知识点方法卡，再做同类题。",
            )
            for row in self._repository.get_wrong_clusters(
                wrong_question_ids=wrong_question_ids
            )
        ]
        return MockExamReview(
            session_id=session_id,
            estimated_score=estimated_score,
            estimate_source="按本次 135 题正确率折算，未使用全国分位。",
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
        raise MockExamUnavailableError("Frozen bundle does not match assigned question set")
    scoring = record.scoring_snapshot
    if (
        not scoring
        or scoring.get("bundleVersionId") != bundle.bundle_version_id
        or scoring.get("scoringPolicyVersion") != MOCK_SCORING_POLICY_VERSION
        or not isinstance(scoring.get("questions"), list)
    ):
        raise MockExamUnavailableError(
            "Assigned session has no matching immutable scoring snapshot"
        )
    return bundle


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
        not isinstance(item, dict)
        or len(item.get("correctOptionIds", [])) != 1
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
