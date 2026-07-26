from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.learning_brain.service import LearningBrainService
from app.main import create_app


NOW = datetime(2026, 7, 26, 8, tzinfo=UTC)


def test_calibration_groups_knowledge_points_and_refuses_low_sample_conclusions(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="kp-token")
    # 样本充足：12 题、9 对 -> accuracy 0.75
    _seed_tagged_practice(
        db_session_factory,
        user_id=user_id,
        module_name="资料分析",
        tag_value="基期量计算",
        total=12,
        correct=9,
        id_prefix="jiqi",
    )
    # 样本不足：3 题、1 对 -> lowSample 且不给 accuracy
    _seed_tagged_practice(
        db_session_factory,
        user_id=user_id,
        module_name="资料分析",
        tag_value="增长率比较",
        total=3,
        correct=1,
        id_prefix="zengzhang",
    )
    # 无知识点标签 -> 显式归「未分类」
    _seed_tagged_practice(
        db_session_factory,
        user_id=user_id,
        module_name="资料分析",
        tag_value=None,
        total=2,
        correct=0,
        id_prefix="untagged",
    )
    client = _make_client(db_session_factory)

    response = client.get(
        "/api/v1/calibration/ability",
        headers={"Authorization": "Bearer kp-token"},
    )

    assert response.status_code == 200
    body = response.json()
    estimates = {
        (item["moduleName"], item["tagValue"]): item
        for item in body["knowledgePointEstimates"]
    }

    sufficient = estimates[("资料分析", "基期量计算")]
    assert sufficient["sampleSize"] == 12
    assert sufficient["lowSample"] is False
    assert sufficient["accuracy"] == 0.75

    low_sample = estimates[("资料分析", "增长率比较")]
    assert low_sample["sampleSize"] == 3
    assert low_sample["lowSample"] is True
    assert low_sample["accuracy"] is None

    unclassified = estimates[("资料分析", "未分类")]
    assert unclassified["sampleSize"] == 2
    assert unclassified["lowSample"] is True
    assert unclassified["accuracy"] is None

    # 既有契约不受影响：模块级估计仍按 module_name 分组输出。
    module_names = {item["moduleName"] for item in body["moduleEstimates"]}
    assert "资料分析" in module_names


def test_learning_brain_projects_top_knowledge_point_weaknesses_per_module(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(db_session_factory)
    # 同一模块四个样本充足知识点：弱项 Top 只保留正确率最低的 3 个。
    for tag_value, correct, prefix in (
        ("基期量计算", 3, "kp_a"),
        ("增长率比较", 5, "kp_b"),
        ("比重变化", 6, "kp_c"),
        ("平均数增长", 8, "kp_d"),
    ):
        _seed_tagged_practice(
            db_session_factory,
            user_id=user_id,
            module_name="资料分析",
            tag_value=tag_value,
            total=10,
            correct=correct,
            id_prefix=prefix,
        )
    # 样本不足的知识点即使全错也不进榜。
    _seed_tagged_practice(
        db_session_factory,
        user_id=user_id,
        module_name="资料分析",
        tag_value="隔年增长",
        total=3,
        correct=0,
        id_prefix="kp_low",
    )
    # 无标签题全错，归「未分类」，不进知识点榜。
    _seed_tagged_practice(
        db_session_factory,
        user_id=user_id,
        module_name="资料分析",
        tag_value=None,
        total=12,
        correct=0,
        id_prefix="kp_untagged",
    )
    service = LearningBrainService()

    with db_session_factory.begin() as session:
        first = service.get_or_create_profile(session, user_id=user_id, now=NOW)
    with db_session_factory.begin() as session:
        replay = service.get_or_create_profile(
            session,
            user_id=user_id,
            now=NOW + timedelta(hours=1),
        )

    # 只读投影不改变画像版本与指纹（不动既有契约、不churn版本）。
    assert first.profile_version_id == replay.profile_version_id
    assert first.input_fingerprint == replay.input_fingerprint

    weaknesses = first.knowledge_point_weaknesses
    assert weaknesses is not None
    assert [item.tag_value for item in weaknesses] == [
        "基期量计算",
        "增长率比较",
        "比重变化",
    ]
    assert all(item.module_name == "资料分析" for item in weaknesses)
    assert all(item.sample_size >= 10 for item in weaknesses)
    assert [item.accuracy for item in weaknesses] == [0.3, 0.5, 0.6]
    tag_values = {item.tag_value for item in weaknesses}
    assert "隔年增长" not in tag_values
    assert "未分类" not in tag_values
    assert replay.knowledge_point_weaknesses == weaknesses

    # 字段以可选的 camelCase 别名对外，老客户端可安全忽略。
    payload = first.model_dump(by_alias=True, mode="json")
    assert payload["knowledgePointWeaknesses"][0]["tagValue"] == "基期量计算"


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
                values (cast(:user_id as uuid), '知识点学习者')
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


def _seed_user_and_release(db_session_factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values (cast(:user_id as uuid), '知识点学习者')
                """
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  :release_id, 'guokao', 2026, 'kp-rules',
                  'kp-positions', 'active', :activated_at
                )
                """
            ),
            {"release_id": str(uuid4()), "activated_at": NOW},
        )
        session.commit()
    return user_id


def _seed_tagged_practice(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
    module_name: str,
    tag_value: str | None,
    total: int,
    correct: int,
    id_prefix: str,
) -> None:
    """给用户造 total 道题的练习记录，前 correct 道答对。

    tag_value 为 None 时不写 question_tags，用于覆盖
    「无标签题归未分类」的分组分支。
    """
    with db_session_factory() as session:
        for index in range(total):
            question_id = f"q_{id_prefix}_{index:03d}"
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
                      '知识点',
                      :stem,
                      'B',
                      false,
                      2026,
                      1
                    )
                    """
                ),
                {
                    "question_id": question_id,
                    "module_name": module_name,
                    "stem": f"{module_name} {id_prefix} 第 {index} 题",
                },
            )
            if tag_value is not None:
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
            is_correct = index < correct
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
                      'kp-practice',
                      :question_id,
                      :selected_answer,
                      'B',
                      :correct,
                      now()
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "question_id": question_id,
                    "selected_answer": "B" if is_correct else "A",
                    "correct": is_correct,
                },
            )
        session.commit()
