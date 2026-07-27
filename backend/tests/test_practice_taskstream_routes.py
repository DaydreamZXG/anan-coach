from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.domains.auth.deps import get_auth_repository
from app.domains.auth.repository import AuthenticatedUser
from app.domains.knowledge.schemas import (
    QuestionDetail,
    QuestionExplanation,
    QuestionOccurrence,
    QuestionOption,
    QuestionSearchResponse,
    QuestionSummary,
    QuestionTag,
)
from app.domains.knowledge.service import QuestionNotFoundError, WeightedPracticeSelection
from app.domains.memory.repository import LearnerProfileRecord
from app.domains.practice.repository import FrozenPracticeQuestion
from app.domains.practice.router import get_practice_service
from app.domains.practice.service import PracticeService
from app.domains.progression.deps import get_progression_service
from app.domains.progression.schemas import ProgressionEventResult
from app.domains.calibration.schemas import AbilityEstimate, CalibrationSources
from app.domains.taskstream.router import get_taskstream_service
from app.domains.taskstream.service import (
    TODAY_XINGCE_TASK_ID,
    TaskstreamService,
    _daily_budget,
    _essay_minutes,
    _score_gap,
    _xingce_minutes,
)
from app.main import create_app


class FakeAuthRepository:
    def get_user_by_access_token(self, access_token: str) -> AuthenticatedUser | None:
        return AuthenticatedUser(
            user_id="user-practice",
            learner_key="user-practice",
            display_name=None,
            email=None,
        )


def test_taskstream_score_gap_requires_and_sums_both_written_components() -> None:
    assert _score_gap(130, 65, 55) == 10.0
    assert _score_gap(130, 65, None) == 0.0


class FakeMemoryService:
    def __init__(self) -> None:
        self.practice_answers: list[dict[str, object]] = []

    def get_required_profile(self, *, user_id: str) -> LearnerProfileRecord:
        return LearnerProfileRecord(
            target_exam="国考",
            target_region="全国",
            position_group="中央机关综合管理岗",
            daily_minutes=120,
            onboarding_complete=True,
        )

    def record_practice_answer(self, **kwargs: object) -> None:
        self.practice_answers.append(kwargs)

    def list_completed_task_ids_for_date(
        self,
        *,
        user_id: str,
        completed_on: date,
    ) -> list[str]:
        return [str(item["task_id"]) for item in self.practice_answers]

    def get_unviewed_mock_review(self, *, user_id: str):
        return None

    def get_latest_mock_review_reason(self, *, user_id: str) -> str:
        return "上周模考资料分析 80.0%，本周保持文字资料。"

    def has_mock_exam_this_week(self, *, user_id: str, week_start: date) -> bool:
        return True


class FakeCalibrationService:
    def get_ability_estimate(self, *, user_id: str) -> AbilityEstimate:
        return AbilityEstimate(
            xingce_score=65.0,
            confidence="high",
            status_label="高置信校准中",
            data_line="基于 1 次摸底/自报 + 0 题练习 + 1 次模考",
            sources=CalibrationSources(
                baseline=1,
                practice_answers=0,
                mock_exams=1,
            ),
            module_estimates=[],
            latest_mock_reason="上周模考资料分析 80.0%，本周保持文字资料。",
        )


class FakeProgressionService:
    def record_practice_answer(
        self,
        *,
        user_id: str,
        correct: bool,
    ) -> ProgressionEventResult:
        return ProgressionEventResult(
            merit_delta=10 if correct else 0,
            fame_delta=1 if correct else -1,
            court_risk_delta=-1 if correct else 2,
            rank_changed=False,
            narrative="日常政务判准，政绩 +10。" if correct else "日常政务判驳。",
        )


class FakeKnowledgeService:
    def __init__(self) -> None:
        self.get_question_calls = 0
        self.live_answer = "B"

    def select_weighted_xingce_practice(
        self,
        *,
        exam_scope: str,
        limit: int,
        module_name: str | None = None,
    ) -> WeightedPracticeSelection:
        items = self.search_questions(limit=limit).items
        return WeightedPracticeSelection(
            release_id="00000000-0000-0000-0000-000000000101",
            release_version="knowledge-map@taskstream-test",
            source_data_version="questions@taskstream-test",
            fresh_until=datetime(2099, 1, 1, tzinfo=UTC),
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
        items = [
            QuestionSummary(
                question_id=f"cq_weighted_{index}",
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
            for index in range(1, 4)
        ]
        return QuestionSearchResponse(
            limit=limit,
            items=items[:limit],
        )

    def get_question(self, question_id: str) -> QuestionDetail:
        self.get_question_calls += 1
        if question_id == "missing":
            raise QuestionNotFoundError(question_id)
        return QuestionDetail(
            question_id=question_id,
            domain="objective",
            status="active",
            module_name="资料分析",
            question_type="资料分析",
            display_level1="资料分析",
            display_level2="文字资料",
            display_level3="增长率",
            knowledge_family="文字资料",
            teaching_point="增长率",
            micro_skill="增长率",
            stem="2025年某地区完成投资额同比增长。",
            stem_html=None,
            answer=self.live_answer,
            material_text=None,
            material_html=None,
            difficulty=None,
            visual_required=False,
            needs_multimodal=False,
            shared_context_required=False,
            occurrence_count=11,
            occurrence_province_count=11,
            first_year=2026,
            latest_year=2026,
            source_member_count=1,
            options=[
                QuestionOption(option_index=1, label="A", option_text="A、无关项"),
                QuestionOption(option_index=2, label="B", option_text="B、拆桥项"),
            ],
            explanation=QuestionExplanation(
                preferred_analysis="答案B。B项切断食用香榧与治疗作用之间的联系。",
                original_analysis=None,
                stored_analysis=None,
                analysis_source="ai_rewrite",
                analysis_status="ai_rewrite_accepted",
                prefer_ai_analysis=True,
                needs_review=False,
            ),
            occurrences=[
                QuestionOccurrence(
                    occurrence_id="qbo_09db7fcfce1442013bdab65cba02",
                    source_question_id="source-1",
                    source_dataset_id="fenbi",
                    province="国考",
                    year=2025,
                    number=76,
                    section="资料分析",
                    paper_title="2026年国家公务员录用考试《行测》题（地市级网友回忆版）",
                    source_path=None,
                    rank=1,
                )
            ],
            tags=[
                QuestionTag(
                    tag_type="knowledge_point",
                    tag_value="文字资料",
                    sort_order=1,
                    evidence_link_count=1,
                )
            ],
            knowledge_chunks=[],
            assets=[],
        )


class FakePracticeRepository:
    def __init__(self) -> None:
        self.questions = [
            FrozenPracticeQuestion(
                release_id="00000000-0000-0000-0000-000000000101",
                release_version="knowledge-map@taskstream-test",
                node_id="km_fixture",
                tag_value="文字资料",
                question_id=f"cq_weighted_{index}",
                content_version_id=f"{index:064x}",
                visual_required=False,
                shared_context_required=False,
                occurrence_count=11,
                latest_year=2026,
                snapshot={
                    "question_id": f"cq_weighted_{index}",
                    "module_name": "资料分析",
                    "question_type": "资料分析",
                    "stem": "2025年某地区完成投资额同比增长。",
                    "stem_html": None,
                    "answer": "B",
                    "material_text": None,
                    "material_html": None,
                    "preferred_analysis": (
                        "答案B。B项切断食用香榧与治疗作用之间的联系。"
                    ),
                    "analysis_source": "ai_rewrite",
                    "analysis_status": "ai_rewrite_accepted",
                    "prefer_ai_analysis": True,
                    "analysis_needs_review": False,
                    "options": [
                        {
                            "label": "A",
                            "option_text": "A、无关项",
                            "option_html": None,
                        },
                        {
                            "label": "B",
                            "option_text": "B、拆桥项",
                            "option_html": None,
                        },
                    ],
                    "assets": [],
                },
            )
            for index in range(1, 4)
        ]

    def frozen_questions(
        self,
        *,
        release_id: str,
        node_id: str,
        question_ids: list[str],
    ) -> list[FrozenPracticeQuestion]:
        return [
            question
            for question_id in question_ids
            for question in self.questions
            if question.release_id == release_id
            and question.node_id == node_id
            and question.question_id == question_id
        ]

    def weighted_frozen_questions(
        self,
        *,
        release_id: str,
        node_id: str,
        limit: int,
    ) -> list[FrozenPracticeQuestion]:
        return [
            question
            for question in self.questions
            if question.release_id == release_id and question.node_id == node_id
        ][:limit]


def make_client(
    *,
    knowledge: FakeKnowledgeService | None = None,
    practice_repository: FakePracticeRepository | None = None,
) -> TestClient:
    app = create_app()
    knowledge = knowledge or FakeKnowledgeService()
    practice_repository = practice_repository or FakePracticeRepository()
    memory = FakeMemoryService()
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[get_taskstream_service] = lambda: TaskstreamService(  # type: ignore[arg-type]
        knowledge_service=knowledge,
        memory_service=memory,
        calibration_service=FakeCalibrationService(),
    )
    app.dependency_overrides[get_practice_service] = lambda: PracticeService(  # type: ignore[arg-type]
        knowledge_service=knowledge,
        memory_service=memory,
        repository=practice_repository,
    )
    app.dependency_overrides[get_progression_service] = lambda: FakeProgressionService()  # type: ignore[return-value]
    return TestClient(app)


def test_taskstream_today_uses_real_question_ids() -> None:
    response = make_client().get(
        "/api/v1/taskstream/today",
        headers={"Authorization": "Bearer user-practice"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["headline"] == "今天 2 小时，先完成 1 个任务"
    assert body["planBasis"] == "rules_v1"
    assert body["target"]["scoreGap"] == 0.0
    assert body["target"]["scoreGapAvailable"] is False
    assert [track["track"] for track in body["trackMix"]] == ["行测"]
    assert all(track["minutes"] <= 25 for track in body["trackMix"])
    xingce_task = body["tasks"][0]
    assert xingce_task["id"] == TODAY_XINGCE_TASK_ID
    assert xingce_task["moduleName"] == "资料分析"
    assert xingce_task["sourceGrounding"] == "product.knowledge_map_nodes + product.questions"
    assert xingce_task["questionIds"] == [
        "cq_weighted_1",
        "cq_weighted_2",
        "cq_weighted_3",
    ]
    assert "近5年国考" in xingce_task["reason"]
    assert "文字资料" in xingce_task["subtitle"]
    assert len(body["tasks"]) == 1
    assert all("/api/v1/essay" not in task["startUrl"] for task in body["tasks"])
    essay_handoff = next(alert for alert in body["alerts"] if alert["id"] == "essay-v2-planning-owner")
    assert essay_handoff["actionUrl"] == "/api/v2/plans/today"
    assert "无版本证据" in essay_handoff["subtitle"]
    assert body["completedTaskIds"] == []


def test_time_budget_r0_uses_revised_defaults_and_task_caps() -> None:
    parttime = _daily_budget(None, "parttime")
    fulltime = _daily_budget(None, "fulltime")

    assert parttime.daily_minutes == 120
    assert fulltime.daily_minutes == 480
    assert _xingce_minutes(parttime.daily_minutes, parttime.study_mode) <= 25
    assert _essay_minutes(parttime.daily_minutes, parttime.study_mode, 25) <= 25
    fulltime_xingce = _xingce_minutes(fulltime.daily_minutes, fulltime.study_mode)
    assert 40 <= fulltime_xingce <= 120
    assert (
        40
        <= _essay_minutes(
            fulltime.daily_minutes,
            fulltime.study_mode,
            fulltime_xingce,
        )
        <= 120
    )


def test_practice_task_start_contract() -> None:
    response = make_client().get(
        f"/api/v1/practice/tasks/{TODAY_XINGCE_TASK_ID}",
        headers={"Authorization": "Bearer user-practice"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["taskId"] == TODAY_XINGCE_TASK_ID
    assert body["track"] == "行测"
    assert body["questionCount"] == 3
    assert body["title"] == "国考资料分析·文字资料 3 题"
    assert body["questions"][0]["explanation"]["analysisSource"] == "ai_rewrite"


def test_practice_answer_contract() -> None:
    client = make_client()
    task_version = client.get(
        f"/api/v1/practice/tasks/{TODAY_XINGCE_TASK_ID}",
        headers={"Authorization": "Bearer user-practice"},
    ).json()["taskVersion"]
    response = client.post(
        f"/api/v1/practice/tasks/{TODAY_XINGCE_TASK_ID}/answers",
        headers={"Authorization": "Bearer user-practice"},
        json={
            "questionId": "cq_weighted_1",
            "selectedAnswer": "B",
            "taskVersion": task_version,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correct"] is True
    assert body["correctAnswer"] == "B"
    assert body["explanationSource"] == "ai_rewrite"
    assert body["sourceCount"] == 11
    assert "国考" in body["sourceSummary"]

    today = client.get(
        "/api/v1/taskstream/today",
        headers={"Authorization": "Bearer user-practice"},
    )
    assert today.status_code == 200
    assert today.json()["completedTaskIds"] == [TODAY_XINGCE_TASK_ID]


def test_practice_display_and_scoring_use_the_same_frozen_content_version() -> None:
    knowledge = FakeKnowledgeService()
    repository = FakePracticeRepository()
    client = make_client(knowledge=knowledge, practice_repository=repository)

    task = client.get(
        f"/api/v1/practice/tasks/{TODAY_XINGCE_TASK_ID}",
        headers={"Authorization": "Bearer user-practice"},
    ).json()
    knowledge.live_answer = "A"

    response = client.post(
        f"/api/v1/practice/tasks/{TODAY_XINGCE_TASK_ID}/answers",
        headers={"Authorization": "Bearer user-practice"},
        json={
            "questionId": "cq_weighted_1",
            "selectedAnswer": "B",
            "taskVersion": task["taskVersion"],
        },
    )

    assert response.status_code == 200
    assert response.json()["correct"] is True
    assert response.json()["correctAnswer"] == "B"
    assert knowledge.get_question_calls == 0


def test_practice_answer_rejects_legacy_learner_header() -> None:
    client = make_client()
    task_version = client.get(
        f"/api/v1/practice/tasks/{TODAY_XINGCE_TASK_ID}",
        headers={"Authorization": "Bearer user-practice"},
    ).json()["taskVersion"]
    response = client.post(
        f"/api/v1/practice/tasks/{TODAY_XINGCE_TASK_ID}/answers",
        headers={"X-Learner-Id": "user-practice"},
        json={
            "questionId": "cq_weighted_1",
            "selectedAnswer": "B",
            "taskVersion": task_version,
        },
    )

    assert response.status_code == 401
