from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.task_content.service import (
    public_question_asset_url,
    safe_rich_html,
)
from app.domains.xingce_learning.real_attempt.service import _published_explanation
from app.domains.xingce_learning.explanation_policy import stored_explanation
from app.main import create_app
from tests.test_planning_routes import (
    _client,
    _seed_learner_and_published_essay_content,
)


def test_xingce_learning_routes_require_authentication() -> None:
    client = TestClient(create_app())
    resource_id = str(uuid4())
    headers = {"Idempotency-Key": "xingce-auth-required"}
    requests = (
        client.post(
            "/api/v2/xingce/task-sessions",
            headers=headers,
            json={"planTaskId": resource_id},
        ),
        client.get(f"/api/v2/xingce/task-sessions/{resource_id}"),
        client.put(
            f"/api/v2/xingce/task-sessions/{resource_id}/checkpoint",
            headers=headers,
            json={
                "expectedRevision": 1,
                "currentQuestionId": "question-1",
                "answers": [
                    {
                        "questionId": "question-1",
                        "selectedOptionIds": ["A"],
                        "eliminatedOptionIds": [],
                        "confidence": "medium",
                        "marked": False,
                        "skipped": False,
                        "elapsedSeconds": 1,
                        "draftText": "",
                        "materialPosition": None,
                    }
                ],
                "totalElapsedSeconds": 1,
            },
        ),
        client.post(
            f"/api/v2/xingce/task-sessions/{resource_id}/submit",
            headers=headers,
            json={"expectedRevision": 1, "totalElapsedSeconds": 1},
        ),
        client.put(
            f"/api/v2/xingce/attempt-reviews/{resource_id}/reflection",
            headers=headers,
            json={
                "expectedRevision": 1,
                "rawText": "我的复盘原话。",
                "tags": ["审题"],
                "strategyNote": None,
                "doubtNote": None,
            },
        ),
        client.put(
            f"/api/v2/xingce/attempt-reviews/{resource_id}/cause-decision",
            headers=headers,
            json={
                "expectedRevision": 1,
                "decision": "skip",
                "replacementCause": None,
            },
        ),
    )

    assert [response.status_code for response in requests] == [401] * len(requests)


def test_xingce_question_asset_policy_only_accepts_owned_public_https() -> None:
    owned = (
        "https://img.xingguoxian.cn/"
        "data/question-bank-export/files/source-question-bank/"
        "fenbi-final-analysis-v1-20260613-assets/questions/q-1.png"
    )

    assert public_question_asset_url(owned) == owned
    assert public_question_asset_url("questions/private/q-1.png") is None
    assert public_question_asset_url(owned.replace("https://", "http://")) is None
    assert public_question_asset_url("https://img.xingguoxian.cn/private/q-1.png") is None


def test_xingce_rich_text_is_sanitized_and_every_stored_explanation_is_visible() -> None:
    sanitized = safe_rich_html(
        '<p style="color:red">材料<strong>重点</strong>'
        '<script>alert(1)</script><table><tr><td colspan="2" onclick="x()">1</td>'
        '</tr></table><img src="https://evil.example/x.png"></p>'
    )

    assert sanitized == (
        '<p>材料<strong>重点</strong><table><tr><td colspan="2">1</td></tr></table></p>'
    )
    assert _published_explanation(
        {
            "preferred_analysis": "未经确认的解析",
            "analysis_source": "ai_rewrite",
            "analysis_status": "pending_review",
            "analysis_needs_review": True,
        }
    ) == ("未经确认的解析", "ai_rewrite")
    assert _published_explanation(
        {
            "preferred_analysis": "已经验收的解析",
            "analysis_source": "ai_rewrite",
            "analysis_status": "ai_rewrite_accepted",
            "analysis_needs_review": False,
        }
    ) == ("已经验收的解析", "ai_rewrite")
    assert stored_explanation(
        {
            "preferred_analysis": "题库原始解析",
            "analysis_source": "own_bank",
            "analysis_status": "human_review_fallback_original",
            "analysis_needs_review": True,
        }
    ) == ("题库原始解析", "own_bank")
    assert stored_explanation(
        {
            "preferred_analysis": "待审核 AI 解析",
            "analysis_source": "ai_rewrite",
            "analysis_status": "pending_review",
            "analysis_needs_review": True,
        }
    ) == ("待审核 AI 解析", "ai_rewrite")
    assert public_question_asset_url("https://evil.example/assets/q-1.png") is None
    assert (
        public_question_asset_url("https://gongkao-dazi-prod.tos-cn-guangzhou.volces.com/q-1.png")
        is None
    )


def test_visual_question_without_owned_public_image_is_not_planned(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "xingce-visual-gate-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
        include_objective_questions=False,
    )
    _seed_objective_questions(
        db_session_factory,
        count=1,
        visual_required=True,
        valid_public_asset=False,
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v2/plans/today", headers=auth)

    assert response.status_code == 200, response.text
    plan = response.json()["data"]
    assert all(task["track"] != "xingce" for task in plan["tasks"])
    assert plan["dualTrackExplanation"]["omittedTrackReasons"]["xingce"] == (
        "当前没有满足内容与证据门禁的行测任务。"
    )
    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from learning.xingce_task_sessions")).scalar_one()
            == 0
        )


def _seed_objective_questions(
    factory: sessionmaker[Session],
    *,
    count: int = 3,
    visual_required: bool = False,
    valid_public_asset: bool = True,
) -> None:
    answers = ("A", "B", "C", "D")
    with factory.begin() as session:
        for index in range(1, count + 1):
            question_id = f"xingce-route-question-{index}"
            session.execute(
                text(
                    """
                    insert into product.questions (
                      question_id, domain, status, module_name, question_type,
                      stem, answer, material_text, shared_context_required,
                      visual_required, occurrence_count, latest_year
                    ) values (
                      :question_id, 'objective', 'canonical', '言语理解',
                      'single_choice', :stem, :answer, :material_text,
                      :shared_context_required, :visual_required,
                      :occurrence_count, 2026
                    )
                    """
                ),
                {
                    "question_id": question_id,
                    "stem": f"行测路由验收题 {index}",
                    "answer": answers[(index - 1) % len(answers)],
                    "material_text": "路由验收共用材料" if index == 1 else None,
                    "shared_context_required": index == 1,
                    "visual_required": visual_required,
                    "occurrence_count": 100 - index,
                },
            )
            for option_index, label in enumerate(answers, start=1):
                session.execute(
                    text(
                        """
                        insert into product.question_options (
                          question_id, option_index, label, option_text
                        ) values (:question_id, :option_index, :label, :option_text)
                        """
                    ),
                    {
                        "question_id": question_id,
                        "option_index": option_index,
                        "label": label,
                        "option_text": f"选项 {label}",
                    },
                )
            session.execute(
                text(
                    """
                    insert into product.question_explanations (
                      question_id, preferred_analysis, analysis_source, analysis_status
                    ) values (:question_id, :analysis, 'own_bank', 'verified')
                    """
                ),
                {"question_id": question_id, "analysis": f"解析 {index}"},
            )
            if visual_required:
                session.execute(
                    text(
                        """
                        insert into product.question_assets (
                          asset_id, question_id, part_kind, part_index,
                          image_ordinal, image_src, object_key,
                          is_representative_asset
                        ) values (
                          :asset_id, :question_id, 'stem', 0, 0,
                          :image_src, 'private/questions/q.png', true
                        )
                        """
                    ),
                    {
                        "asset_id": f"asset-{question_id}",
                        "question_id": question_id,
                        "image_src": (
                            "https://img.xingguoxian.cn/"
                            "data/question-bank-export/files/source-question-bank/"
                            "fenbi-final-analysis-v1-20260613-assets/questions/"
                            f"{question_id}.png"
                            if valid_public_asset
                            else f"https://evil.example/{question_id}.png"
                        ),
                    },
                )


def _xingce_task(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/v2/plans/today", headers=auth)
    assert response.status_code == 200, response.text
    return next(task for task in response.json()["data"]["tasks"] if task["track"] == "xingce")
