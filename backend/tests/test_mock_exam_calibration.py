from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.mock_exam.service import GUOKAO_MOCK_BLUEPRINT
from app.main import create_app


def test_weekly_mock_exam_full_loop_feeds_workbench_and_calibration(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="mock-token")
    excluded_question_id = _seed_mock_qbank(db_session_factory)
    _seed_recent_answer(db_session_factory, user_id=user_id, question_id=excluded_question_id)
    client = _make_client(db_session_factory)

    created = client.post(
        "/api/v1/mock-exams/weekly",
        headers={"Authorization": "Bearer mock-token"},
    )

    assert created.status_code == 200
    exam = created.json()
    assert exam["questionCount"] == 135
    question_ids = [item["questionId"] for item in exam["questions"]]
    assert excluded_question_id not in question_ids
    assert exam["durationMinutes"] == 120
    assert exam["sourceGrounding"] == "product.questions + product.knowledge_map_nodes + learner.mock_exam_sessions"

    answers = [
        {
            "questionId": question_id,
            "selectedAnswer": "A" if index < 2 else "B",
        }
        for index, question_id in enumerate(question_ids)
    ]
    submitted = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers={"Authorization": "Bearer mock-token"},
        json={"answers": answers},
    )

    assert submitted.status_code == 200
    review = submitted.json()["review"]
    assert review["estimatedScore"] == 98.5
    assert review["viewed"] is False
    assert "未使用全国分位" in review["estimateSource"]
    assert review["wrongClusters"][0]["methodCardIds"] == ["mc_mock"]
    assert "上周模考" in review["nextWeekReason"]

    calibration = client.get(
        "/api/v1/calibration/ability",
        headers={"Authorization": "Bearer mock-token"},
    )
    assert calibration.status_code == 200
    ability = calibration.json()
    assert ability["confidence"] == "high"
    assert ability["xingceScore"] == 98.5
    assert ability["sources"]["mockExams"] == 1
    assert "1 次模考" in ability["dataLine"]

    workbench_before_view = client.get(
        "/api/v1/workbench/preview",
        headers={"Authorization": "Bearer mock-token"},
    )
    assert workbench_before_view.status_code == 200
    assert workbench_before_view.json()["sections"][0]["id"] == "mock_exam"
    assert workbench_before_view.json()["sections"][0]["objects"][0]["payload"]["viewed"] is False

    viewed = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/review/view",
        headers={"Authorization": "Bearer mock-token"},
    )
    assert viewed.status_code == 200
    assert viewed.json()["viewed"] is True

    today = client.get(
        "/api/v1/taskstream/today",
        headers={"Authorization": "Bearer mock-token"},
    )
    assert today.status_code == 200
    body = today.json()
    assert body["calibration"]["confidence"] == "high"
    assert body["tasks"][0]["kind"] == "xingce_practice"
    assert "上周模考" in body["rationale"]


def test_mock_exam_submission_requires_all_assigned_questions(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-incomplete-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)

    created = client.post(
        "/api/v1/mock-exams/weekly",
        headers={"Authorization": "Bearer mock-incomplete-token"},
    )
    assert created.status_code == 200
    exam = created.json()
    first_question = exam["questions"][0]["questionId"]

    submitted = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers={"Authorization": "Bearer mock-incomplete-token"},
        json={"answers": [{"questionId": first_question, "selectedAnswer": "B"}]},
    )

    assert submitted.status_code == 409


def test_taskstream_prioritizes_unviewed_mock_review(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="unviewed-token")
    _seed_mock_qbank(db_session_factory)
    _seed_unviewed_mock_review(db_session_factory, user_id=user_id)
    client = _make_client(db_session_factory)

    response = client.get(
        "/api/v1/taskstream/today",
        headers={"Authorization": "Bearer unviewed-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["alerts"][0]["id"] == "mock-review-unviewed"
    assert body["tasks"][0]["kind"] == "mock_exam_review"
    assert body["tasks"][0]["sourceGrounding"] == "learner.mock_exam_sessions.review_payload"


def test_taskstream_schedules_weekly_mock_when_missing_this_week(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-due-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)

    response = client.get(
        "/api/v1/taskstream/today",
        headers={"Authorization": "Bearer mock-due-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tasks"] == [
        {
            "id": "weekly-guokao-mock-exam",
            "title": "国考行测 135 题整卷模考",
            "subtitle": "严格 120 分钟，中途不反馈，交卷后生成复盘。",
            "kind": "mock_exam",
            "track": "行测",
            "moduleName": None,
            "estimatedMinutes": 120,
            "reason": "每周模考用于高置信校准，不给全国分位，只看自己的趋势。",
            "sourceGrounding": "product.questions + product.knowledge_map_nodes",
            "questionIds": [],
            "startUrl": "/api/v1/mock-exams/weekly",
        }
    ]


def _make_client(db_session_factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def _seed_user(db_session_factory: sessionmaker[Session], *, token: str) -> str:
    user_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (cast(:user_id as uuid), '学习者')
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id,
                  user_id,
                  session_token_hash,
                  device_id,
                  expires_at
                ) values (
                  cast(:session_id as uuid),
                  cast(:user_id as uuid),
                  :session_token_hash,
                  'ios-test',
                  :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "session_token_hash": token_hash_for_tests(token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id,
                  target_exam,
                  target_region,
                  position_group,
                  daily_minutes,
                  study_mode,
                  target_score,
                  baseline_source,
                  baseline_xingce_score,
                  onboarding_complete
                ) values (
                  cast(:user_id as uuid),
                  '国考',
                  '全国',
                  '中央机关综合管理岗',
                  120,
                  'parttime',
                  132,
                  'self_report',
                  60,
                  true
                )
                """
            ),
            {"user_id": user_id},
        )
        session.commit()
    return user_id


def _seed_mock_qbank(db_session_factory: sessionmaker[Session]) -> str:
    module_slugs = {
        "常识判断": "changshi",
        "言语理解与表达": "yanyu",
        "数量关系": "shuliang",
        "判断推理": "panduan",
        "资料分析": "ziliao",
    }
    excluded_question_id = "q_changshi_extra_000"
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into product.method_cards (
                  id,
                  exam_scope,
                  track,
                  module_name,
                  tag_path,
                  title,
                  recognition,
                  applicability,
                  method_steps,
                  error_signatures,
                  fix_action,
                  time_budget_sec,
                  accuracy_rule,
                  pitfall,
                  author_note,
                  validation,
                  priority,
                  status
                ) values (
                  'mc_mock',
                  'guokao',
                  'xingce',
                  '常识判断',
                  '高频常识判断',
                  '模考错题修复卡',
                  '模考错题',
                  '{}'::jsonb,
                  '["定位错因","补同类题"]'::jsonb,
                  '[]'::jsonb,
                  '先复盘本 cluster，再做同类题。',
                  120,
                  '复盘后同类题正确率提升',
                  null,
                  'test',
                  '{}'::jsonb,
                  5,
                  'published'
                )
                """
            )
        )
        for module_name, count in GUOKAO_MOCK_BLUEPRINT:
            slug = module_slugs[module_name]
            tag_value = f"高频{module_name}"
            method_card_ids = '["mc_mock"]' if module_name == "常识判断" else "[]"
            session.execute(
                text(
                    """
                    insert into product.knowledge_map_nodes (
                      node_id,
                      exam_scope,
                      track,
                      module_name,
                      node_type,
                      tag_type,
                      tag_value,
                      display_path,
                      support_status,
                      near5_window_start_year,
                      near5_window_end_year,
                      near5_weight,
                      near5_occurrence_count,
                      stable_for_old_questions,
                      method_card_ids,
                      evidence,
                      status
                    ) values (
                      :node_id,
                      'guokao',
                      'xingce',
                      :module_name,
                      'knowledge_point',
                      'knowledge_point',
                      :tag_value,
                      :display_path,
                      'text_supported',
                      2022,
                      2026,
                      0.5,
                      50,
                      true,
                      cast(:method_card_ids as jsonb),
                      '{}'::jsonb,
                      'published'
                    )
                    """
                ),
                {
                    "node_id": f"km_{slug}",
                    "module_name": module_name,
                    "tag_value": tag_value,
                    "display_path": f"行测/{module_name}/{tag_value}",
                    "method_card_ids": method_card_ids,
                },
            )
            total_to_insert = count + (1 if module_name == "常识判断" else 0)
            for index in range(total_to_insert):
                question_id = (
                    excluded_question_id
                    if module_name == "常识判断" and index == 0
                    else f"q_{slug}_{index:03d}"
                )
                session.execute(
                    text(
                        """
                        insert into product.questions (
                          question_id,
                          domain,
                          status,
                          module_name,
                          question_type,
                          stem,
                          answer,
                          visual_required,
                          latest_year,
                          occurrence_count
                        ) values (
                          :question_id,
                          'objective',
                          'active',
                          :module_name,
                          :module_name,
                          :stem,
                          'B',
                          false,
                          2026,
                          10
                        )
                        """
                    ),
                    {
                        "question_id": question_id,
                        "module_name": module_name,
                        "stem": f"{module_name} 第 {index} 题",
                    },
                )
                session.execute(
                    text(
                        """
                        insert into product.question_tags (
                          question_tag_id,
                          question_id,
                          tag_type,
                          tag_value
                        ) values (
                          :question_tag_id,
                          :question_id,
                          'knowledge_point',
                          :tag_value
                        )
                        """
                    ),
                    {
                        "question_tag_id": f"tag_{question_id}",
                        "question_id": question_id,
                        "tag_value": tag_value,
                    },
                )
                session.execute(
                    text(
                        """
                        insert into product.question_occurrences (
                          occurrence_id,
                          question_id,
                          province,
                          year,
                          paper_title,
                          canonical_occurrence_rank
                        ) values (
                          :occurrence_id,
                          :question_id,
                          '国考',
                          2026,
                          '2026年国家公务员录用考试《行测》题（测试卷）',
                          1
                        )
                        """
                    ),
                    {
                        "occurrence_id": f"occ_{question_id}",
                        "question_id": question_id,
                    },
                )
        session.commit()
    return excluded_question_id


def _seed_recent_answer(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
    question_id: str,
) -> None:
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.practice_answers (
                  answer_id,
                  user_id,
                  task_id,
                  question_id,
                  selected_answer,
                  correct_answer,
                  correct,
                  answered_at
                ) values (
                  gen_random_uuid(),
                  cast(:user_id as uuid),
                  'recent-practice',
                  :question_id,
                  'B',
                  'B',
                  true,
                  now()
                )
                """
            ),
            {"user_id": user_id, "question_id": question_id},
        )
        session.commit()


def _seed_unviewed_mock_review(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
) -> None:
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.mock_exam_sessions (
                  session_id,
                  user_id,
                  exam_scope,
                  track,
                  status,
                  question_ids,
                  blueprint,
                  review_payload,
                  submitted_at,
                  created_at,
                  updated_at
                ) values (
                  gen_random_uuid(),
                  cast(:user_id as uuid),
                  'guokao',
                  'xingce',
                  'submitted',
                  '[]'::jsonb,
                  '[]'::jsonb,
                  jsonb_build_object(
                    'sessionId', gen_random_uuid()::text,
                    'estimatedScore', 70.5,
                    'estimateSource', '按本次 135 题正确率折算，未使用全国分位。',
                    'submittedAt', now()::text,
                    'viewed', false,
                    'modules', '[]'::jsonb,
                    'wrongClusters', '[]'::jsonb,
                    'nextWeekReason', '上周模考资料分析 62.5%，本周加练基期量。'
                  ),
                  now(),
                  now(),
                  now()
                )
                """
            ),
            {"user_id": user_id},
        )
        session.commit()
