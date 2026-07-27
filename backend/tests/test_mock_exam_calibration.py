from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.guokao_xingce import GUOKAO_XINGCE_MODULES
from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog
from app.domains.mock_exam.repository import (
    KnowledgeMapReleaseSnapshot,
    MockExamRepository,
)
from app.domains.mock_exam.service import (
    GUOKAO_MOCK_BLUEPRINT,
    MOCK_KNOWLEDGE_LINEAGE_CONTRACT_VERSION,
)
from app.main import create_app
from tests.task_editing_helpers import claim_task_edit_headers


def test_weekly_mock_exam_full_loop_feeds_workbench_without_faking_score_calibration(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="mock-token")
    excluded_question_id = _seed_mock_qbank(db_session_factory)
    _seed_recent_answer(db_session_factory, user_id=user_id, question_id=excluded_question_id)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer mock-token"}

    created = client.post(
        "/api/v1/mock-exams/weekly",
        headers=auth,
    )

    assert created.status_code == 200
    exam = created.json()
    auth = {
        **auth,
        **claim_task_edit_headers(
            client,
            auth,
            resource_type="mock_exam_session",
            resource_id=exam["sessionId"],
            revision=exam["revision"],
        ),
    }
    assert exam["questionCount"] == 135
    question_ids = [item["questionId"] for item in exam["questions"]]
    assert excluded_question_id not in question_ids
    assert exam["durationMinutes"] == 120
    assert (
        exam["sourceGrounding"] == "product.knowledge_map_question_bindings + "
        "product.question_content_versions + learner.mock_exam_sessions"
    )

    answers = [
        {
            "questionId": question_id,
            "selectedAnswer": "A" if index < 2 else "B",
        }
        for index, question_id in enumerate(question_ids)
    ]
    submitted = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers=auth,
        json={"expectedRevision": exam["revision"], "answers": answers},
    )

    assert submitted.status_code == 200
    review = submitted.json()["review"]
    assert review["estimatedScore"] == 98.5
    assert review["performanceIndex"] == 98.5
    assert review["metricName"] == "question_weighted_performance_index"
    assert review["scoringPolicyVersionId"] == ("mock-question-weighted-performance-index-v2")
    assert review["performanceRange"]["lower"] < 98.5
    assert review["performanceRange"]["upper"] > 98.5
    assert review["viewed"] is False
    assert "不是国考原始分" in review["estimateSource"]
    assert review["wrongClusters"][0]["methodCardIds"] == ["mc_mock"]
    assert "上周模考" in review["nextWeekReason"]

    calibration = client.get(
        "/api/v1/calibration/ability",
        headers={"Authorization": "Bearer mock-token"},
    )
    assert calibration.status_code == 200
    ability = calibration.json()
    assert ability["sources"]["mockExams"] == 0
    assert ability["xingceScore"] != 98.0

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
    assert body["calibration"]["confidence"] == "low"
    assert body["tasks"][0]["kind"] == "xingce_practice"
    assert "上周模考" in body["rationale"]


def test_mock_exam_submission_requires_all_assigned_questions(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-incomplete-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer mock-incomplete-token"}

    created = client.post(
        "/api/v1/mock-exams/weekly",
        headers=auth,
    )
    assert created.status_code == 200
    exam = created.json()
    auth = {
        **auth,
        **claim_task_edit_headers(
            client,
            auth,
            resource_type="mock_exam_session",
            resource_id=exam["sessionId"],
            revision=exam["revision"],
        ),
    }
    first_question = exam["questions"][0]["questionId"]

    submitted = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers=auth,
        json={
            "expectedRevision": exam["revision"],
            "answers": [{"questionId": first_question, "selectedAnswer": "B"}],
        },
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


def test_mock_exam_knowledge_reads_ignore_historical_release_with_same_nodes(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="mock-release-history-token")
    _seed_mock_qbank(db_session_factory)
    copied_release_id = _publish_copy_of_active_knowledge_map_release(db_session_factory)

    with db_session_factory() as session:
        repository = MockExamRepository(session)
        release = repository.resolve_fresh_active_knowledge_release()
        assert release is not None
        assert release.release_id == copied_release_id
        selected_ids = repository.select_module_question_ids(
            user_id=user_id,
            module_name="政治理论",
            module_code="political_theory",
            limit=20,
            knowledge_release=release,
        )
        wrong_clusters = repository.get_wrong_clusters(
            wrong_question_ids=["q_zhengzhi_000"],
            knowledge_release=release,
        )

    assert len(selected_ids) == 20
    assert len(set(selected_ids)) == 20
    assert len(wrong_clusters) == 1
    assert wrong_clusters[0]["node_id"] == "km_zhengzhi"
    assert int(wrong_clusters[0]["wrong_count"]) == 1


def test_mock_exam_knowledge_reads_fail_closed_when_active_release_is_stale(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-stale-release-token")
    _seed_mock_qbank(
        db_session_factory,
        expire_after_activation=True,
    )

    with db_session_factory() as session:
        session.execute(
            text(
                """
                select pg_sleep(
                  (
                    greatest(
                      extract(
                        epoch from fresh_until - statement_timestamp()
                      ),
                      0
                    ) + 0.05
                  )::double precision
                )
                from product.knowledge_map_releases
                where exam_scope = 'guokao'
                  and track = 'xingce'
                  and status = 'active'
                """
            )
        )
        repository = MockExamRepository(session)

        assert repository.resolve_fresh_active_knowledge_release() is None


def test_mock_question_module_alias_comes_from_frozen_release_vocab(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-vocab-alias-token")
    _seed_mock_qbank(
        db_session_factory,
        module_aliases=(("历史词表言语别名", "verbal_comprehension"),),
    )
    with db_session_factory() as session:
        session.execute(
            text(
                """
                update product.questions
                set module_name = '历史词表言语别名'
                where question_id = 'q_yanyu_000'
                """
            )
        )
        session.commit()

    client = _make_client(db_session_factory)
    created = client.post(
        "/api/v1/mock-exams/weekly",
        headers={"Authorization": "Bearer mock-vocab-alias-token"},
    )

    assert created.status_code == 200
    question = next(
        item for item in created.json()["questions"] if item["questionId"] == "q_yanyu_000"
    )
    assert question["moduleName"] == "言语理解与表达"
    with db_session_factory() as session:
        metadata = session.execute(
            text(
                """
                select paper.equivalence_metadata
                from learner.mock_exam_sessions mock_session
                join product.mock_exam_paper_versions paper
                  on paper.paper_version_id = mock_session.paper_version_id
                where mock_session.session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": created.json()["sessionId"]},
        ).scalar_one()
    assert metadata["knowledgeMapRelease"]["source"]["vocabVersion"] == "mock-vocab@1"


def test_mock_assembly_and_review_freeze_release_across_publish_switch(
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_user(db_session_factory, token="mock-release-switch-token")
    _seed_mock_qbank(db_session_factory)
    with db_session_factory() as session:
        assert session.execute(text("show transaction_isolation")).scalar_one() == "read committed"
        initial_release = MockExamRepository(session).resolve_fresh_active_knowledge_release()
        assert initial_release is not None

    selected_release_ids: list[str] = []
    replacement_release_id: str | None = None
    original_select = MockExamRepository.select_module_question_ids

    def select_then_publish(
        repository: MockExamRepository,
        *,
        user_id: str,
        module_name: str,
        module_code: str,
        limit: int,
        knowledge_release: KnowledgeMapReleaseSnapshot,
    ) -> list[str]:
        nonlocal replacement_release_id
        selected_release_ids.append(knowledge_release.release_id)
        result = original_select(
            repository,
            user_id=user_id,
            module_name=module_name,
            module_code=module_code,
            limit=limit,
            knowledge_release=knowledge_release,
        )
        if replacement_release_id is None:
            replacement_release_id = _publish_sparse_replacement_release(db_session_factory)
        return result

    monkeypatch.setattr(
        MockExamRepository,
        "select_module_question_ids",
        select_then_publish,
    )
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer mock-release-switch-token"}
    created = client.post("/api/v1/mock-exams/weekly", headers=auth)

    assert created.status_code == 200
    exam = created.json()
    assert replacement_release_id is not None
    assert selected_release_ids == [initial_release.release_id] * 6
    with db_session_factory() as session:
        paper_metadata, active_release_id = session.execute(
            text(
                """
                select
                  paper.equivalence_metadata,
                  (
                    select release_id::text
                    from product.knowledge_map_releases
                    where exam_scope = 'guokao'
                      and track = 'xingce'
                      and status = 'active'
                  )
                from learner.mock_exam_sessions mock_session
                join product.mock_exam_paper_versions paper
                  on paper.paper_version_id = mock_session.paper_version_id
                where mock_session.session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": exam["sessionId"]},
        ).one()
    assert paper_metadata["knowledgeMapRelease"]["releaseId"] == initial_release.release_id
    assert active_release_id == replacement_release_id

    edit_headers = claim_task_edit_headers(
        client,
        auth,
        resource_type="mock_exam_session",
        resource_id=exam["sessionId"],
        revision=exam["revision"],
    )
    answers = [
        {
            "questionId": item["questionId"],
            "selectedAnswer": "A" if index == 0 else "B",
        }
        for index, item in enumerate(exam["questions"])
    ]
    submitted = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers={**auth, **edit_headers},
        json={"expectedRevision": exam["revision"], "answers": answers},
    )

    assert submitted.status_code == 200
    assert submitted.json()["review"]["wrongClusters"] == [
        {
            "nodeId": "km_zhengzhi",
            "displayPath": "行测/政治理论/高频政治理论",
            "wrongCount": 1,
            "methodCardIds": ["mc_mock"],
            "fixAction": "先复盘本 cluster，再做同类题。",
        }
    ]


def test_legacy_mock_without_lineage_completes_without_current_release_enrichment(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-legacy-lineage-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer mock-legacy-lineage-token"}
    created = client.post("/api/v1/mock-exams/weekly", headers=auth)
    assert created.status_code == 200
    exam = created.json()
    edit_headers = claim_task_edit_headers(
        client,
        auth,
        resource_type="mock_exam_session",
        resource_id=exam["sessionId"],
        revision=exam["revision"],
    )
    legacy_paper_id = _replace_session_paper_metadata(
        db_session_factory,
        session_id=exam["sessionId"],
        equivalence_metadata={},
    )
    replacement_release_id = _publish_sparse_replacement_release(db_session_factory)

    answers = [
        {
            "questionId": item["questionId"],
            "selectedAnswer": "A" if index == 0 else "B",
        }
        for index, item in enumerate(exam["questions"])
    ]
    submitted = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers={**auth, **edit_headers},
        json={"expectedRevision": exam["revision"], "answers": answers},
    )

    assert submitted.status_code == 200
    review = submitted.json()["review"]
    assert review["paperVersionId"] == legacy_paper_id
    assert review["wrongClusters"] == []
    with db_session_factory() as session:
        metadata, active_release_id = session.execute(
            text(
                """
                select
                  paper.equivalence_metadata,
                  (
                    select release_id::text
                    from product.knowledge_map_releases
                    where status = 'active'
                      and exam_scope = 'guokao'
                      and track = 'xingce'
                  )
                from product.mock_exam_paper_versions paper
                where paper.paper_version_id = cast(:paper_version_id as uuid)
                """
            ),
            {"paper_version_id": legacy_paper_id},
        ).one()
    assert metadata == {}
    assert active_release_id == replacement_release_id


def test_mock_new_lineage_contract_fails_closed_when_snapshot_is_incomplete(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-invalid-lineage-token")
    _seed_mock_qbank(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer mock-invalid-lineage-token"}
    created = client.post("/api/v1/mock-exams/weekly", headers=auth)
    assert created.status_code == 200
    exam = created.json()
    edit_headers = claim_task_edit_headers(
        client,
        auth,
        resource_type="mock_exam_session",
        resource_id=exam["sessionId"],
        revision=exam["revision"],
    )
    with db_session_factory() as session:
        original_metadata = session.execute(
            text(
                """
                select equivalence_metadata
                from product.mock_exam_paper_versions
                where paper_version_id = cast(:paper_version_id as uuid)
                """
            ),
            {"paper_version_id": exam["paperVersionId"]},
        ).scalar_one()
    _replace_session_paper_metadata(
        db_session_factory,
        session_id=exam["sessionId"],
        equivalence_metadata={
            "knowledgeMapLineageContractVersion": (MOCK_KNOWLEDGE_LINEAGE_CONTRACT_VERSION),
            "knowledgeMapRelease": {"releaseId": str(uuid4())},
        },
    )
    submitted = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers={**auth, **edit_headers},
        json={
            "expectedRevision": exam["revision"],
            "answers": [
                {"questionId": item["questionId"], "selectedAnswer": "B"}
                for item in exam["questions"]
            ],
        },
    )

    assert submitted.status_code == 409
    assert submitted.json()["code"] == "mock_knowledge_lineage_invalid"

    inconsistent_metadata = json.loads(json.dumps(original_metadata, ensure_ascii=False))
    inconsistent_metadata["knowledgeMapRelease"]["source"]["dataVersion"] = (
        "tampered-source-version"
    )
    _replace_session_paper_metadata(
        db_session_factory,
        session_id=exam["sessionId"],
        equivalence_metadata=inconsistent_metadata,
    )
    inconsistent = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers={**auth, **edit_headers},
        json={
            "expectedRevision": exam["revision"],
            "answers": [
                {"questionId": item["questionId"], "selectedAnswer": "B"}
                for item in exam["questions"]
            ],
        },
    )
    assert inconsistent.status_code == 409
    assert inconsistent.json()["code"] == "mock_knowledge_lineage_invalid"

    with db_session_factory() as session:
        status_value = session.execute(
            text(
                """
                select status
                from learner.mock_exam_sessions
                where session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": exam["sessionId"]},
        ).scalar_one()
    assert status_value == "assigned"


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


def _seed_mock_qbank(
    db_session_factory: sessionmaker[Session],
    *,
    fresh_until: datetime | None = None,
    expire_after_activation: bool = False,
    module_aliases: tuple[tuple[str, str], ...] = (),
) -> str:
    module_slugs = {
        "政治理论": "zhengzhi",
        "常识判断": "changshi",
        "言语理解与表达": "yanyu",
        "数量关系": "shuliang",
        "判断推理": "panduan",
        "资料分析": "ziliao",
    }
    excluded_question_id = "q_changshi_extra_000"
    with db_session_factory() as session:
        knowledge_map_release_id = str(uuid4())
        session.execute(
            text(
                """
                insert into product.vocab_module_alias_map (
                  version,
                  raw_module_name,
                  action,
                  canonical_code,
                  local_question_count,
                  basis
                ) values (
                  'mock-vocab@1',
                  :raw_module_name,
                  'canonical',
                  :canonical_code,
                  1,
                  'mock exam fixture'
                )
                """
            ),
            [
                {
                    "raw_module_name": module.display_name,
                    "canonical_code": module.code,
                }
                for module in GUOKAO_XINGCE_MODULES
            ],
        )
        if module_aliases:
            session.execute(
                text(
                    """
                    insert into product.vocab_module_alias_map (
                      version,
                      raw_module_name,
                      action,
                      canonical_code,
                      local_question_count,
                      basis
                    ) values (
                      'mock-vocab@1',
                      :raw_module_name,
                      'map',
                      :canonical_code,
                      1,
                      'mock frozen vocab alias'
                    )
                    """
                ),
                [
                    {
                        "raw_module_name": raw_module_name,
                        "canonical_code": canonical_code,
                    }
                    for raw_module_name, canonical_code in module_aliases
                ],
            )
        session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id,
                  release_version,
                  exam_scope,
                  track,
                  source_data_version,
                  vocab_version,
                  generator_version,
                  source_fingerprint,
                  generated_at,
                  fresh_until,
                  status,
                  node_count,
                  activated_at
                ) values (
                  cast(:release_id as uuid),
                  :release_version,
                  'guokao',
                  'xingce',
                  'mock-qbank@2026-07-27',
                  'mock-vocab@1',
                  'mock-fixture@1',
                  repeat('a', 64),
                  now() - interval '1 day',
                  :fresh_until,
                  'draft',
                  0,
                  null
                )
                """
            ),
            {
                "release_id": knowledge_map_release_id,
                "release_version": f"mock-map@{knowledge_map_release_id}",
                "fresh_until": fresh_until or datetime.now(UTC) + timedelta(days=7),
            },
        )
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
                  '政治理论',
                  '高频政治理论',
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
            method_card_ids = '["mc_mock"]' if module_name == "政治理论" else "[]"
            session.execute(
                text(
                    """
                    insert into product.knowledge_map_nodes (
                      release_id,
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
                      cast(:release_id as uuid),
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
                    "release_id": knowledge_map_release_id,
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
                          '知识点',
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
                for option_index, label in enumerate(("A", "B", "C", "D")):
                    session.execute(
                        text(
                            """
                            insert into product.question_options (
                              question_id,
                              option_index,
                              label,
                              option_text
                            ) values (
                              :question_id,
                              :option_index,
                              :label,
                              :option_text
                            )
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
        _freeze_mock_question_bindings(
            session,
            release_id=knowledge_map_release_id,
        )
        if expire_after_activation:
            session.execute(
                text(
                    """
                    update product.knowledge_map_releases
                    set fresh_until =
                      statement_timestamp() + interval '100 milliseconds'
                    where release_id = cast(:release_id as uuid)
                      and status = 'draft'
                    """
                ),
                {"release_id": knowledge_map_release_id},
            )
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set status = 'active', node_count = :node_count, activated_at = now()
                where release_id = cast(:release_id as uuid)
                  and status = 'draft'
                """
            ),
            {
                "release_id": knowledge_map_release_id,
                "node_count": len(GUOKAO_MOCK_BLUEPRINT),
            },
        )
        session.commit()
    return excluded_question_id


def _freeze_mock_question_bindings(
    session: Session,
    *,
    release_id: str,
) -> None:
    catalog = SQLQuestionContentCatalog(session)
    question_ids = list(
        session.execute(
            text(
                """
                select question_id
                from product.questions
                where domain = 'objective'
                  and status = 'active'
                order by question_id
                """
            )
        ).scalars()
    )
    for question_id in question_ids:
        catalog.publish_current(question_id=str(question_id))

    session.execute(
        text(
            """
            with prepared as (
              select
                release.release_id,
                node.node_id,
                question.question_id,
                content_head.content_version_id,
                module_alias.canonical_code as module_code,
                canonical_module.raw_module_name as canonical_module,
                tag.tag_type as source_tag_type,
                tag.tag_value as source_tag_value,
                (
                  nullif(
                    btrim(content.snapshot_payload->>'stem'),
                    ''
                  ) is not null
                  and nullif(
                    btrim(content.snapshot_payload->>'answer'),
                    ''
                  ) is not null
                  and exists (
                    select 1
                    from jsonb_array_elements(
                      content.snapshot_payload->'options'
                    ) option
                    where upper(btrim(option->>'label')) =
                          upper(
                            btrim(content.snapshot_payload->>'answer')
                          )
                  )
                ) as delivery_eligible,
                question.visual_required,
                question.shared_context_required,
                question.latest_year,
                coalesce(question.occurrence_count, 0)::integer
                  as occurrence_count
              from product.knowledge_map_releases release
              join product.questions question
                on question.domain = 'objective'
               and question.status = 'active'
              join product.vocab_module_alias_map module_alias
                on module_alias.version = release.vocab_version
               and module_alias.raw_module_name = question.module_name
               and module_alias.action in ('canonical', 'map')
              join product.vocab_module_alias_map canonical_module
                on canonical_module.version = module_alias.version
               and canonical_module.action = 'canonical'
               and canonical_module.canonical_code =
                   module_alias.canonical_code
              join product.question_tags tag
                on tag.question_id = question.question_id
              join product.knowledge_map_nodes node
                on node.release_id = release.release_id
               and node.module_name = canonical_module.raw_module_name
               and node.tag_type = tag.tag_type
               and node.tag_value = tag.tag_value
              join product.question_content_heads content_head
                on content_head.question_id = question.question_id
              join product.question_content_versions content
                on content.question_id = question.question_id
               and content.content_version_id =
                   content_head.content_version_id
              where release.release_id = cast(:release_id as uuid)
                and release.status = 'draft'
            )
            insert into product.knowledge_map_question_bindings (
              release_id,
              node_id,
              question_id,
              content_version_id,
              module_code,
              canonical_module,
              binding_kind,
              source_tag_type,
              source_tag_value,
              delivery_eligible,
              visual_required,
              shared_context_required,
              latest_year,
              occurrence_count,
              latest_recent_exam_year,
              recent_occurrence_count,
              latest_old_exam_year,
              old_occurrence_count,
              binding_fingerprint,
              created_at
            )
            select
              prepared.release_id,
              prepared.node_id,
              prepared.question_id,
              prepared.content_version_id,
              prepared.module_code,
              prepared.canonical_module,
              'direct',
              prepared.source_tag_type,
              prepared.source_tag_value,
              prepared.delivery_eligible,
              prepared.visual_required,
              prepared.shared_context_required,
              prepared.latest_year,
              prepared.occurrence_count,
              2026,
              1,
              null,
              0,
              product.knowledge_map_question_binding_row_fingerprint(
                row(
                  prepared.release_id,
                  prepared.node_id,
                  prepared.question_id,
                  prepared.content_version_id,
                  prepared.module_code,
                  prepared.canonical_module,
                  'direct',
                  prepared.source_tag_type,
                  prepared.source_tag_value,
                  prepared.delivery_eligible,
                  prepared.visual_required,
                  prepared.shared_context_required,
                  prepared.latest_year,
                  prepared.occurrence_count,
                  2026,
                  1,
                  null,
                  0,
                  repeat('0', 64),
                  transaction_timestamp()
                )::product.knowledge_map_question_bindings
              ),
              transaction_timestamp()
            from prepared
            """
        ),
        {"release_id": release_id},
    )
    session.execute(
        text(
            """
            with manifest as (
              select *
              from product.knowledge_map_question_binding_manifest(
                cast(:release_id as uuid)
              )
            )
            update product.knowledge_map_releases release
            set
              question_binding_count = manifest.binding_count,
              question_binding_fingerprint =
                manifest.binding_fingerprint
            from manifest
            where release.release_id = cast(:release_id as uuid)
              and release.status = 'draft'
            """
        ),
        {"release_id": release_id},
    )


def _copy_mock_question_bindings(
    session: Session,
    *,
    source_release_id: str,
    target_release_id: str,
    node_ids: tuple[str, ...] | None = None,
    target_node_id: str | None = None,
) -> None:
    session.execute(
        text(
            """
            with prepared as (
              select
                cast(:target_release_id as uuid) as release_id,
                coalesce(:target_node_id, binding.node_id) as node_id,
                binding.question_id,
                binding.content_version_id,
                binding.module_code,
                binding.canonical_module,
                binding.binding_kind,
                binding.source_tag_type,
                binding.source_tag_value,
                binding.delivery_eligible,
                binding.visual_required,
                binding.shared_context_required,
                binding.latest_year,
                binding.occurrence_count,
                binding.latest_recent_exam_year,
                binding.recent_occurrence_count,
                binding.latest_old_exam_year,
                binding.old_occurrence_count
              from product.knowledge_map_question_bindings binding
              where binding.release_id = cast(:source_release_id as uuid)
                and (
                  cast(:node_ids as text[]) is null
                  or binding.node_id = any(cast(:node_ids as text[]))
                )
            )
            insert into product.knowledge_map_question_bindings (
              release_id,
              node_id,
              question_id,
              content_version_id,
              module_code,
              canonical_module,
              binding_kind,
              source_tag_type,
              source_tag_value,
              delivery_eligible,
              visual_required,
              shared_context_required,
              latest_year,
              occurrence_count,
              latest_recent_exam_year,
              recent_occurrence_count,
              latest_old_exam_year,
              old_occurrence_count,
              binding_fingerprint,
              created_at
            )
            select
              prepared.*,
              product.knowledge_map_question_binding_row_fingerprint(
                row(
                  prepared.release_id,
                  prepared.node_id,
                  prepared.question_id,
                  prepared.content_version_id,
                  prepared.module_code,
                  prepared.canonical_module,
                  prepared.binding_kind,
                  prepared.source_tag_type,
                  prepared.source_tag_value,
                  prepared.delivery_eligible,
                  prepared.visual_required,
                  prepared.shared_context_required,
                  prepared.latest_year,
                  prepared.occurrence_count,
                  prepared.latest_recent_exam_year,
                  prepared.recent_occurrence_count,
                  prepared.latest_old_exam_year,
                  prepared.old_occurrence_count,
                  repeat('0', 64),
                  transaction_timestamp()
                )::product.knowledge_map_question_bindings
              ),
              transaction_timestamp()
            from prepared
            """
        ),
        {
            "source_release_id": source_release_id,
            "target_release_id": target_release_id,
            "node_ids": list(node_ids) if node_ids is not None else None,
            "target_node_id": target_node_id,
        },
    )
    session.execute(
        text(
            """
            with manifest as (
              select *
              from product.knowledge_map_question_binding_manifest(
                cast(:target_release_id as uuid)
              )
            )
            update product.knowledge_map_releases release
            set
              question_binding_count = manifest.binding_count,
              question_binding_fingerprint =
                manifest.binding_fingerprint
            from manifest
            where release.release_id =
                  cast(:target_release_id as uuid)
              and release.status = 'draft'
            """
        ),
        {"target_release_id": target_release_id},
    )


def _publish_copy_of_active_knowledge_map_release(
    db_session_factory: sessionmaker[Session],
) -> str:
    with db_session_factory() as session:
        copied_release_id = str(uuid4())
        active_release_id = session.execute(
            text(
                """
                select release_id::text
                from product.knowledge_map_releases
                where exam_scope = 'guokao'
                  and track = 'xingce'
                  and status = 'active'
                """
            )
        ).scalar_one()
        session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id,
                  release_version,
                  exam_scope,
                  track,
                  source_data_version,
                  vocab_version,
                  generator_version,
                  source_fingerprint,
                  generated_at,
                  fresh_until,
                  status,
                  node_count,
                  supersedes_release_id
                ) values (
                  cast(:copied_release_id as uuid),
                  :release_version,
                  'guokao',
                  'xingce',
                  'mock-qbank@copied-release',
                  'mock-vocab@1',
                  'mock-fixture@copied-release',
                  repeat('b', 64),
                  now(),
                  now() + interval '7 days',
                  'draft',
                  0,
                  cast(:active_release_id as uuid)
                )
                """
            ),
            {
                "copied_release_id": copied_release_id,
                "active_release_id": active_release_id,
                "release_version": f"mock-map@copied-{copied_release_id}",
            },
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_nodes (
                  release_id,
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  parent_node_id,
                  aliases,
                  support_status,
                  near5_window_start_year,
                  near5_window_end_year,
                  near5_weight,
                  near5_occurrence_count,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status,
                  created_at,
                  updated_at
                )
                select
                  cast(:copied_release_id as uuid),
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  parent_node_id,
                  aliases,
                  support_status,
                  near5_window_start_year,
                  near5_window_end_year,
                  near5_weight,
                  near5_occurrence_count,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status,
                  created_at,
                  updated_at
                from product.knowledge_map_nodes
                where release_id = cast(:active_release_id as uuid)
                """
            ),
            {
                "copied_release_id": copied_release_id,
                "active_release_id": active_release_id,
            },
        )
        _copy_mock_question_bindings(
            session,
            source_release_id=active_release_id,
            target_release_id=copied_release_id,
        )
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set status = 'retired', retired_at = now()
                where release_id = cast(:active_release_id as uuid)
                  and status = 'active'
                """
            ),
            {"active_release_id": active_release_id},
        )
        session.execute(
            text(
                """
                update product.knowledge_map_releases release
                set
                  status = 'active',
                  node_count = (
                    select count(*)::integer
                    from product.knowledge_map_nodes node
                    where node.release_id = release.release_id
                  ),
                  activated_at = now()
                where release.release_id = cast(:copied_release_id as uuid)
                  and release.status = 'draft'
                """
            ),
            {"copied_release_id": copied_release_id},
        )
        session.commit()
    return copied_release_id


def _publish_sparse_replacement_release(
    db_session_factory: sessionmaker[Session],
) -> str:
    replacement_release_id = str(uuid4())
    replacement_vocab_version = f"mock-vocab@replacement-{replacement_release_id}"
    with db_session_factory() as session:
        active_release_id = session.execute(
            text(
                """
                select release_id::text
                from product.knowledge_map_releases
                where exam_scope = 'guokao'
                  and track = 'xingce'
                  and status = 'active'
                """
            )
        ).scalar_one()
        session.execute(
            text(
                """
                insert into product.vocab_module_alias_map (
                  version,
                  raw_module_name,
                  action,
                  canonical_code,
                  local_question_count,
                  basis
                ) values (
                  :version,
                  :raw_module_name,
                  'canonical',
                  :canonical_code,
                  1,
                  'mock replacement release'
                )
                """
            ),
            [
                {
                    "version": replacement_vocab_version,
                    "raw_module_name": module.display_name,
                    "canonical_code": module.code,
                }
                for module in GUOKAO_XINGCE_MODULES
            ],
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id,
                  release_version,
                  exam_scope,
                  track,
                  source_data_version,
                  vocab_version,
                  generator_version,
                  source_fingerprint,
                  generated_at,
                  fresh_until,
                  status,
                  node_count
                ) values (
                  cast(:release_id as uuid),
                  :release_version,
                  'guokao',
                  'xingce',
                  'mock-qbank@replacement',
                  :vocab_version,
                  'mock-fixture@replacement',
                  repeat('b', 64),
                  now(),
                  now() + interval '7 days',
                  'draft',
                  0
                )
                """
            ),
            {
                "release_id": replacement_release_id,
                "release_version": (f"mock-map@replacement-{replacement_release_id}"),
                "vocab_version": replacement_vocab_version,
            },
        )
        session.execute(
            text(
                """
                insert into product.knowledge_map_nodes (
                  release_id,
                  node_id,
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  display_path,
                  parent_node_id,
                  aliases,
                  support_status,
                  near5_window_start_year,
                  near5_window_end_year,
                  near5_weight,
                  near5_occurrence_count,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status,
                  created_at,
                  updated_at
                )
                select
                  cast(:replacement_release_id as uuid),
                  'km_zhengzhi_replacement',
                  exam_scope,
                  track,
                  module_name,
                  node_type,
                  tag_type,
                  tag_value,
                  '行测/政治理论/替换发布',
                  null,
                  aliases,
                  support_status,
                  near5_window_start_year,
                  near5_window_end_year,
                  near5_weight,
                  near5_occurrence_count,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status,
                  now(),
                  now()
                from product.knowledge_map_nodes
                where release_id = cast(:active_release_id as uuid)
                  and node_id = 'km_zhengzhi'
                """
            ),
            {
                "replacement_release_id": replacement_release_id,
                "active_release_id": active_release_id,
            },
        )
        _copy_mock_question_bindings(
            session,
            source_release_id=active_release_id,
            target_release_id=replacement_release_id,
            node_ids=("km_zhengzhi",),
            target_node_id="km_zhengzhi_replacement",
        )
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set status = 'retired',
                    retired_at = now()
                where release_id = cast(:active_release_id as uuid)
                """
            ),
            {"active_release_id": active_release_id},
        )
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                set status = 'active',
                    node_count = 1,
                    activated_at = now()
                where release_id = cast(:replacement_release_id as uuid)
                """
            ),
            {"replacement_release_id": replacement_release_id},
        )
        session.commit()
    return replacement_release_id


def _replace_session_paper_metadata(
    db_session_factory: sessionmaker[Session],
    *,
    session_id: str,
    equivalence_metadata: dict[str, object],
) -> str:
    paper_version_id = str(uuid4())
    with db_session_factory() as session:
        session.execute(
            text(
                """
                insert into product.mock_exam_paper_versions (
                  paper_version_id,
                  paper_key,
                  version_no,
                  state,
                  exam_scope,
                  track,
                  title,
                  duration_minutes,
                  question_ids,
                  blueprint,
                  learner_bundle_version_id,
                  scoring_policy_version_id,
                  exposure_policy,
                  equivalence_metadata,
                  content_fingerprint,
                  published_at,
                  created_at
                )
                select
                  cast(:paper_version_id as uuid),
                  :paper_key,
                  1,
                  'retired',
                  mock_session.exam_scope,
                  mock_session.track,
                  '历史兼容测试卷',
                  120,
                  mock_session.question_ids,
                  mock_session.blueprint,
                  mock_session.learner_bundle->>'bundleVersionId',
                  mock_session.scoring_policy_version_id,
                  jsonb_build_object('legacy', true),
                  cast(:equivalence_metadata as jsonb),
                  :content_fingerprint,
                  mock_session.created_at,
                  mock_session.created_at
                from learner.mock_exam_sessions mock_session
                where mock_session.session_id = cast(:session_id as uuid)
                """
            ),
            {
                "paper_version_id": paper_version_id,
                "paper_key": f"mock-lineage-fixture:{paper_version_id}",
                "equivalence_metadata": json.dumps(
                    equivalence_metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "content_fingerprint": uuid4().hex + uuid4().hex,
                "session_id": session_id,
            },
        )
        session.execute(
            text(
                """
                update learner.mock_exam_sessions
                set paper_version_id = cast(:paper_version_id as uuid)
                where session_id = cast(:session_id as uuid)
                """
            ),
            {"paper_version_id": paper_version_id, "session_id": session_id},
        )
        session.commit()
    return paper_version_id


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
        paper_version_id = str(uuid4())
        session.execute(
            text(
                """
                insert into product.mock_exam_paper_versions (
                  paper_version_id, paper_key, version_no, state, exam_scope,
                  track, title, duration_minutes, question_ids, blueprint,
                  learner_bundle_version_id, scoring_policy_version_id,
                  exposure_policy, equivalence_metadata, content_fingerprint,
                  published_at, created_at
                ) values (
                  cast(:paper_version_id as uuid), :paper_key, 1, 'retired',
                  'guokao', 'xingce', '测试历史卷', 120,
                  '[]'::jsonb, '[]'::jsonb, 'fixture-bundle',
                  'mock-objective-exact-v1', '{}'::jsonb, '{}'::jsonb,
                  repeat('a', 64), now(), now()
                )
                """
            ),
            {
                "paper_version_id": paper_version_id,
                "paper_key": f"fixture:{paper_version_id}",
            },
        )
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
                  paper_version_id,
                  scoring_policy_version_id,
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
                  cast(:paper_version_id as uuid),
                  'mock-objective-exact-v1',
                  now(),
                  now(),
                  now()
                )
                """
            ),
            {"user_id": user_id, "paper_version_id": paper_version_id},
        )
        session.commit()
