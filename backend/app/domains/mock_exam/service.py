from __future__ import annotations

from datetime import UTC, datetime

from app.domains.knowledge.service import KnowledgeService
from app.domains.mock_exam.repository import AssignedQuestionRecord, MockExamRepository
from app.domains.progression.service import ProgressionService
from app.domains.mock_exam.schemas import (
    MockExamModuleBlueprint,
    MockExamModuleReview,
    MockExamReview,
    MockExamSession,
    MockExamSubmitRequest,
    MockExamSubmitResponse,
    MockExamWrongCluster,
)

WEEKLY_MOCK_TASK_ID = "weekly-guokao-mock-exam"
MOCK_DURATION_MINUTES = 120
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


class MockExamService:
    def __init__(
        self,
        *,
        repository: MockExamRepository,
        knowledge_service: KnowledgeService,
        progression_service: ProgressionService,
    ) -> None:
        self._repository = repository
        self._knowledge_service = knowledge_service
        self._progression_service = progression_service

    def create_weekly_mock(self, *, user_id: str) -> MockExamSession:
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
                    f"Not enough guokao mock questions for {module_name}: "
                    f"{len(module_question_ids)}/{count}"
                )
            question_ids.extend(module_question_ids)
            blueprint_payload.append(
                {"moduleName": module_name, "questionCount": count}
            )
        session_id = self._repository.create_session(
            user_id=user_id,
            question_ids=question_ids,
            blueprint=blueprint_payload,
        )
        questions = [self._knowledge_service.get_question(question_id) for question_id in question_ids]
        return MockExamSession(
            session_id=session_id,
            status="assigned",
            title="国考行测 135 题整卷模考",
            duration_minutes=MOCK_DURATION_MINUTES,
            question_count=len(question_ids),
            blueprint=[
                MockExamModuleBlueprint(**item)
                for item in blueprint_payload
            ],
            questions=questions,
            source_grounding="product.questions + product.knowledge_map_nodes + learner.mock_exam_sessions",
        )

    def submit_mock(
        self,
        *,
        user_id: str,
        session_id: str,
        request: MockExamSubmitRequest,
    ) -> MockExamSubmitResponse:
        mock_session = self._repository.get_session(session_id=session_id, user_id=user_id)
        if mock_session is None:
            raise MockExamNotFoundError(session_id)
        if mock_session.status != "assigned":
            raise MockExamSubmissionError("Only assigned mock exams can be submitted")

        assigned_ids = mock_session.question_ids
        answers = {
            item.question_id: item.selected_answer.strip().upper()
            for item in request.answers
        }
        if set(answers) != set(assigned_ids):
            raise MockExamSubmissionError("Mock exam submission must answer every assigned question")

        assigned_questions = self._repository.get_assigned_questions(question_ids=assigned_ids)
        if len(assigned_questions) != len(assigned_ids):
            raise MockExamSubmissionError("Assigned question set is no longer complete")

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
        self._repository.submit_session(
            session_id=session_id,
            user_id=user_id,
            answers=answer_payload,
            review_payload=review.model_dump(by_alias=True),
        )
        self._progression_service.record_mock_exam(
            user_id=user_id,
            estimated_score=review.estimated_score,
        )
        return MockExamSubmitResponse(
            session_id=session_id,
            status="submitted",
            review=review,
        )

    def get_review(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> MockExamReview:
        session = self._repository.get_session(session_id=session_id, user_id=user_id)
        if session is None or session.review_payload is None:
            raise MockExamNotFoundError(session_id)
        return MockExamReview(**session.review_payload)

    def mark_review_viewed(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> MockExamReview:
        session = self._repository.mark_review_viewed(session_id=session_id, user_id=user_id)
        if session is None or session.review_payload is None:
            raise MockExamNotFoundError(session_id)
        payload = dict(session.review_payload)
        payload["viewed"] = True
        return MockExamReview(**payload)

    def _build_review(
        self,
        *,
        user_id: str,
        session_id: str,
        assigned_questions: list[AssignedQuestionRecord],
        answers: dict[str, str],
    ) -> MockExamReview:
        by_module: dict[str, dict[str, int]] = {}
        wrong_question_ids: list[str] = []
        correct_count = 0
        for question in assigned_questions:
            module = by_module.setdefault(question.module_name, {"correct": 0, "total": 0})
            module["total"] += 1
            correct_answer = question.correct_answer.strip().upper() if question.correct_answer else None
            correct = correct_answer is not None and answers[question.question_id] == correct_answer
            if correct:
                correct_count += 1
                module["correct"] += 1
            else:
                wrong_question_ids.append(question.question_id)

        previous = self._repository.get_previous_module_accuracy(
            user_id=user_id,
            before_session_id=session_id,
        )
        modules = []
        for module_name, _ in GUOKAO_MOCK_BLUEPRINT:
            values = by_module.get(module_name, {"correct": 0, "total": 0})
            accuracy = round(values["correct"] / values["total"], 4) if values["total"] else 0.0
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
                fix_action=row["fix_action"] or "先回看该知识点方法卡，再做同类题。",
            )
            for row in self._repository.get_wrong_clusters(
                wrong_question_ids=wrong_question_ids,
            )
        ]
        next_week_reason = _next_week_reason(modules, wrong_clusters)
        return MockExamReview(
            session_id=session_id,
            estimated_score=estimated_score,
            estimate_source="按本次 135 题正确率折算，未使用全国分位。",
            submitted_at=datetime.now(UTC).isoformat(),
            viewed=False,
            modules=modules,
            wrong_clusters=wrong_clusters,
            next_week_reason=next_week_reason,
        )


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
    return f"上周模考{weakest.module_name} {weakest.accuracy * 100:.1f}%，本周先补最低模块。"
