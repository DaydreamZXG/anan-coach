from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.taskstream.service import TODAY_XINGCE_TASK_ID
from app.main import create_app


def test_health_contract() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "gongkao-dazi"


def test_bootstrap_contract(db_session_factory: sessionmaker[Session]) -> None:
    _seed_product_counts(db_session_factory)
    client = _make_db_client(db_session_factory)

    response = client.get("/api/v1/bootstrap")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["app"]["name"] == "考公搭子"
    assert body["app"]["bundleId"] == "com.xingguoxian.gongkaodazi"
    assert body["features"]["ipadOptimized"] is True
    assert body["questionBank"] == {
        "canonicalQuestions": 3,
        "objectiveQuestions": 2,
        "essayQuestions": 1,
        "explanations": 2,
        "knowledgeChunks": 3,
    }
    assert body["navigation"]["firstLevelSections"] == [
        {"id": "today", "title": "今日"},
        {"id": "workbench", "title": "工作台"},
        {"id": "profile", "title": "我的"},
    ]
    assert "providerBaseUrl" not in body["ai"]
    assert "model" not in body["ai"]
    assert body["iap"]["products"] == [
        "gongkao_ai_pro_monthly",
        "gongkao_ai_pro_yearly",
    ]


def test_bootstrap_reports_zero_when_positions_table_is_not_installed(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_product_counts(db_session_factory)
    _seed_authenticated_user(db_session_factory)
    with db_session_factory() as session:
        session.execute(text("drop table product.positions cascade"))
        session.commit()
    client = _make_db_client(db_session_factory)

    response = client.get(
        "/api/v1/workbench/preview",
        headers={"Authorization": "Bearer workbench-token"},
    )
    assert response.status_code == 200
    assert response.json()["sourceBase"]["positionRecords"] == 0

    bootstrap = client.get("/api/v1/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["questionBank"]["canonicalQuestions"] == 3


def test_workbench_preview_requires_bearer() -> None:
    response = TestClient(create_app()).get("/api/v1/workbench/preview")
    assert response.status_code == 401


def test_workbench_preview_empty_sections_for_empty_learner_data(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_product_counts(db_session_factory)
    _seed_authenticated_user(db_session_factory)
    client = _make_db_client(db_session_factory)

    response = client.get(
        "/api/v1/workbench/preview",
        headers={"Authorization": "Bearer workbench-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sourceBase"]["openWebResearch"] is False
    assert body["sourceBase"]["canonicalQuestions"] == 3
    assert body["sourceBase"]["knowledgeChunks"] == 3
    assert body["sourceBase"]["positionRecords"] == 5
    assert body["sections"] == []


def test_workbench_preview_uses_real_learner_objects(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_product_counts(db_session_factory)
    user_id = _seed_authenticated_user(db_session_factory)
    _seed_learner_activity(db_session_factory, user_id=user_id)
    _seed_docket_activity(db_session_factory, user_id=user_id)
    client = _make_db_client(db_session_factory)

    response = client.get(
        "/api/v1/workbench/preview",
        headers={"Authorization": "Bearer workbench-token"},
    )

    assert response.status_code == 200
    body = response.json()

    assert [section["title"] for section in body["sections"]] == [
        "卷宗复盘",
        "行测复盘",
        "申论复盘",
    ]

    docket = body["sections"][0]["objects"][0]
    assert docket["type"] == "docket_review"
    assert docket["sourceCount"] == 2
    assert docket["payload"]["wrongCount"] == 1
    assert docket["payload"]["questionIds"] == ["cq_docket_2", "cq_docket_1"]

    practice = body["sections"][1]["objects"][0]
    assert practice["type"] == "practice_review"
    assert practice["sourceCount"] == 2
    assert practice["payload"]["wrongCount"] == 1
    assert practice["payload"]["questionIds"] == ["cq_2", "cq_1"]

    essay = body["sections"][2]["objects"][0]
    assert essay["type"] == "essay_review"
    assert essay["sourceCount"] == 2
    assert essay["payload"]["attemptId"] == "er_real"
    assert essay["payload"]["hasRewrite"] is True


def _make_db_client(db_session_factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    return TestClient(app)


def _seed_product_counts(db_session_factory: sessionmaker[Session]) -> None:
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into product.questions (question_id, status, domain) values
                  ('cq_obj_1', 'active', 'objective'),
                  ('cq_obj_2', 'active', 'objective'),
                  ('cq_essay_1', 'active', 'essay'),
                  ('cq_retired_1', 'retired', 'objective')
                """
            )
        )
        session.execute(
            text(
                """
                insert into product.question_explanations (question_id) values
                  ('cq_obj_1'),
                  ('cq_obj_2')
                """
            )
        )
        session.execute(
            text(
                """
                insert into product.knowledge_chunks (knowledge_chunk_id) values
                  ('kc_1'),
                  ('kc_2'),
                  ('kc_3')
                """
            )
        )
        session.execute(
            text(
                """
                insert into product.position_systems (
                  system_code,
                  name_zh,
                  paper_type,
                  is_big_six
                ) values (
                  'tax',
                  '国家税务总局',
                  'administrativeLaw',
                  true
                )
                on conflict (system_code) do nothing
                """
            )
        )
        session.execute(
            text(
                """
                insert into product.positions (
                  source_id,
                  year,
                  position_code,
                  name,
                  department_full,
                  system_code,
                  region,
                  paper_type,
                  headcount
                ) values
                  ('pos_1', 2026, '300110001001', '一级行政执法员', '国家税务总局测试局1', 'tax', '北京', 'administrativeLaw', 1),
                  ('pos_2', 2026, '300110001002', '一级行政执法员', '国家税务总局测试局2', 'tax', '北京', 'administrativeLaw', 1),
                  ('pos_3', 2026, '300110001003', '一级行政执法员', '国家税务总局测试局3', 'tax', '北京', 'administrativeLaw', 1),
                  ('pos_4', 2026, '300110001004', '一级行政执法员', '国家税务总局测试局4', 'tax', '北京', 'administrativeLaw', 1),
                  ('pos_5', 2026, '300110001005', '一级行政执法员', '国家税务总局测试局5', 'tax', '北京', 'administrativeLaw', 1)
                """
            )
        )
        session.commit()


def _seed_authenticated_user(db_session_factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name, email)
                values (cast(:user_id as uuid), '学习者', 'learner@example.com')
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
                "session_token_hash": token_hash_for_tests("workbench-token"),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        session.commit()
    return user_id


def _seed_learner_activity(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
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
                ) values
                  (
                    gen_random_uuid(),
                    cast(:user_id as uuid),
                    :today_xingce_task_id,
                    'cq_1',
                    'A',
                    'B',
                    false,
                    now() - interval '1 minute'
                  ),
                  (
                    gen_random_uuid(),
                    cast(:user_id as uuid),
                    :today_xingce_task_id,
                    'cq_2',
                    'B',
                    'B',
                    true,
                    now()
                  )
                """
            ),
            {"user_id": user_id, "today_xingce_task_id": TODAY_XINGCE_TASK_ID},
        )
        session.execute(
            text(
                """
                insert into learner.essay_attempts (
                  attempt_id,
                  learner_key,
                  task_id,
                  original_answer,
                  review_payload,
                  rewritten_answer,
                  created_at,
                  updated_at
                ) values (
                  'er_real',
                  :learner_key,
                  'today-shenlun-summary-rewrite',
                  '平台分散，反馈不足。',
                  cast(:review_payload as jsonb),
                  '平台分散、权责不清、反馈不足。',
                  now(),
                  now()
                )
                """
            ),
            {
                "learner_key": user_id,
                "review_payload": (
                    '{"dimensions":[{"name":"要点完整度","score":3}],'
                    '"missingPoints":["权责边界不清"]}'
                ),
            },
        )
        session.commit()


def _seed_docket_activity(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
) -> None:
    docket_id = str(uuid4())
    card_one = str(uuid4())
    card_two = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into product.questions (question_id, status, domain) values
                  ('cq_docket_1', 'active', 'objective'),
                  ('cq_docket_2', 'active', 'objective')
                """
            )
        )
        session.execute(
            text(
                """
                insert into learner.dockets (
                  docket_id,
                  user_id,
                  docket_date,
                  status,
                  created_at,
                  updated_at
                ) values (
                  cast(:docket_id as uuid),
                  cast(:user_id as uuid),
                  current_date,
                  'active',
                  now(),
                  now()
                )
                """
            ),
            {"docket_id": docket_id, "user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.docket_cards (
                  card_id,
                  docket_id,
                  question_id,
                  type,
                  track,
                  module_name,
                  world_label,
                  title,
                  narrative,
                  interaction,
                  source_grounding,
                  status,
                  sort_order,
                  payload,
                  created_at,
                  updated_at
                ) values
                  (
                    cast(:card_one as uuid),
                    cast(:docket_id as uuid),
                    'cq_docket_1',
                    'daily_affair',
                    'xingce',
                    '言语理解与表达',
                    '校阅诏令文义',
                    '诏令释义',
                    '文书句读暗藏机锋。',
                    'swipe_verdict',
                    'product.questions',
                    'completed',
                    1,
                    '{}',
                    now(),
                    now()
                  ),
                  (
                    cast(:card_two as uuid),
                    cast(:docket_id as uuid),
                    'cq_docket_2',
                    'daily_affair',
                    'xingce',
                    '判断推理',
                    '堪破州府冤案',
                    '州府疑案',
                    '刑名卷宗互有牵连。',
                    'swipe_verdict',
                    'product.questions',
                    'completed',
                    2,
                    '{}',
                    now(),
                    now()
                  )
                """
            ),
            {
                "docket_id": docket_id,
                "card_one": card_one,
                "card_two": card_two,
            },
        )
        session.execute(
            text(
                """
                insert into learner.verdicts (
                  verdict_id,
                  user_id,
                  docket_id,
                  card_id,
                  question_id,
                  selected_answer,
                  correct,
                  elapsed_seconds,
                  created_at
                ) values
                  (
                    gen_random_uuid(),
                    cast(:user_id as uuid),
                    cast(:docket_id as uuid),
                    cast(:card_one as uuid),
                    'cq_docket_1',
                    'A',
                    false,
                    16,
                    now() - interval '1 minute'
                  ),
                  (
                    gen_random_uuid(),
                    cast(:user_id as uuid),
                    cast(:docket_id as uuid),
                    cast(:card_two as uuid),
                    'cq_docket_2',
                    'B',
                    true,
                    19,
                    now()
                  )
                """
            ),
            {
                "user_id": user_id,
                "docket_id": docket_id,
                "card_one": card_one,
                "card_two": card_two,
            },
        )
        session.commit()
