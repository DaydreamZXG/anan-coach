from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.learning_brain.service import LearningBrainService
from app.main import create_app
from tests.position_release_helpers import seed_active_position_release


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
        (item["moduleName"], item["tagValue"]): item for item in body["knowledgePointEstimates"]
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
        seed_active_position_release(
            session,
            activated_at=NOW,
            database_now=datetime.now(UTC),
            rule_set_version="kp-rules",
            position_data_version="kp-positions",
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
    """给用户造 total 道题的行测评估事实，前 correct 道答对。

    tag_value 为 None 时不写 question_tags，用于覆盖
    「无标签题归未分类」的分组分支。
    """
    assessment_rows: list[tuple[str, bool]] = []
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
            assessment_rows.append((question_id, is_correct))
        session.commit()
    _seed_xingce_assessments(
        db_session_factory,
        user_id=user_id,
        id_prefix=id_prefix,
        rows=assessment_rows,
    )


def _seed_xingce_assessments(
    db_session_factory: sessionmaker[Session],
    *,
    user_id: str,
    id_prefix: str,
    rows: list[tuple[str, bool]],
) -> None:
    local_date = NOW.date()
    recorded_at = NOW
    with db_session_factory.begin() as session:
        preference_id = session.execute(
            text(
                """
                select preference_version_id
                from learning.weekly_study_preference_versions
                where user_id = cast(:user_id as uuid)
                order by version_no desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        if preference_id is None:
            preference_id = str(uuid4())
            session.execute(
                text(
                    """
                    insert into learning.weekly_study_preference_versions (
                      preference_version_id, command_id, user_id, version_no,
                      timezone, weekday_minutes, weekend_minutes,
                      long_block_minutes, fragment_minutes,
                      regular_rest_weekday, regular_mock_weekday,
                      effective_from, source_type, confirmed,
                      input_fingerprint, supersedes_preference_version_id,
                      created_at
                    ) values (
                      cast(:preference_id as uuid), :command_id,
                      cast(:user_id as uuid), 1, 'Asia/Shanghai',
                      120, 120, 60, 30, 7, 6, :local_date,
                      'system_default', false, :input_fingerprint, null,
                      :recorded_at
                    )
                    """
                ),
                {
                    "preference_id": preference_id,
                    "command_id": f"kp-preference:{user_id}",
                    "user_id": user_id,
                    "local_date": local_date,
                    "input_fingerprint": _hash("preference", user_id),
                    "recorded_at": recorded_at,
                },
            )
        capacity_id = session.execute(
            text(
                """
                select capacity_version_id
                from learning.daily_capacity_versions
                where user_id = cast(:user_id as uuid)
                  and local_date = :local_date
                order by version_no desc
                limit 1
                """
            ),
            {"user_id": user_id, "local_date": local_date},
        ).scalar_one_or_none()
        if capacity_id is None:
            capacity_id = str(uuid4())
            session.execute(
                text(
                    """
                    insert into learning.daily_capacity_versions (
                      capacity_version_id, command_id, user_id, local_date,
                      version_no, available_minutes, energy_level,
                      stress_level, confidence_level, confirmed, source_type,
                      weekly_preference_version_id, input_fingerprint,
                      supersedes_capacity_version_id, recorded_at
                    ) values (
                      cast(:capacity_id as uuid), :command_id,
                      cast(:user_id as uuid), :local_date, 1, 120, 3, 3, 3,
                      false, 'weekly_preference',
                      cast(:preference_id as uuid), :input_fingerprint,
                      null, :recorded_at
                    )
                    """
                ),
                {
                    "capacity_id": capacity_id,
                    "command_id": f"kp-capacity:{user_id}",
                    "user_id": user_id,
                    "local_date": local_date,
                    "preference_id": str(preference_id),
                    "input_fingerprint": _hash("capacity", user_id),
                    "recorded_at": recorded_at,
                },
            )

        previous_plan = (
            session.execute(
                text(
                    """
                    select plan_version_id, version_no
                    from learning.daily_plan_versions plan
                    where plan.user_id = cast(:user_id as uuid)
                      and plan.local_date = :local_date
                      and not exists (
                        select 1
                        from learning.daily_plan_versions successor
                        where successor.supersedes_plan_version_id =
                              plan.plan_version_id
                      )
                    order by plan.version_no desc
                    limit 1
                    """
                ),
                {"user_id": user_id, "local_date": local_date},
            )
            .mappings()
            .one_or_none()
        )
        plan_id = str(uuid4())
        plan_version_no = int(previous_plan["version_no"]) + 1 if previous_plan is not None else 1
        session.execute(
            text(
                """
                insert into learning.daily_plan_versions (
                  plan_version_id, command_id, user_id, local_date, version_no,
                  capacity_version_id, weekly_preference_version_id, day_mode,
                  headline, rationale, goal_gap, scheduled_minutes,
                  due_reflection_count, due_verification_count,
                  track_explanation, input_versions, evidence_refs,
                  invalidation_conditions, replan_reason_codes, plan_basis,
                  contract_version, algorithm_version, input_fingerprint,
                  supersedes_plan_version_id, generated_at, valid_until
                ) values (
                  cast(:plan_id as uuid), :command_id,
                  cast(:user_id as uuid), :local_date, :version_no,
                  cast(:capacity_id as uuid), cast(:preference_id as uuid),
                  'normal', '知识点校准事实', '测试不可变评估事实聚合',
                  '{}'::jsonb, 20, 0, 0, '{}'::jsonb, '{}'::jsonb,
                  '[{"owner":"calibration-test"}]'::jsonb,
                  '["assessment_recorded"]'::jsonb, '[]'::jsonb,
                  'evidence_priority_v2', 'daily-plan@v2',
                  'calibration-fixture@v1', :input_fingerprint,
                  cast(:supersedes_plan_id as uuid),
                  :recorded_at, :valid_until
                )
                """
            ),
            {
                "plan_id": plan_id,
                "command_id": f"kp-plan:{user_id}:{id_prefix}",
                "user_id": user_id,
                "local_date": local_date,
                "version_no": plan_version_no,
                "capacity_id": str(capacity_id),
                "preference_id": str(preference_id),
                "input_fingerprint": _hash("plan", user_id, id_prefix),
                "supersedes_plan_id": (
                    str(previous_plan["plan_version_id"]) if previous_plan is not None else None
                ),
                "recorded_at": recorded_at,
                "valid_until": recorded_at + timedelta(days=1),
            },
        )
        task_id = str(uuid4())
        work_item_key = _hash("work-item", user_id, id_prefix)
        session.execute(
            text(
                """
                insert into learning.daily_plan_tasks (
                  task_id, plan_version_id, user_id, local_date,
                  plan_version_no, candidate_key, work_item_key, task_order,
                  track, stage, kind, title, reason, estimated_minutes,
                  mandatory, start_url, priority_score, goal_gain,
                  ability_gain, verification_urgency, time_fit, load_penalty,
                  track_balance_bonus, evidence_refs, completion_contract,
                  input_fingerprint, created_at
                ) values (
                  cast(:task_id as uuid), cast(:plan_id as uuid),
                  cast(:user_id as uuid), :local_date, :plan_version_no,
                  :candidate_key, :work_item_key, 1, 'xingce',
                  'objective_practice', 'practice', '知识点校准训练',
                  '形成不可变行测评估事实', 20, true,
                  '/pages/xingce-task-intro/index?mode=targeted',
                  0.8, 0.6, 0.8, 0.5, 1.0, 0.1, 0,
                  '[{"owner":"calibration-test"}]'::jsonb,
                  '{}'::jsonb, :input_fingerprint, :recorded_at
                )
                """
            ),
            {
                "task_id": task_id,
                "plan_id": plan_id,
                "user_id": user_id,
                "local_date": local_date,
                "plan_version_no": plan_version_no,
                "candidate_key": f"kp:{id_prefix}",
                "work_item_key": work_item_key,
                "input_fingerprint": _hash("task", user_id, id_prefix),
                "recorded_at": recorded_at,
            },
        )

        session_id = str(uuid4())
        start_command_id = f"kp-start:{user_id}:{id_prefix}"
        submit_command_id = f"kp-submit:{user_id}:{id_prefix}"
        for command_id, command_type in (
            (start_command_id, "session_start"),
            (submit_command_id, "attempt_submit"),
        ):
            session.execute(
                text(
                    """
                    insert into learning.xingce_commands (
                      user_id, command_id, command_type, target_id,
                      request_fingerprint, response_payload, created_at
                    ) values (
                      cast(:user_id as uuid), :command_id, :command_type,
                      cast(:session_id as uuid), :request_fingerprint,
                      '{}'::jsonb, :recorded_at
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "command_id": command_id,
                    "command_type": command_type,
                    "session_id": session_id,
                    "request_fingerprint": _hash(command_id),
                    "recorded_at": recorded_at,
                },
            )
        attempt_group_id = str(uuid4())
        session.execute(
            text(
                """
                insert into learning.xingce_task_sessions (
                  session_id, user_id, source_plan_task_id,
                  source_plan_version_id, local_date, work_item_key, mode,
                  state, attempt_group_id, revision, bundle_version_id,
                  safe_bundle, scoring_bundle, current_question_id,
                  checkpoint, total_elapsed_seconds, created_at, updated_at,
                  submitted_at, completed_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid),
                  cast(:task_id as uuid), cast(:plan_id as uuid), :local_date,
                  :work_item_key, 'targeted', 'submitted',
                  cast(:attempt_group_id as uuid), 2, :bundle_version_id,
                  cast(:safe_bundle as jsonb), '{}'::jsonb,
                  :current_question_id, '{"answers":[]}'::jsonb, 60,
                  :recorded_at, :recorded_at, :recorded_at, null
                )
                """
            ),
            {
                "session_id": session_id,
                "user_id": user_id,
                "task_id": task_id,
                "plan_id": plan_id,
                "local_date": local_date,
                "work_item_key": work_item_key,
                "attempt_group_id": attempt_group_id,
                "bundle_version_id": _hash("bundle", user_id, id_prefix),
                "safe_bundle": json.dumps(
                    {
                        "questions": [
                            {"questionId": question_id} for question_id, _is_correct in rows
                        ]
                    }
                ),
                "current_question_id": rows[-1][0],
                "recorded_at": recorded_at,
            },
        )
        for index, (question_id, is_correct) in enumerate(rows):
            answer_version_id = str(uuid4())
            attempt_record_id = str(uuid4())
            content_version_id = _hash("content", question_id)
            payload = {
                "questionId": question_id,
                "selectedOptionIds": ["B" if is_correct else "A"],
            }
            session.execute(
                text(
                    """
                    insert into learning.xingce_answer_versions (
                      answer_version_id, command_id, user_id, session_id,
                      question_id, version_no, payload, payload_sha256,
                      supersedes_answer_version_id, created_at
                    ) values (
                      cast(:answer_version_id as uuid), :command_id,
                      cast(:user_id as uuid), cast(:session_id as uuid),
                      :question_id, 1, cast(:payload as jsonb),
                      :payload_sha256, null, :recorded_at
                    )
                    """
                ),
                {
                    "answer_version_id": answer_version_id,
                    "command_id": submit_command_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "payload": json.dumps(payload),
                    "payload_sha256": _hash(json.dumps(payload, sort_keys=True)),
                    "recorded_at": recorded_at,
                },
            )
            session.execute(
                text(
                    """
                    insert into learning.xingce_attempt_records (
                      attempt_record_id, attempt_group_id, item_attempt_id,
                      submit_command_id, user_id, session_id, question_id,
                      session_revision, answer_version_id, content_version_id,
                      scoring_policy_version, timing_evidence_ref,
                      timing_evidence, submission_hash, source_lineage_refs,
                      created_at
                    ) values (
                      cast(:attempt_record_id as uuid),
                      cast(:attempt_group_id as uuid), :item_attempt_id,
                      :submit_command_id, cast(:user_id as uuid),
                      cast(:session_id as uuid), :question_id, 2,
                      cast(:answer_version_id as uuid), :content_version_id,
                      'calibration-fixture@v1', :timing_evidence_ref,
                      '{}'::jsonb, :submission_hash,
                      '["plan","session","answer"]'::jsonb, :recorded_at
                    )
                    """
                ),
                {
                    "attempt_record_id": attempt_record_id,
                    "attempt_group_id": attempt_group_id,
                    "item_attempt_id": _hash("item-attempt", user_id, id_prefix, str(index)),
                    "submit_command_id": submit_command_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "answer_version_id": answer_version_id,
                    "content_version_id": content_version_id,
                    "timing_evidence_ref": _hash("timing", question_id),
                    "submission_hash": _hash("submission", user_id, question_id),
                    "recorded_at": recorded_at,
                },
            )
            session.execute(
                text(
                    """
                    insert into learning.xingce_assessments (
                      assessment_id, attempt_record_id, submit_command_id,
                      user_id, session_id, question_id, answer_version_id,
                      content_version_id, scoring_policy_version,
                      assessment_key_hash, result_code, correct,
                      result_snapshot, created_at
                    ) values (
                      gen_random_uuid(), cast(:attempt_record_id as uuid),
                      :submit_command_id, cast(:user_id as uuid),
                      cast(:session_id as uuid), :question_id,
                      cast(:answer_version_id as uuid), :content_version_id,
                      'calibration-fixture@v1', :assessment_key_hash,
                      :result_code, :correct, cast(:result_snapshot as jsonb),
                      :recorded_at
                    )
                    """
                ),
                {
                    "attempt_record_id": attempt_record_id,
                    "submit_command_id": submit_command_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "question_id": question_id,
                    "answer_version_id": answer_version_id,
                    "content_version_id": content_version_id,
                    "assessment_key_hash": _hash("assessment", user_id, id_prefix, str(index)),
                    "result_code": "correct" if is_correct else "incorrect",
                    "correct": is_correct,
                    "result_snapshot": json.dumps(
                        {"objectiveVerdict": ("correct" if is_correct else "incorrect")}
                    ),
                    "recorded_at": recorded_at,
                },
            )


def _hash(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()
