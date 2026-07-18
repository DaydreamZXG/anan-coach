from __future__ import annotations

from importlib import import_module
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _seed_v12_essay_history(engine: Engine) -> None:
    """Persist rows that predate grounded execution metadata.

    Migration 13 must quarantine this immutable history instead of inventing
    material bindings, execution identities, or grounding evidence for it.
    """

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values ('00000000-0000-0000-0000-000000000201', 'legacy-user');

                insert into product.essay_task_versions (
                  task_version_id, task_key, version_no, question_type,
                  prompt, prompt_sha256, word_limit, maximum_score, state
                ) values (
                  '00000000-0000-0000-0000-000000000202',
                  'legacy-grounding-task', 1, 'summary', 'legacy prompt',
                  repeat('a', 64), 200, 20, 'published'
                );

                insert into product.essay_material_bundle_versions (
                  material_bundle_version_id, task_version_id, task_key,
                  version_no, completeness, source_kind, content_sha256
                ) values (
                  '00000000-0000-0000-0000-000000000203',
                  '00000000-0000-0000-0000-000000000202',
                  'legacy-grounding-task', 1, 'complete', 'official',
                  repeat('b', 64)
                );

                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key, version_no,
                  source_kind, verification_status, state, total_maximum,
                  criteria, points, content_sha256
                ) values
                (
                  '00000000-0000-0000-0000-000000000204',
                  '00000000-0000-0000-0000-000000000202',
                  'legacy-grounding-task', 1, 'official', 'verified',
                  'published', 20, '[{"id":"criterion-1"}]'::jsonb,
                  '[{"id":"point-1"}]'::jsonb, repeat('c', 64)
                ),
                (
                  '00000000-0000-0000-0000-000000000205',
                  '00000000-0000-0000-0000-000000000202',
                  'legacy-grounding-task', 2, 'system_compiled', 'unverified',
                  'draft', 20, '[{"id":"criterion-1"}]'::jsonb,
                  '[{"id":"point-1"}]'::jsonb, repeat('d', 64)
                );

                insert into learning.essay_attempts (
                  attempt_id, command_id, user_id, task_version_id, task_key,
                  material_bundle_version_id, rubric_version_id, source_context,
                  timed_status, elapsed_seconds, input_fingerprint, submitted_at
                ) values (
                  '00000000-0000-0000-0000-000000000206',
                  'legacy-attempt-command',
                  '00000000-0000-0000-0000-000000000201',
                  '00000000-0000-0000-0000-000000000202',
                  'legacy-grounding-task',
                  '00000000-0000-0000-0000-000000000203',
                  '00000000-0000-0000-0000-000000000204',
                  'in_program', 'timed', 900, repeat('e', 64), now()
                );

                insert into learning.essay_answer_versions (
                  answer_version_id, command_id, attempt_id, user_id,
                  task_version_id, version_no, source_type, answer_text,
                  answer_sha256, thinking_payload, thinking_sha256,
                  input_fingerprint
                ) values (
                  '00000000-0000-0000-0000-000000000207',
                  'legacy-answer-command',
                  '00000000-0000-0000-0000-000000000206',
                  '00000000-0000-0000-0000-000000000201',
                  '00000000-0000-0000-0000-000000000202',
                  1, 'typed', 'legacy answer', repeat('f', 64),
                  '{"reflection":"legacy reflection"}'::jsonb,
                  repeat('1', 64), repeat('2', 64)
                );

                insert into learning.essay_review_versions (
                  review_version_id, command_id, attempt_id, user_id,
                  task_version_id, answer_version_id, material_bundle_version_id,
                  rubric_version_id, status, review_scope,
                  thinking_evidence_available, readiness, score_lower,
                  score_recommended, score_upper, score_maximum,
                  score_confidence, dimensions, point_coverage, claim_evidence,
                  findings, rewrite_plan, contract_version,
                  provider_schema_version, rubric_contract_version,
                  prompt_version, provider, model, input_fingerprint,
                  output_fingerprint, generated_at, valid_until
                ) values (
                  '00000000-0000-0000-0000-000000000208',
                  'legacy-review-command',
                  '00000000-0000-0000-0000-000000000206',
                  '00000000-0000-0000-0000-000000000201',
                  '00000000-0000-0000-0000-000000000202',
                  '00000000-0000-0000-0000-000000000207',
                  '00000000-0000-0000-0000-000000000203',
                  '00000000-0000-0000-0000-000000000204',
                  'completed', 'grounded_full', true, '{}'::jsonb,
                  10, 12, 14, 20, 'medium', '[{"id":"dimension-1"}]'::jsonb,
                  '[{"id":"point-1"}]'::jsonb, '[]'::jsonb,
                  '[{"id":"finding-1"}]'::jsonb, '{}'::jsonb,
                  'essay-review-v2', 'provider-schema-v1', 'rubric-v1',
                  'prompt-v1', 'legacy-provider', 'legacy-model',
                  repeat('3', 64), repeat('4', 64), now(), now() + interval '7 days'
                );
                """
            )
        )


def _seed_v12_onboarding_history(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into learner.users (user_id, display_name) values
                  ('00000000-0000-0000-0000-000000000221', 'legacy-scheduled'),
                  ('00000000-0000-0000-0000-000000000222', 'legacy-no-schedule');

                insert into learner.profiles (
                  user_id, daily_minutes, study_mode, education, major,
                  fresh_graduate, political_status, grassroots_years,
                  onboarding_complete, profile_version
                ) values
                (
                  '00000000-0000-0000-0000-000000000221', 90, 'parttime',
                  '本科', '法学', true, '群众', 0, true, 1
                ),
                (
                  '00000000-0000-0000-0000-000000000222', null, null,
                  '本科', '法学', true, '群众', 0, true, 1
                );
                """
            )
        )


def test_wechat_identity_migration_upgrades_from_audited_baseline(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as connection:
        # conftest installs the current schema before this test reconstructs the
        # audited v12 baseline. Remove every later object so Alembic upgrades
        # from the same database shape that production v12 actually had.
        migration35 = import_module(
            "migrations.versions.20260717_35_commerce_privacy_operations"
        )
        migration30 = import_module(
            "migrations.versions.20260716_30_community_production_closure"
        )
        migration34 = import_module(
            "migrations.versions.20260716_34_ai_governance"
        )
        migration28 = import_module(
            "migrations.versions.20260716_28_community_method_cards"
        )
        definitions = connection.execute(
            text(
                "select definition from privacy.append_only_function_snapshots "
                "order by function_name"
            )
        ).scalars()
        for definition in definitions:
            connection.exec_driver_sql(str(definition))
        connection.execute(
            text("drop function if exists privacy.erasure_authorized(jsonb)")
        )
        for table_name in reversed(migration35._PRIVACY_TABLES):
            connection.execute(
                text(f"drop table if exists privacy.{table_name} cascade")
            )
        for table_name in reversed(migration35._COMMERCE_TABLES):
            connection.execute(
                text(f"drop table if exists commerce.{table_name} cascade")
            )
        connection.execute(
            text("drop table if exists learning.retake_program_versions cascade")
        )
        connection.execute(
            text("alter table learning.score_evidence drop column if exists erased_at")
        )
        connection.execute(
            text("drop function if exists commerce.enforce_stage8_state_transition()")
        )
        connection.execute(text("drop schema if exists privacy cascade"))
        connection.execute(
            text(
                "delete from commerce.iap_products "
                "where product_id = 'founder_experience_30d'"
            )
        )
        for table_name in reversed(migration34._TABLES):
            connection.execute(
                text(f"drop table if exists ai_audit.{table_name} cascade")
            )
        for function_name in (
            "ai_audit.validate_external_ai_operation",
            "ai_audit.validate_model_call_attribution",
            "ai_audit.validate_ai_scenario_deployment",
        ):
            connection.execute(text(f"drop function if exists {function_name}()"))
        later_tables = (
            *migration30._DROP_ORDER,
            ("community_method_card_delayed_verifications", "product"),
            *migration28._DROP_ORDER,
        )
        for table_name, schema_name in later_tables:
            connection.execute(
                text(f"drop table if exists {schema_name}.{table_name} cascade")
            )
        for function_name in (
            "commerce.validate_community_membership_activation",
            "commerce.validate_community_ai_reservation_event",
            "commerce.validate_community_ai_reservation",
            "product.enforce_community_policy_resource_update",
            "commerce.reject_community_reward_history_mutation",
            "product.reject_community_method_history_mutation",
        ):
            connection.execute(text(f"drop function if exists {function_name}()"))
        connection.execute(
            text("drop table learner.mock_exam_trajectory_events cascade")
        )
        connection.execute(
            text(
                "alter table learner.mock_exam_sessions "
                "drop column scoring_policy_version_id, "
                "drop column paper_version_id"
            )
        )
        connection.execute(text("drop table product.mock_exam_paper_versions cascade"))
        connection.execute(
            text("drop table product.mock_exam_scoring_policy_versions cascade")
        )
        connection.execute(text("drop table learner.mock_exam_commands"))
        connection.execute(text("drop index learner.mock_exam_one_assigned_per_owner_idx"))
        connection.execute(
            text("drop table learner.mock_exam_session_supersessions")
        )
        for column in (
            "scoring_snapshot",
            "learner_bundle",
            "checkpoint_updated_at",
            "checkpoint",
            "deadline_at",
            "started_at",
            "revision",
        ):
            connection.execute(
                text(f"alter table learner.mock_exam_sessions drop column {column}")
            )
        connection.execute(
            text("drop table product.question_visual_fingerprints")
        )
        connection.execute(text("drop table product.question_quality_heads"))
        connection.execute(
            text("drop table product.question_quality_decision_disputes")
        )
        connection.execute(text("drop table product.question_quality_commands"))
        connection.execute(
            text("drop table product.question_quality_decision_versions")
        )
        connection.execute(text("drop table product.question_content_heads"))
        connection.execute(text("drop table product.question_content_versions"))
        connection.execute(
            text("drop function product.reject_question_content_version_mutation()")
        )
        connection.execute(text("drop index product.product_question_stem_trgm_idx"))
        connection.execute(
            text("drop table product.content_license_authorization_snapshots")
        )
        connection.execute(text("drop table product.content_license_heads"))
        connection.execute(
            text("drop table product.content_license_record_versions")
        )
        connection.execute(text("drop table product.content_license_commands"))
        connection.execute(
            text("drop function product.reject_content_license_history_mutation()")
        )
        connection.execute(text("drop table learner.onboarding_progress_events"))
        connection.execute(text("drop table learner.onboarding_commands"))
        connection.execute(text("drop table learner.onboarding_progress"))
        connection.execute(
            text("drop function learner.enforce_onboarding_progress_transition()")
        )
        connection.execute(
            text("drop function learner.reject_onboarding_history_mutation()")
        )
        connection.execute(
            text("drop trigger onboarding_profile_enforce_projection on learner.profiles")
        )
        connection.execute(
            text("drop function learner.enforce_onboarding_profile_projection()")
        )
        connection.execute(text("drop table commerce.feature_usage_reservations"))
        connection.execute(
            text("drop function commerce.enforce_feature_usage_reservation_update()")
        )
        for table_name in (
            "app_store_transactions",
            "app_store_notification_events",
            "entitlement_usage_daily",
            "entitlement_audit_logs",
            "user_entitlements",
            "iap_products",
        ):
            connection.execute(
                text(f"drop table commerce.{table_name}")
            )
        connection.execute(text("drop table ai_audit.agent_tool_call_events"))
        connection.execute(text("drop table ai_audit.agent_run_versions"))
        connection.execute(text("drop table ai_audit.agent_definition_releases"))
        connection.execute(text("drop table ai_audit.essay_review_model_calls"))
        connection.execute(text("drop table ai_audit.essay_review_checkpoints"))
        connection.execute(text("drop table ai_audit.model_call_ledger cascade"))
        connection.execute(
            text("drop function ai_audit.enforce_model_call_ledger_transition()")
        )
        connection.execute(
            text("drop function ai_audit.reject_append_only_mutation()")
        )
        connection.execute(text("drop schema learning cascade"))
        connection.execute(text("drop table product.essay_rubric_versions"))
        connection.execute(text("drop table product.essay_material_segments"))
        connection.execute(text("drop table product.essay_material_bundle_versions"))
        connection.execute(text("drop table product.essay_task_versions"))
        connection.execute(text("drop table learner.position_goal_previews"))
        connection.execute(text("drop table learner.account_events"))
        connection.execute(text("drop table learner.position_goal_events"))
        connection.execute(text("drop table learner.position_goal_versions"))
        connection.execute(text("drop table learner.eligibility_snapshots"))
        connection.execute(text("drop table learner.qualification_evidence"))
        connection.execute(text("drop table learner.qualification_requirement_confirmations"))
        connection.execute(text("drop table learner.qualification_facts"))
        connection.execute(text("drop table learner.qualification_profile_changes"))
        connection.execute(text("drop table product.position_requirements"))
        connection.execute(text("drop table product.position_rule_compilations"))
        connection.execute(text("drop table product.position_rule_releases"))
        connection.execute(text("alter table learner.app_sessions drop column revoked_reason"))
        connection.execute(text("alter table learner.users drop column account_status_changed_at"))
        connection.execute(text("alter table learner.users drop column account_status"))
        connection.execute(text("alter table learner.profiles drop column profile_version"))
        connection.execute(text("drop table learner.wechat_identities"))
        connection.execute(text("drop schema platform cascade"))
        connection.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values ('00000000-0000-0000-0000-000000000241', 'legacy-duplicate-mock');

                insert into product.questions (
                  question_id, domain, status, module_name, question_type, stem, answer
                ) values (
                  'q-legacy-duplicate-mock', 'objective', 'active',
                  '判断推理', 'single_choice', '迁移保留题目', 'A'
                );

                insert into product.question_options (
                  question_id, option_index, label, option_text
                ) values
                  ('q-legacy-duplicate-mock', 1, 'A', '正确选项'),
                  ('q-legacy-duplicate-mock', 2, 'B', '干扰选项');

                insert into learner.mock_exam_sessions (
                  session_id, user_id, exam_scope, track, status,
                  question_ids, blueprint, answers, review_payload,
                  review_viewed_at, submitted_at, created_at, updated_at
                ) values
                (
                  '00000000-0000-0000-0000-000000000242',
                  '00000000-0000-0000-0000-000000000241',
                  'guokao', 'xingce', 'assigned',
                  '["q-legacy-duplicate-mock"]'::jsonb,
                  jsonb_build_array(jsonb_build_object(
                    'moduleName', '判断推理', 'questionCount', 1
                  )),
                  null, null,
                  null, null, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
                ),
                (
                  '00000000-0000-0000-0000-000000000243',
                  '00000000-0000-0000-0000-000000000241',
                  'guokao', 'xingce', 'assigned',
                  '["q-legacy-duplicate-mock"]'::jsonb,
                  jsonb_build_array(jsonb_build_object(
                    'moduleName', '判断推理', 'questionCount', 1
                  )),
                  null, null,
                  null, null, '2026-07-01T00:01:00Z', '2026-07-01T00:01:00Z'
                ),
                (
                  '00000000-0000-0000-0000-000000000244',
                  '00000000-0000-0000-0000-000000000241',
                  'guokao', 'xingce', 'reviewed',
                  '["q-legacy-duplicate-mock"]'::jsonb,
                  jsonb_build_object(
                    'modules', jsonb_build_object('判断推理', 1)
                  ),
                  '[]'::jsonb, '{}'::jsonb,
                  '2026-07-01T00:03:00Z', '2026-07-01T00:02:00Z',
                  '2026-07-01T00:02:00Z', '2026-07-01T00:03:00Z'
                );
                """
            )
        )

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", db_engine.url.render_as_string(hide_password=False))
    try:
        command.stamp(config, "20260712_00")
        command.upgrade(config, "20260713_12")
        _seed_v12_essay_history(db_engine)
        _seed_v12_onboarding_history(db_engine)
        command.upgrade(config, "head")

        inspector = inspect(db_engine)
        assert "wechat_identities" in inspector.get_table_names(schema="learner")
        assert "account_events" in inspector.get_table_names(schema="learner")
        assert "mock_exam_session_supersessions" in inspector.get_table_names(
            schema="learner"
        )
        assert set(inspector.get_table_names(schema="platform")) == {
            "background_jobs",
            "companion_paid_commands",
            "outbox_events",
        }
        companion_command_indexes = {
            index["name"]
            for index in inspector.get_indexes(
                "companion_paid_commands",
                schema="platform",
            )
        }
        assert {
            "platform_companion_paid_commands_open_idx",
            "platform_companion_paid_commands_provider_key_key",
            "platform_companion_paid_commands_user_created_idx",
        }.issubset(companion_command_indexes)
        assert {
            "platform_essay_review_job_review_version_idx",
            "platform_essay_review_job_user_answer_idx",
        }.issubset({
            index["name"]
            for index in inspector.get_indexes(
                "background_jobs",
                schema="platform",
            )
        })
        with db_engine.connect() as connection:
            assigned_sessions = connection.execute(
                text(
                    """
                    select session_id::text
                    from learner.mock_exam_sessions
                    where user_id = '00000000-0000-0000-0000-000000000241'
                      and status = 'assigned'
                    """
                )
            ).scalars().all()
            kept_session = connection.execute(
                text(
                    """
                    select scoring_policy_version_id,
                           learner_bundle->>'bundleVersionId' as bundle_version_id,
                           jsonb_array_length(learner_bundle->'questions') as question_count,
                           checkpoint->>'currentQuestionId' as current_question_id
                    from learner.mock_exam_sessions
                    where session_id = '00000000-0000-0000-0000-000000000242'
                    """
                )
            ).mappings().one()
            reviewed_paper_blueprint_type = connection.execute(
                text(
                    """
                    select jsonb_typeof(paper.blueprint)
                    from learner.mock_exam_sessions session
                    join product.mock_exam_paper_versions paper
                      on paper.paper_version_id = session.paper_version_id
                    where session.session_id =
                      '00000000-0000-0000-0000-000000000244'
                    """
                )
            ).scalar_one()
            archived = connection.execute(
                text(
                    """
                    select archived_session_id::text, kept_session_id::text,
                           original_row->>'status' as original_status
                    from learner.mock_exam_session_supersessions
                    where owner_user_id = '00000000-0000-0000-0000-000000000241'
                    """
                )
            ).mappings().one()
        assert assigned_sessions == ["00000000-0000-0000-0000-000000000242"]
        assert kept_session["scoring_policy_version_id"] == (
            "mock-balanced-performance-index-v1"
        )
        assert len(str(kept_session["bundle_version_id"])) == 64
        assert kept_session["question_count"] == 1
        assert kept_session["current_question_id"] == "q-legacy-duplicate-mock"
        assert reviewed_paper_blueprint_type == "array"
        assert archived == {
            "archived_session_id": "00000000-0000-0000-0000-000000000243",
            "kept_session_id": "00000000-0000-0000-0000-000000000242",
            "original_status": "assigned",
        }
        assert {
            "ability_estimate_versions",
            "essay_answer_versions",
            "essay_learning_artifact_versions",
            "essay_attempts",
            "essay_review_intent_versions",
            "essay_review_versions",
            "essay_rewrite_versions",
            "essay_transfer_verification_versions",
            "daily_capacity_versions",
            "daily_plan_tasks",
            "daily_plan_versions",
            "score_evidence",
            "source_artifact_versions",
            "recognition_revisions",
            "content_match_versions",
            "offline_packet_versions",
            "answer_ingestion_versions",
            "task_execution_versions",
            "weekly_study_preference_versions",
            "inference_candidate_versions",
            "learner_brain_profile_versions",
            "strategy_decision_versions",
            "weekly_plan_versions",
        }.issubset(inspector.get_table_names(schema="learning"))
        assert {
            "essay_material_bundle_versions",
            "essay_material_segments",
            "essay_rubric_versions",
            "essay_task_versions",
            "content_license_commands",
            "content_license_record_versions",
            "content_license_heads",
            "content_license_authorization_snapshots",
        }.issubset(inspector.get_table_names(schema="product"))
        assert {
            "qualification_facts",
            "qualification_profile_changes",
            "qualification_evidence",
            "qualification_requirement_confirmations",
            "eligibility_snapshots",
            "position_goal_versions",
            "position_goal_events",
            "position_goal_previews",
            "onboarding_progress",
            "onboarding_commands",
            "onboarding_progress_events",
        }.issubset(inspector.get_table_names(schema="learner"))
        assert "position_requirements" in inspector.get_table_names(schema="product")
        assert "position_rule_releases" in inspector.get_table_names(schema="product")
        assert "position_rule_compilations" in inspector.get_table_names(schema="product")
        assert "feature_usage_reservations" in inspector.get_table_names(
            schema="commerce"
        )
        assert {
            "iap_products",
            "user_entitlements",
            "app_store_transactions",
            "app_store_notification_events",
            "entitlement_usage_daily",
            "entitlement_audit_logs",
        }.issubset(inspector.get_table_names(schema="commerce"))
        profile_columns = {
            column["name"]
            for column in inspector.get_columns("profiles", schema="learner")
        }
        assert "profile_version" in profile_columns
        user_columns = {
            column["name"] for column in inspector.get_columns("users", schema="learner")
        }
        session_columns = {
            column["name"]
            for column in inspector.get_columns("app_sessions", schema="learner")
        }
        assert "account_status" in user_columns
        assert "revoked_reason" in session_columns

        with db_engine.connect() as connection:
            scheduled = connection.execute(
                text(
                    """
                    select progress.qualification_ready,
                           progress.study_preference_ready,
                           progress.completed,
                           profile.onboarding_complete,
                           preference.weekday_minutes
                    from learner.onboarding_progress progress
                    join learner.profiles profile using (user_id)
                    left join learning.weekly_study_preference_versions preference
                      on preference.user_id = progress.user_id
                     and preference.version_no = progress.study_preference_version
                    where progress.user_id =
                      '00000000-0000-0000-0000-000000000221'
                    """
                )
            ).mappings().one()
            assert scheduled["qualification_ready"] is True
            assert scheduled["study_preference_ready"] is True
            assert scheduled["completed"] is True
            assert scheduled["onboarding_complete"] is True
            assert scheduled["weekday_minutes"] == 90

            no_schedule = connection.execute(
                text(
                    """
                    select progress.qualification_ready,
                           progress.study_preference_ready,
                           progress.completed,
                           profile.onboarding_complete
                    from learner.onboarding_progress progress
                    join learner.profiles profile using (user_id)
                    where progress.user_id =
                      '00000000-0000-0000-0000-000000000222'
                    """
                )
            ).mappings().one()
            assert no_schedule["qualification_ready"] is True
            assert no_schedule["study_preference_ready"] is False
            assert no_schedule["completed"] is False
            assert no_schedule["onboarding_complete"] is False
        unique_constraints = inspector.get_unique_constraints(
            "wechat_identities",
            schema="learner",
        )
        assert any(
            constraint["column_names"] == ["app_id", "open_id"]
            for constraint in unique_constraints
        )

        with db_engine.connect() as connection:
            legacy_review = connection.execute(
                text(
                    """
                    select score_confidence_reasons, grounding_contract_version,
                           grounding_report, execution_fingerprint,
                           execution_attempt_no
                      from learning.essay_review_versions
                     where review_version_id =
                           '00000000-0000-0000-0000-000000000208'
                    """
                )
            ).mappings().one()
            assert all(value is None for value in legacy_review.values())

            legacy_compiled_rubric = connection.execute(
                text(
                    """
                    select material_bundle_version_id,
                           compilation_input_fingerprint, compilation_audit
                      from product.essay_rubric_versions
                     where rubric_version_id =
                           '00000000-0000-0000-0000-000000000205'
                    """
                )
            ).mappings().one()
            assert all(value is None for value in legacy_compiled_rubric.values())

            quarantining_constraints = dict(
                connection.execute(
                    text(
                        """
                        select conname, convalidated
                          from pg_constraint
                         where conname = any(:constraint_names)
                        """
                    ),
                    {
                        "constraint_names": [
                            "essay_review_execution_fingerprint_check",
                            "essay_review_execution_attempt_check",
                            "essay_review_score_confidence_reasons_check",
                            "essay_review_grounding_report_check",
                            "essay_attempt_rubric_material_fk",
                            "essay_review_rubric_material_fk",
                            "essay_rubric_material_binding_check",
                            "essay_rubric_compilation_audit_check",
                        ]
                    },
                ).all()
            )
            assert len(quarantining_constraints) == 8
            assert not any(quarantining_constraints.values())

            null_guard_definitions = {
                name: definition.casefold()
                for name, definition in connection.execute(
                    text(
                        """
                        select conname, pg_get_constraintdef(oid)
                          from pg_constraint
                         where conname = any(:constraint_names)
                        """
                    ),
                    {
                        "constraint_names": [
                            "essay_review_score_confidence_reasons_check",
                            "essay_review_grounding_report_check",
                            "essay_rubric_material_binding_check",
                            "essay_rubric_compilation_audit_check",
                        ]
                    },
                ).all()
            }
            assert "score_confidence_reasons is not null" in (
                null_guard_definitions[
                    "essay_review_score_confidence_reasons_check"
                ]
            )
            grounding_definition = null_guard_definitions[
                "essay_review_grounding_report_check"
            ]
            assert "grounding_contract_version is not null" in (
                grounding_definition
            )
            assert "grounding_report is not null" in grounding_definition
            assert "compilation_input_fingerprint is not null" in (
                null_guard_definitions["essay_rubric_material_binding_check"]
            )
            assert "compilation_audit is not null" in (
                null_guard_definitions["essay_rubric_compilation_audit_check"]
            )

            essay_constraint_names = set(
                connection.execute(
                    text(
                        """
                        select constraint_name
                        from information_schema.table_constraints
                        where table_schema in ('product', 'learning')
                          and constraint_name like 'essay_%'
                        """
                    )
                ).scalars()
            )
            assert {
                "essay_answer_attempt_owner_task_fk",
                "essay_learning_artifact_answer_owner_task_fk",
                "essay_learning_artifact_material_task_fk",
                "essay_learning_artifact_review_binding_fk",
                "essay_learning_artifact_rubric_task_fk",
                "essay_learning_artifact_task_fk",
                "essay_answer_supersedes_same_owner_task_fk",
                "essay_attempt_material_task_fk",
                "essay_attempt_rubric_task_fk",
                "essay_material_supersedes_same_task_fk",
                "essay_review_answer_owner_task_fk",
                "essay_review_intent_answer_owner_task_fk",
                "essay_review_intent_attempt_content_fk",
                "essay_review_intent_material_task_fk",
                "essay_review_intent_rubric_material_fk",
                "essay_review_intent_supersedes_fk",
                "essay_review_supersedes_same_owner_task_fk",
                "essay_rewrite_base_answer_owner_task_fk",
                "essay_rewrite_new_answer_owner_task_fk",
                "essay_rewrite_review_owner_task_fk",
                "essay_transfer_answer_owner_task_fk",
                "essay_transfer_source_review_owner_fk",
                "essay_transfer_supersedes_same_scope_fk",
            }.issubset(essay_constraint_names)
            attempt_identity = next(
                constraint
                for constraint in inspect(db_engine).get_unique_constraints(
                    "essay_attempts",
                    schema="learning",
                )
                if constraint["name"] == "essay_attempt_content_identity_key"
            )
            assert attempt_identity["column_names"] == [
                "attempt_id",
                "user_id",
                "task_version_id",
                "material_bundle_version_id",
                "rubric_version_id",
            ]
            intent_content_fk = next(
                constraint
                for constraint in inspect(db_engine).get_foreign_keys(
                    "essay_review_intent_versions",
                    schema="learning",
                )
                if constraint["name"] == "essay_review_intent_attempt_content_fk"
            )
            assert intent_content_fk["constrained_columns"] == [
                "attempt_id",
                "user_id",
                "task_version_id",
                "material_bundle_version_id",
                "rubric_version_id",
            ]
            plan_columns = {
                column["name"]: column
                for column in inspect(db_engine).get_columns(
                    "daily_plan_versions",
                    schema="learning",
                )
            }
            assert plan_columns["due_reflection_count"]["nullable"] is True
            assert plan_columns["due_verification_count"]["nullable"] is True
            planning_constraint_names = set(
                connection.execute(
                    text(
                        """
                        select constraint_name
                        from information_schema.table_constraints
                        where table_schema = 'learning'
                          and (
                            constraint_name like 'daily_%'
                            or constraint_name like 'weekly_preference_%'
                            or constraint_name like 'task_execution_%'
                          )
                        """
                    )
                ).scalars()
            )
            assert {
                "daily_capacity_preference_owner_fk",
                "daily_capacity_supersedes_same_owner_date_fk",
                "daily_plan_capacity_owner_date_fk",
                "daily_plan_preference_owner_fk",
                "daily_plan_supersedes_same_owner_date_fk",
                "daily_plan_task_same_owner_date_version_fk",
                "task_execution_same_owner_task_date_version_fk",
                "task_execution_supersedes_same_owner_task_date_fk",
                "weekly_preference_supersedes_same_owner_fk",
            }.issubset(planning_constraint_names)
            append_only_tables = set(
                connection.execute(
                    text(
                        """
                        select n.nspname || '.' || c.relname
                        from pg_trigger t
                        join pg_class c on c.oid = t.tgrelid
                        join pg_namespace n on n.oid = c.relnamespace
                        where not t.tgisinternal
                          and t.tgname like '%_append_only'
                        """
                    )
                ).scalars()
            )
            assert {
                "ai_audit.essay_review_model_calls",
                "learning.essay_answer_versions",
                "learning.essay_learning_artifact_versions",
                "learning.essay_attempts",
                "learning.essay_review_intent_versions",
                "learning.essay_review_versions",
                "learning.essay_rewrite_versions",
                "learning.essay_transfer_verification_versions",
                "learning.daily_capacity_versions",
                "learning.daily_plan_tasks",
                "learning.daily_plan_versions",
                "learning.task_execution_versions",
                "learning.weekly_study_preference_versions",
                "learner.onboarding_commands",
                "learner.onboarding_progress_events",
                "product.essay_material_bundle_versions",
                "product.essay_material_segments",
                "product.essay_rubric_versions",
                "product.essay_task_versions",
            }.issubset(append_only_tables)
            assert connection.execute(
                text(
                    """
                    select exists (
                      select 1 from pg_trigger trigger
                      join pg_class relation on relation.oid = trigger.tgrelid
                      join pg_namespace namespace on namespace.oid = relation.relnamespace
                      where not trigger.tgisinternal
                        and namespace.nspname = 'learner'
                        and relation.relname = 'onboarding_progress'
                        and trigger.tgname = 'onboarding_progress_enforce_transition'
                    )
                    """
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    """
                    select exists (
                      select 1 from pg_trigger trigger
                      join pg_class relation on relation.oid = trigger.tgrelid
                      join pg_namespace namespace on namespace.oid = relation.relnamespace
                      where not trigger.tgisinternal
                        and namespace.nspname = 'ai_audit'
                        and relation.relname = 'model_call_ledger'
                        and trigger.tgname = 'model_call_ledger_enforce_transition'
                    )
                    """
                )
            ).scalar_one()
            assert connection.execute(
                text(
                    """
                    select exists (
                      select 1 from pg_trigger trigger
                      join pg_class relation on relation.oid = trigger.tgrelid
                      join pg_namespace namespace on namespace.oid = relation.relnamespace
                      where not trigger.tgisinternal
                        and namespace.nspname = 'platform'
                        and relation.relname = 'companion_paid_commands'
                        and trigger.tgname = 'companion_paid_command_enforce_transition'
                    )
                    """
                )
            ).scalar_one()

        planning_indexes = {
            index["name"]
            for table_name in (
                "weekly_study_preference_versions",
                "daily_capacity_versions",
                "daily_plan_versions",
                "daily_plan_tasks",
                "task_execution_versions",
                "essay_learning_artifact_versions",
                "essay_review_intent_versions",
            )
            for index in inspector.get_indexes(table_name, schema="learning")
        }
        assert {
            "learning_weekly_preference_current_idx",
            "learning_weekly_preference_one_successor_idx",
            "learning_daily_capacity_current_idx",
            "learning_daily_capacity_one_successor_idx",
            "learning_daily_plan_current_idx",
            "learning_daily_plan_one_successor_idx",
            "learning_daily_plan_task_track_idx",
            "learning_task_execution_current_idx",
            "learning_task_execution_one_successor_idx",
            "learning_task_execution_terminal_candidate_idx",
            "learning_essay_artifact_current_task_idx",
            "learning_essay_artifact_one_successor_idx",
            "learning_essay_review_intent_current_answer_idx",
            "learning_essay_review_intent_job_idx",
            "learning_essay_review_intent_one_successor_idx",
        }.issubset(planning_indexes)

        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into product.essay_task_versions (
                      task_version_id, task_key, version_no, question_type,
                      prompt, prompt_sha256, word_limit, maximum_score, state
                    ) values (
                      '00000000-0000-0000-0000-000000000101',
                      'migration-contract-task', 1, 'summary', 'prompt',
                      repeat('a', 64), 200, 20, 'draft'
                    )
                    """
                )
            )
        with pytest.raises(DBAPIError, match="append-only"):
            with db_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        update product.essay_task_versions
                        set prompt = 'mutated'
                        where task_key = 'migration-contract-task'
                        """
                    )
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with db_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        delete from product.essay_task_versions
                        where task_key = 'migration-contract-task'
                        """
                    )
                )

        with db_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    insert into product.essay_material_bundle_versions (
                      material_bundle_version_id, task_version_id, task_key,
                      version_no, completeness, source_kind, content_sha256
                    ) values (
                      '00000000-0000-0000-0000-000000000209',
                      '00000000-0000-0000-0000-000000000202',
                      'legacy-grounding-task', 2, 'complete', 'official',
                      repeat('9', 64)
                    );
                    insert into product.essay_rubric_versions (
                      rubric_version_id, task_version_id, task_key,
                      material_bundle_version_id, version_no, source_kind,
                      verification_status, state, total_maximum, criteria,
                      points, content_sha256
                    ) values (
                      '00000000-0000-0000-0000-000000000210',
                      '00000000-0000-0000-0000-000000000202',
                      'legacy-grounding-task',
                      '00000000-0000-0000-0000-000000000209',
                      3, 'official', 'verified', 'published', 20,
                      '[{"id":"criterion-new"}]'::jsonb,
                      '[{"id":"point-new"}]'::jsonb, repeat('a', 64)
                    );
                    """
                )
            )
        with pytest.raises(DBAPIError, match="foreign key"):
            with db_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        insert into learning.essay_review_intent_versions (
                          intent_version_id, intent_id, command_id, user_id,
                          attempt_id, answer_version_id, task_version_id,
                          material_bundle_version_id, rubric_version_id,
                          review_scope, version_no, status, input_fingerprint,
                          intent_fingerprint, state_fingerprint
                        ) values (
                          '00000000-0000-0000-0000-000000000211',
                          '00000000-0000-0000-0000-000000000212',
                          'mismatched-attempt-content-intent',
                          '00000000-0000-0000-0000-000000000201',
                          '00000000-0000-0000-0000-000000000206',
                          '00000000-0000-0000-0000-000000000207',
                          '00000000-0000-0000-0000-000000000202',
                          '00000000-0000-0000-0000-000000000209',
                          '00000000-0000-0000-0000-000000000210',
                          'grounded_full', 1, 'pending', repeat('b', 64),
                          repeat('c', 64), repeat('d', 64)
                        )
                        """
                    )
                )

        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "drop trigger mock_exam_session_supersessions_append_only "
                    "on learner.mock_exam_session_supersessions"
                )
            )
            connection.execute(
                text(
                    "delete from learner.mock_exam_session_supersessions "
                    "where owner_user_id = "
                    "'00000000-0000-0000-0000-000000000241'"
                )
            )
            connection.execute(
                text(
                    "delete from learner.mock_exam_sessions "
                    "where user_id = '00000000-0000-0000-0000-000000000241'"
                )
            )
            connection.execute(
                text(
                    "delete from product.question_content_heads "
                    "where question_id = 'q-legacy-duplicate-mock'"
                )
            )
            connection.execute(
                text(
                    "drop trigger question_content_versions_append_only "
                    "on product.question_content_versions"
                )
            )
            connection.execute(
                text(
                    "delete from product.question_content_versions "
                    "where question_id = 'q-legacy-duplicate-mock'"
                )
            )
        command.downgrade(config, "20260713_09")
        downgraded = inspect(db_engine)
        assert set(downgraded.get_table_names(schema="learning")) == {
            "ability_estimate_versions",
            "score_evidence",
        }
        assert "essay_task_versions" not in downgraded.get_table_names(
            schema="product"
        )
        command.upgrade(config, "20260713_10")
        assert "essay_review_versions" in inspect(db_engine).get_table_names(
            schema="learning"
        )
    finally:
        with db_engine.begin() as connection:
            connection.execute(text("drop table if exists public.alembic_version"))


def test_stage15_migration_refuses_destructive_downgrade_with_facts(
    db_engine: Engine,
) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", db_engine.url.render_as_string(hide_password=False)
    )
    command.stamp(config, "20260715_20")
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values ('00000000-0000-0000-0000-000000000231', 'stage15-user');

                insert into learning.source_artifact_versions (
                  artifact_version_id, artifact_id, owner_user_id, version_no,
                  source_kind, state, object_ref, mime_type, content_sha256,
                  byte_size, page_count, supersedes_artifact_version_id,
                  contract_payload, created_at
                ) values (
                  '00000000-0000-0000-0000-000000000232',
                  '00000000-0000-0000-0000-000000000233',
                  '00000000-0000-0000-0000-000000000231', 1,
                  'camera_image', 'uploaded', 'private/user/object.jpg',
                  'image/jpeg', repeat('a', 64), 128, null, null,
                  '{"contractVersion":"source-artifact@v1"}'::jsonb, now()
                )
                """
            )
        )

    with pytest.raises(RuntimeError, match="destructive downgrade is forbidden"):
        command.downgrade(config, "20260715_19")

    assert "source_artifact_versions" in inspect(db_engine).get_table_names(
        schema="learning"
    )
    with db_engine.begin() as connection:
        connection.execute(text("drop table if exists public.alembic_version"))


def test_stage35_community_method_card_migration(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = import_module(
        "migrations.versions.20260716_28_community_method_cards"
    )
    existing = set(inspect(db_engine).get_table_names(schema="product"))
    if "community_method_cards" not in existing:
        with db_engine.begin() as connection:
            for statement in migration._STATEMENTS:
                connection.execute(text(statement))

    product_tables = set(inspect(db_engine).get_table_names(schema="product"))
    commerce_tables = set(inspect(db_engine).get_table_names(schema="commerce"))
    assert {
        "community_method_cards",
        "community_method_card_versions",
        "community_method_card_candidates",
        "community_method_card_reviews",
        "community_method_card_review_checks",
        "community_method_card_exposures",
        "community_method_card_trials",
        "community_method_card_votes",
        "community_method_card_reports",
        "community_method_card_effect_policies",
        "community_method_card_effect_projections",
        "community_method_card_milestone_events",
    } <= product_tables
    assert {
        "community_reward_policies",
        "community_reward_awards",
        "community_reward_cap_buckets",
        "community_reward_ledger_entries",
    } <= commerce_tables
    assert "method_cards" in product_tables
    with db_engine.begin() as connection:
        append_only_triggers = set(
            connection.execute(
                text(
                    """
                    select trigger_name
                    from information_schema.triggers
                    where trigger_schema in ('product', 'commerce')
                      and trigger_name like 'community_%_append_only'
                    """
                )
            ).scalars()
        )
    assert {
        "community_method_card_versions_append_only",
        "community_method_card_trials_append_only",
        "community_reward_policies_append_only",
        "community_reward_ledger_entries_append_only",
    } <= append_only_triggers

    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(
            text(
                """
                insert into learner.users (user_id, display_name)
                values ('00000000-0000-0000-0000-000000000351', 'stage35-author')
                on conflict (user_id) do nothing;

                insert into product.community_method_cards (
                  card_id, author_user_id, track, state,
                  head_version_id, state_version, created_at, updated_at
                ) values (
                  '00000000-0000-0000-0000-000000000352',
                  '00000000-0000-0000-0000-000000000351',
                  'xingce', 'private_draft', null, 1, now(), now()
                )
                """
            )
        )
        assert migration._has_records(connection) is True
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        with pytest.raises(RuntimeError, match="destructive downgrade is forbidden"):
            migration.downgrade()
    finally:
        transaction.rollback()
        connection.close()
