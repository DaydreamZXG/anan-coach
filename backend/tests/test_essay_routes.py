from __future__ import annotations

from fastapi.testclient import TestClient
from httpx import Response

from app.domains.ai.provider import AIProvider
from app.domains.essay.router import get_essay_service
from app.domains.essay.repository import EssayRepository
from app.domains.essay.schemas import EssayMaterial, EssayTask
from app.domains.essay.service import EssayService
from app.domains.taskstream.service import TODAY_ESSAY_TASK_ID
from app.main import create_app


class FakeEssayService:
    def get_task(self, task_id: str) -> EssayTask:
        return EssayTask(
            task_id=task_id,
            question_id="cq_essay_real",
            title="申论概括题重写",
            material="材料1\n平台分散、权责不清、反馈不足。",
            materials=[
                EssayMaterial(
                    index=1,
                    title="材料1",
                    text="平台分散、权责不清、反馈不足。",
                    source="fixture",
                )
            ],
            material_incomplete=False,
            prompt="请概括群众诉求响应慢原因。",
            word_limit=80,
            dimensions=["要点完整度", "概括抽象度", "结构层次", "语言压缩", "格式规范"],
        )


def make_client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_essay_service] = lambda: FakeEssayService()  # type: ignore[return-value]
    return TestClient(app)


def test_essay_task_contract() -> None:
    response = make_client().get(f"/api/v1/essay/tasks/{TODAY_ESSAY_TASK_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["taskId"] == TODAY_ESSAY_TASK_ID
    assert body["wordLimit"] == 80
    assert "要点完整度" in body["dimensions"]
    assert response.headers["deprecation"] == "true"
    assert response.headers["sunset"] == "Mon, 13 Jul 2026 00:00:00 GMT"
    assert response.headers["link"] == '</api/v2/plans/today>; rel="successor-version"'


def test_essay_review_is_gone_even_without_authentication() -> None:
    response = make_client().post(
        f"/api/v1/essay/tasks/{TODAY_ESSAY_TASK_ID}/review",
        json={"answerText": "群众诉求响应慢，主要是部门多、流程慢、反馈不清。"},
    )

    _assert_v1_writer_retired(response, TODAY_ESSAY_TASK_ID)


def test_essay_review_is_gone_for_legacy_learner_header() -> None:
    response = make_client().post(
        f"/api/v1/essay/tasks/{TODAY_ESSAY_TASK_ID}/review",
        headers={"X-Learner-Id": "learner-test"},
        json={"answerText": "群众诉求响应慢，主要是部门多、流程慢、反馈不清。"},
    )

    _assert_v1_writer_retired(response, TODAY_ESSAY_TASK_ID)


def test_essay_review_writer_never_reaches_the_old_ai_or_quota_service() -> None:
    response = make_client().post(
        f"/api/v1/essay/tasks/{TODAY_ESSAY_TASK_ID}/review",
        headers={"Authorization": "Bearer learner-test"},
        json={"answerText": "群众诉求响应慢，主要是部门多、流程慢、反馈不清。"},
    )

    _assert_v1_writer_retired(response, TODAY_ESSAY_TASK_ID)


def test_essay_review_retirement_precedes_legacy_quota_logic() -> None:
    response = make_client().post(
        f"/api/v1/essay/tasks/{TODAY_ESSAY_TASK_ID}/review",
        headers={"Authorization": "Bearer quota-full"},
        json={"answerText": "群众诉求响应慢，主要是部门多、流程慢、反馈不清。"},
    )

    _assert_v1_writer_retired(response, TODAY_ESSAY_TASK_ID)


def test_essay_rewrite_writer_is_permanently_gone() -> None:
    response = make_client().post(
        f"/api/v1/essay/tasks/{TODAY_ESSAY_TASK_ID}/rewrite",
        headers={"Authorization": "Bearer learner-test"},
        json={
            "reviewId": "er_test",
            "originalAnswer": "群众诉求响应慢，主要是部门多、流程慢、反馈不清。",
            "rewrittenAnswer": "平台分散、权责不清、反馈不足，导致诉求转派多、办理慢。",
        },
    )

    _assert_v1_writer_retired(response, TODAY_ESSAY_TASK_ID)


def test_retired_writer_does_not_validate_or_accept_the_old_request_schema() -> None:
    response = make_client().post(
        f"/api/v1/essay/tasks/{TODAY_ESSAY_TASK_ID}/review",
        json={"legacy": "payload"},
    )

    _assert_v1_writer_retired(response, TODAY_ESSAY_TASK_ID)


def test_openapi_marks_all_v1_essay_routes_deprecated() -> None:
    paths = make_client().get("/openapi.json").json()["paths"]

    assert paths["/api/v1/essay/tasks/{task_id}"]["get"]["deprecated"] is True
    assert paths["/api/v1/essay/tasks/{task_id}/review"]["post"]["deprecated"] is True
    assert paths["/api/v1/essay/tasks/{task_id}/rewrite"]["post"]["deprecated"] is True
    assert "requestBody" not in paths["/api/v1/essay/tasks/{task_id}/review"]["post"]
    assert "requestBody" not in paths["/api/v1/essay/tasks/{task_id}/rewrite"]["post"]


def test_legacy_essay_service_exposes_no_write_capability() -> None:
    assert not hasattr(EssayService, "review_answer")
    assert not hasattr(EssayService, "compare_rewrite")
    assert not hasattr(EssayRepository, "create_attempt")
    assert not hasattr(EssayRepository, "update_rewrite")
    assert not hasattr(AIProvider, "review_essay_answer")


def _assert_v1_writer_retired(response: Response, task_id: str) -> None:
    assert response.status_code == 410
    body = response.json()
    assert body["detail"] == {
        "code": "essay_v1_write_retired",
        "message": "Legacy essay AI review and rewrite writes are permanently retired.",
        "owner": "essay_intelligence_v2",
        "replacement": {
            "plan": "/api/v2/plans/today",
            "workspace": "/api/v2/essay-intelligence/tasks/{taskVersionId}/workspace",
            "completePlanTask": "/api/v2/essay-intelligence/artifacts/complete-plan-task",
        },
        "legacyTaskId": task_id,
    }
    assert response.headers["deprecation"] == "true"
