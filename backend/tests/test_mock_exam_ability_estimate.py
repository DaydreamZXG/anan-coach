from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.guokao_xingce import GUOKAO_XINGCE_MODULES
from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog
from app.domains.mock_exam.service import GUOKAO_MOCK_BLUEPRINT
from app.main import create_app
from tests.task_editing_helpers import claim_task_edit_headers


def test_full_mock_submission_updates_ability_estimate_idempotently(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_user(db_session_factory, token="mock-ability-token")
    _seed_mock_qbank(db_session_factory)
    _seed_active_position_release(db_session_factory)
    client = _make_client(db_session_factory)
    auth = {"Authorization": "Bearer mock-ability-token"}

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
    answers = [
        {
            "questionId": item["questionId"],
            "selectedAnswer": "A" if index < 2 else "B",
        }
        for index, item in enumerate(exam["questions"])
    ]
    submit_headers = {**auth, **edit_headers}

    submitted = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers=submit_headers,
        json={"expectedRevision": exam["revision"], "answers": answers},
    )
    assert submitted.status_code == 200

    with db_session_factory() as session:
        evidence = session.execute(
            text(
                """
                select source_type, reported_context, xingce_score, shenlun_score,
                       total_score, timed_status, verification_status
                from learning.score_evidence
                """
            )
        ).mappings().one()
        estimate = session.execute(
            text(
                """
                select status, xingce_recommended, total_recommended, confidence
                from learning.ability_estimate_versions
                """
            )
        ).mappings().one()
        outbox = session.execute(
            text(
                "select event_type from platform.outbox_events "
                "where event_type = 'ability_estimate.changed'"
            )
        ).scalars().all()

    assert evidence["source_type"] == "controlled_mock"
    assert evidence["reported_context"] is None
    assert evidence["xingce_score"] == 98.5
    assert evidence["shenlun_score"] is None
    assert evidence["total_score"] is None
    assert evidence["timed_status"] == "timed"
    assert evidence["verification_status"] == "system_verified"
    assert estimate["status"] == "partial"
    assert estimate["xingce_recommended"] == 98.5
    assert estimate["total_recommended"] is None
    assert estimate["confidence"] == "medium"
    assert outbox == ["ability_estimate.changed"]

    current = client.get(
        "/api/v2/ability-estimates/current",
        headers={"Authorization": "Bearer mock-ability-token"},
    )
    assert current.status_code == 200
    data = current.json()["data"]
    assert data["readiness"] == "partial"
    assert data["missingComponents"] == ["shenlun"]
    assert data["estimate"]["xingce"]["recommended"] == 98.5
    assert data["estimate"]["evidenceSummary"]["evidenceCount"] == 1
    assert data["estimate"]["evidenceSummary"]["sourceCounts"] == {"controlled_mock": 1}

    replay = client.post(
        f"/api/v1/mock-exams/{exam['sessionId']}/submit",
        headers=submit_headers,
        json={"expectedRevision": exam["revision"], "answers": answers},
    )
    assert replay.status_code == 409  # already submitted: revision conflict, no second write

    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from learning.score_evidence")
        ).scalar_one() == 1
        assert session.execute(
            text("select count(*) from learning.ability_estimate_versions")
        ).scalar_one() == 1


def test_controlled_mock_replay_returns_persisted_evidence(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="mock-ability-replay-token")
    _seed_active_position_release(db_session_factory)
    client = _make_client(db_session_factory)
    result = client.post(
        "/api/v2/score-evidence/reported",
        headers={
            "Authorization": "Bearer mock-ability-replay-token",
            "Idempotency-Key": "unrelated-reported-evidence",
        },
        json={
            "reportedContext": "full_mock",
            "xingceScore": 62,
            "timedStatus": "timed",
            "occurredAt": "2026-08-01T08:00:00Z",
        },
    )
    assert result.status_code == 200

    from app.domains.ability_estimation.service import AbilityEstimateService

    first = None
    with db_session_factory.begin() as session:
        first = AbilityEstimateService().record_controlled_mock_score(
            session,
            user_id=user_id,
            command_id="mock-exam:replay-session",
            source_report_version_id="replay-session",
            occurred_at=datetime(2026, 8, 2, 8, tzinfo=UTC),
            xingce_score=70.0,
            shenlun_score=None,
            now=datetime(2026, 8, 3, 8, tzinfo=UTC),
        )
    with db_session_factory.begin() as session:
        replay = AbilityEstimateService().record_controlled_mock_score(
            session,
            user_id=user_id,
            command_id="mock-exam:replay-session",
            source_report_version_id="replay-session",
            occurred_at=datetime(2026, 8, 2, 8, tzinfo=UTC),
            xingce_score=70.0,
            shenlun_score=None,
            now=datetime(2026, 8, 3, 8, tzinfo=UTC),
        )

    assert replay.evidence.evidence_id == first.evidence.evidence_id
    assert replay.estimate.estimate_version_id == first.estimate.estimate_version_id
    assert replay.estimate.source_evidence_ids == first.estimate.source_evidence_ids
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from learning.score_evidence")
        ).scalar_one() == 2  # reported evidence + one controlled mock evidence


def test_controlled_mock_rejects_same_command_id_with_different_score(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory, token="mock-ability-command-token")
    _seed_active_position_release(db_session_factory)
    from app.domains.ability_estimation.service import AbilityEstimateService
    from app.domains.ability_estimation.service import AbilityEstimateContractError

    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    with db_session_factory.begin() as session:
        AbilityEstimateService().record_controlled_mock_score(
            session,
            user_id=user_id,
            command_id="mock-exam:command-session",
            source_report_version_id="command-session",
            occurred_at=datetime(2026, 8, 2, 8, tzinfo=UTC),
            xingce_score=70.0,
            shenlun_score=None,
            now=now,
        )
    try:
        with db_session_factory.begin() as session:
            AbilityEstimateService().record_controlled_mock_score(
                session,
                user_id=user_id,
                command_id="mock-exam:command-session",
                source_report_version_id="command-session",
                occurred_at=datetime(2026, 8, 2, 8, tzinfo=UTC),
                xingce_score=80.0,
                shenlun_score=None,
                now=now,
            )
        raised = False
    except AbilityEstimateContractError:
        raised = True
    assert raised


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
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid),
                  :session_token_hash, 'ios-mock-ability-test', :expires_at
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
                  user_id, target_exam, target_region, position_group,
                  daily_minutes, study_mode, baseline_source,
                  baseline_xingce_score, onboarding_complete
                ) values (
                  cast(:user_id as uuid), '国考', '全国', '中央机关综合管理岗',
                  120, 'parttime', 'self_report', 60, true
                )
                """
            ),
            {"user_id": user_id},
        )
        session.commit()
    return user_id


def _seed_active_position_release(db_session_factory: sessionmaker[Session]) -> None:
    with db_session_factory() as session:
        release_id = str(uuid4())
        activated_at = datetime.now(UTC)
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  cast(:release_id as uuid), 'guokao', 2026,
                  'mock-ability-rules-v1', 'mock-ability-positions-v1',
                  'draft', null
                )
                """
            ),
            {"release_id": release_id},
        )
        session.execute(
            text(
                """
                insert into product.position_release_windows (
                  window_id, release_id, window_kind, sequence_no,
                  opens_at, closes_at, official_source_url,
                  official_source_title, official_source_published_at,
                  official_source_received_at, official_source_sha256,
                  official_source_receipt
                ) values (
                  cast(:window_id as uuid), cast(:release_id as uuid),
                  'primary', 1, :opens_at, :closes_at,
                  'https://example.gov.cn/guokao/mock-ability',
                  '内场模考能力估计测试窗口', :published_at, :received_at,
                  repeat('a', 64), 'mock-ability-fixture'
                )
                """
            ),
            {
                "window_id": str(uuid4()),
                "release_id": release_id,
                "opens_at": activated_at - timedelta(days=30),
                "closes_at": activated_at + timedelta(days=730),
                "published_at": activated_at - timedelta(days=40),
                "received_at": activated_at - timedelta(days=35),
            },
        )
        session.execute(
            text(
                """
                update product.position_rule_releases
                set status = 'active', activated_at = :activated_at
                where release_id = cast(:release_id as uuid)
                """
            ),
            {"release_id": release_id, "activated_at": activated_at},
        )
        session.commit()


def _seed_mock_qbank(
    db_session_factory: sessionmaker[Session],
    *,
    fresh_until: datetime | None = None,
) -> None:
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
                  version, raw_module_name, action, canonical_code,
                  local_question_count, basis
                ) values (
                  'mock-vocab@1', :raw_module_name, 'canonical',
                  :canonical_code, 1, 'mock exam ability fixture'
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
        session.execute(
            text(
                """
                insert into product.knowledge_map_releases (
                  release_id, release_version, exam_scope, track,
                  source_data_version, vocab_version, generator_version,
                  source_fingerprint, generated_at, fresh_until, status,
                  node_count, activated_at
                ) values (
                  cast(:release_id as uuid), :release_version, 'guokao',
                  'xingce', 'mock-qbank@2026-07-27', 'mock-vocab@1',
                  'mock-fixture@1', repeat('a', 64),
                  now() - interval '1 day', :fresh_until, 'draft', 0, null
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
                  id, exam_scope, track, module_name, tag_path, title,
                  recognition, applicability, method_steps, error_signatures,
                  fix_action, time_budget_sec, accuracy_rule, pitfall,
                  author_note, validation, priority, status
                ) values (
                  'mc_mock', 'guokao', 'xingce', '政治理论', '高频政治理论',
                  '模考错题修复卡', '模考错题', '{}'::jsonb,
                  '["定位错因","补同类题"]'::jsonb, '[]'::jsonb,
                  '先复盘本 cluster，再做同类题。', 120,
                  '复盘后同类题正确率提升', null, 'test', '{}'::jsonb,
                  5, 'published'
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
                      release_id, node_id, exam_scope, track, module_name,
                      node_type, tag_type, tag_value, display_path,
                      support_status, near5_window_start_year,
                      near5_window_end_year, near5_weight,
                      near5_occurrence_count, stable_for_old_questions,
                      method_card_ids, evidence, status
                    ) values (
                      cast(:release_id as uuid), :node_id, 'guokao', 'xingce',
                      :module_name, 'knowledge_point', 'knowledge_point',
                      :tag_value, :display_path, 'text_supported', 2022, 2026,
                      0.5, 50, true, cast(:method_card_ids as jsonb),
                      '{}'::jsonb, 'published'
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
                          question_id, domain, status, module_name,
                          question_type, stem, answer, visual_required,
                          latest_year, occurrence_count
                        ) values (
                          :question_id, 'objective', 'active', :module_name,
                          '知识点', :stem, 'B', false, 2026, 10
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
                              question_id, option_index, label, option_text
                            ) values (
                              :question_id, :option_index, :label, :option_text
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
                          question_tag_id, question_id, tag_type, tag_value
                        ) values (
                          :question_tag_id, :question_id,
                          'knowledge_point', :tag_value
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
                          occurrence_id, question_id, province, year,
                          paper_title, canonical_occurrence_rank
                        ) values (
                          :occurrence_id, :question_id, '国考', 2026,
                          '2026年国家公务员录用考试《行测》题（测试卷）', 1
                        )
                        """
                    ),
                    {"occurrence_id": f"occ_{question_id}", "question_id": question_id},
                )
        _freeze_mock_question_bindings(session, release_id=knowledge_map_release_id)
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
              release_id, node_id, question_id, content_version_id,
              module_code, canonical_module, binding_kind,
              source_tag_type, source_tag_value, delivery_eligible,
              visual_required, shared_context_required, latest_year,
              occurrence_count, latest_recent_exam_year,
              recent_occurrence_count, latest_old_exam_year,
              old_occurrence_count, binding_fingerprint, created_at
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
              question_binding_fingerprint = manifest.binding_fingerprint
            from manifest
            where release.release_id = cast(:release_id as uuid)
              and release.status = 'draft'
            """
        ),
        {"release_id": release_id},
    )
