from __future__ import annotations

from datetime import date
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.domains.auth.deps import get_auth_repository
from app.domains.auth.repository import AuthenticatedUser
from app.domains.calibration.schemas import AbilityEstimate, CalibrationSources
from app.domains.feed.schemas import (
    CalibrationNotePayload,
    CoachBriefPayload,
    CompanionPromptPayload,
    ExternalCaptureNudgePayload,
    FallbackPayload,
    GoalGapPayload,
    MockExamDuePayload,
    MockExamSessionSpec,
    MockReviewPendingPayload,
    QuotaNoticePayload,
    StreakRestartPayload,
    TaskEssayPayload,
    TaskMethodCardPayload,
    TaskPracticePayload,
    TodayFeed,
    WrongClusterPayload,
)
from app.domains.knowledge.schemas import QuestionSearchResponse, QuestionSummary
from app.domains.knowledge.service import WeightedPracticeSelection
from app.domains.memory.deps import get_memory_service
from app.domains.memory.repository import LearnerProfileRecord, LearnerWorkbenchObjectRecord
from app.domains.taskstream.router import get_taskstream_service
from app.domains.taskstream.service import TaskstreamService
from app.main import create_app


class FakeAuthRepository:
    def get_user_by_access_token(self, access_token: str) -> AuthenticatedUser | None:
        return AuthenticatedUser(
            user_id="user-feed",
            learner_key="user-feed",
            display_name=None,
            email=None,
        )


class FakeMemoryService:
    def __init__(
        self,
        *,
        profile: LearnerProfileRecord | None = None,
        completed_task_ids: list[str] | None = None,
        mock_created_this_week: bool = True,
        unviewed_mock: LearnerWorkbenchObjectRecord | None = None,
    ) -> None:
        self.profile = profile or LearnerProfileRecord(
            target_exam="国考",
            target_region="全国",
            position_group="中央机关综合管理岗",
            daily_minutes=120,
            study_mode="parttime",
            target_score=72.5,
            baseline_source="self_report",
            baseline_xingce_score=64.0,
            onboarding_complete=True,
        )
        self.completed_task_ids = completed_task_ids or []
        self.mock_created_this_week = mock_created_this_week
        self.unviewed_mock = unviewed_mock

    def get_required_profile(self, *, user_id: str) -> LearnerProfileRecord:
        return self.profile

    def list_completed_task_ids_for_date(
        self,
        *,
        user_id: str,
        completed_on: date,
    ) -> list[str]:
        return self.completed_task_ids

    def get_unviewed_mock_review(
        self,
        *,
        user_id: str,
    ) -> LearnerWorkbenchObjectRecord | None:
        return self.unviewed_mock

    def get_latest_mock_review_reason(self, *, user_id: str) -> str | None:
        return "上周模考资料分析 80.0%，本周保持文字资料。"

    def has_mock_exam_this_week(self, *, user_id: str, week_start: date) -> bool:
        return self.mock_created_this_week


class FakeCalibrationService:
    def __init__(self, *, xingce_score: float | None = 66.0) -> None:
        self.xingce_score = xingce_score

    def get_ability_estimate(self, *, user_id: str) -> AbilityEstimate:
        return AbilityEstimate(
            xingce_score=self.xingce_score,
            confidence="high" if self.xingce_score is not None else "unavailable",
            status_label="高置信校准中" if self.xingce_score is not None else "等待校准",
            data_line="基于 1 次摸底/自报 + 0 题练习 + 1 次模考"
            if self.xingce_score is not None
            else "暂无可用校准数据",
            sources=CalibrationSources(
                baseline=1 if self.xingce_score is not None else 0,
                practice_answers=0,
                mock_exams=1 if self.xingce_score is not None else 0,
            ),
            module_estimates=[],
            latest_mock_reason=None,
        )


class FakeKnowledgeService:
    def select_weighted_xingce_practice(
        self,
        *,
        exam_scope: str,
        limit: int,
        module_name: str | None = None,
    ) -> WeightedPracticeSelection:
        items = self.search_questions(limit=limit).items
        return WeightedPracticeSelection(
            node_id="km_fixture",
            exam_scope=exam_scope,
            track="xingce",
            module_name="资料分析",
            node_type="knowledge_point",
            tag_type="knowledge_point",
            tag_value="文字资料",
            display_path="行测/资料分析/文字资料",
            window_start_year=2022,
            window_end_year=2026,
            occurrence_count=66,
            weight=0.12,
            items=items,
        )

    def search_questions(self, **kwargs: object) -> QuestionSearchResponse:
        limit = int(kwargs["limit"])
        return QuestionSearchResponse(
            limit=limit,
            items=[
                QuestionSummary(
                    question_id=f"feed_q_{index}",
                    domain="objective",
                    module_name="资料分析",
                    question_type="资料分析",
                    stem="2025年某地区完成投资额同比增长。",
                    answer="B",
                    visual_required=False,
                    has_explanation=True,
                    occurrence_count=11,
                    latest_year=2026,
                )
                for index in range(1, limit + 1)
            ],
        )


def make_client(
    *,
    memory: FakeMemoryService | None = None,
    calibration: FakeCalibrationService | None = None,
) -> TestClient:
    app = create_app()
    memory_service = memory or FakeMemoryService()
    taskstream_service = TaskstreamService(  # type: ignore[arg-type]
        knowledge_service=FakeKnowledgeService(),
        memory_service=memory_service,
        calibration_service=calibration or FakeCalibrationService(),
    )
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[get_memory_service] = lambda: memory_service
    app.dependency_overrides[get_taskstream_service] = lambda: taskstream_service
    return TestClient(app)


def test_all_v1_payload_schemas_validate() -> None:
    payload_cases: list[tuple[type[BaseModel], dict[str, object]]] = [
        (
            GoalGapPayload,
            {
                "targetScore": 72.5,
                "estimatedScore": 66.0,
                "daysLeft": None,
                "positionName": "中央机关综合管理岗",
            },
        ),
        (CoachBriefPayload, {"text": "今天先完成旧 planner 给出的任务。", "planBasis": "rules_v1"}),
        (
            TaskPracticePayload,
            {
                "taskId": "practice-1",
                "title": "国考资料分析 3 题",
                "subtitle": "练文字资料",
                "moduleName": "资料分析",
                "questionCount": 3,
                "estMinutes": 24,
                "startUrl": "/api/v1/practice/tasks/practice-1",
                "completed": False,
            },
        ),
        (
            TaskEssayPayload,
            {
                "taskId": "essay-1",
                "title": "申论概括题重写",
                "subtitle": "压缩答案",
                "questionType": "概括题",
                "estMinutes": 25,
                "startUrl": "/api/v1/essay/tasks/essay-1",
                "completed": False,
            },
        ),
        (
            TaskMethodCardPayload,
            {
                "taskId": "method-1",
                "cardId": "mc-data-growth",
                "cardTitle": "增长率读题法",
                "estMinutes": 12,
                "startUrl": "/api/v1/method-cards/mc-data-growth",
                "completed": False,
            },
        ),
        (
            MockExamDuePayload,
            {
                "sessionSpec": {
                    "taskId": "weekly-guokao-mock-exam",
                    "title": "国考行测 135 题整卷模考",
                    "startUrl": "/api/v1/mock-exams/weekly",
                    "questionCount": 135,
                },
                "estMinutes": 120,
                "completed": False,
            },
        ),
        (
            MockReviewPendingPayload,
            {
                "taskId": "weekly-guokao-mock-exam-review",
                "sessionId": "mock-session-1",
                "estimatedScore": 68.2,
                "reviewUrl": "/api/v1/mock-exams/mock-session-1/review",
                "completed": False,
            },
        ),
        (
            WrongClusterPayload,
            {"nodeId": "km-1", "displayPath": "行测/资料分析/增长率", "wrongCount": 4},
        ),
        (StreakRestartPayload, {"daysIdle": 3, "miniTaskId": "practice-1"}),
        (
            CalibrationNotePayload,
            {"statusLabel": "高置信校准中", "dataLine": "基于 1 次摸底 + 1 次模考", "delta": None},
        ),
        (QuotaNoticePayload, {"remaining": 12, "resetAt": "2026-07-05T00:00:00+08:00"}),
        (
            CompanionPromptPayload,
            {"suggestedQuestion": "这道资料分析我错在哪里？", "contextObjectId": "task-1"},
        ),
        (ExternalCaptureNudgePayload, {"text": "把外部错题拍进来，我帮你归到知识点。"}),
        (FallbackPayload, {"title": "暂不支持的卡片", "body": "请升级客户端后查看。", "deeplink": None}),
    ]

    for payload_type, payload in payload_cases:
        assert payload_type.model_validate(payload).model_dump(by_alias=True)

    assert MockExamSessionSpec.model_validate(
        {
            "taskId": "weekly-guokao-mock-exam",
            "title": "国考行测 135 题整卷模考",
            "startUrl": "/api/v1/mock-exams/weekly",
            "questionCount": 135,
        }
    )


def test_feed_today_preserves_taskstream_task_collection() -> None:
    client = make_client()

    today = client.get(
        "/api/v1/taskstream/today",
        headers={"Authorization": "Bearer user-feed"},
    )
    feed = client.get(
        "/api/v1/feed/today",
        headers={"Authorization": "Bearer user-feed"},
    )

    assert today.status_code == 200
    assert feed.status_code == 200
    body = feed.json()
    TodayFeed.model_validate(body)
    assert body["planBasis"] == "rules_v1"
    assert [card["priority"] for card in body["cards"]] == sorted(
        [card["priority"] for card in body["cards"]],
        reverse=True,
    )
    today_tasks = today.json()["tasks"]
    assert "fallback" not in [card["type"] for card in body["cards"]]
    assert _feed_task_ids(body["cards"]) == [task["id"] for task in today_tasks]
    assert _practice_card_payload(body["cards"])["moduleName"] == today_tasks[0]["moduleName"]
    assert _feed_task_minutes(body["cards"], today_tasks) <= 120


def test_mock_review_pending_is_top_priority() -> None:
    memory = FakeMemoryService(
        unviewed_mock=LearnerWorkbenchObjectRecord(
            id="mock-session-1",
            type="mock_exam_review",
            title="行测模考复盘",
            subtitle="待查看 · 预估 68.2 分",
            source_count=2,
            payload={
                "sessionId": "mock-session-1",
                "estimatedScore": 68.2,
                "nextWeekReason": "上周模考资料分析 60.0%，本周加练文字资料。",
            },
        )
    )
    client = make_client(memory=memory)

    feed = client.get(
        "/api/v1/feed/today",
        headers={"Authorization": "Bearer user-feed"},
    )

    assert feed.status_code == 200
    body = feed.json()
    assert body["cards"][0]["type"] == "mock_review_pending"
    assert body["cards"][0]["payload"]["sessionId"] == "mock-session-1"
    assert body["cards"][0]["payload"]["estimatedScore"] == 68.2
    assert _feed_task_ids(body["cards"]) == ["weekly-guokao-mock-exam-review"]


def test_mock_exam_due_task_maps_to_feed_card_without_reordering_planner() -> None:
    client = make_client(memory=FakeMemoryService(mock_created_this_week=False))

    today = client.get(
        "/api/v1/taskstream/today",
        headers={"Authorization": "Bearer user-feed"},
    )
    feed = client.get(
        "/api/v1/feed/today",
        headers={"Authorization": "Bearer user-feed"},
    )

    assert today.status_code == 200
    assert feed.status_code == 200
    cards = feed.json()["cards"]
    assert any(card["type"] == "mock_exam_due" for card in cards)
    assert _feed_task_ids(cards) == [task["id"] for task in today.json()["tasks"]]


def test_goal_gap_reason_is_honest_when_calibration_data_missing() -> None:
    profile = LearnerProfileRecord(
        target_exam="国考",
        target_region="全国",
        position_group="中央机关综合管理岗",
        daily_minutes=120,
        study_mode="parttime",
        target_score=None,
        baseline_source="none",
        baseline_xingce_score=None,
        onboarding_complete=True,
    )
    client = make_client(
        memory=FakeMemoryService(profile=profile),
        calibration=FakeCalibrationService(xingce_score=None),
    )

    feed = client.get(
        "/api/v1/feed/today",
        headers={"Authorization": "Bearer user-feed"},
    )

    assert feed.status_code == 200
    goal_gap = next(card for card in feed.json()["cards"] if card["type"] == "goal_gap")
    assert goal_gap["payload"]["targetScore"] is None
    assert goal_gap["payload"]["estimatedScore"] is None
    assert "暂不计算分差" in goal_gap["reason"]
    assert goal_gap["dataLine"] == "暂无可用校准数据"


def _feed_task_ids(cards: list[dict[str, object]]) -> list[str]:
    task_ids: list[str] = []
    for card in cards:
        payload = card["payload"]
        assert isinstance(payload, dict)
        if card["type"] in {"task_practice", "task_essay", "task_method_card", "mock_review_pending"}:
            task_ids.append(str(payload["taskId"]))
        if card["type"] == "mock_exam_due":
            session_spec = payload["sessionSpec"]
            assert isinstance(session_spec, dict)
            task_ids.append(str(session_spec["taskId"]))
    return task_ids


def _practice_card_payload(cards: list[dict[str, object]]) -> dict[str, object]:
    for card in cards:
        if card["type"] == "task_practice":
            payload = card["payload"]
            assert isinstance(payload, dict)
            return payload
    raise AssertionError("task_practice card not found")


def _feed_task_minutes(
    cards: list[dict[str, object]],
    tasks: list[dict[str, object]],
) -> int:
    review_minutes = {
        task["id"]: int(task["estimatedMinutes"])
        for task in tasks
        if task["kind"] == "mock_exam_review"
    }
    minutes = 0
    for card in cards:
        payload = card["payload"]
        assert isinstance(payload, dict)
        if card["type"] in {"task_practice", "task_essay", "task_method_card"}:
            minutes += int(payload["estMinutes"])
        if card["type"] == "mock_exam_due":
            minutes += int(payload["estMinutes"])
        if card["type"] == "mock_review_pending":
            minutes += review_minutes[str(payload["taskId"])]
    return minutes
