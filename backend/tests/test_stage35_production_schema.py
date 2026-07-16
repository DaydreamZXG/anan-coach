from __future__ import annotations

from importlib import import_module

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError


def _apply_stage35_schema(engine: Engine) -> object:
    migration28 = import_module(
        "migrations.versions.20260716_28_community_method_cards"
    )
    migration29 = import_module(
        "migrations.versions.20260716_29_community_delayed_verifications"
    )
    migration30 = import_module(
        "migrations.versions.20260716_30_community_production_closure"
    )
    with engine.begin() as connection:
        product_tables = set(inspect(connection).get_table_names(schema="product"))
        if "community_method_cards" not in product_tables:
            for statement in migration28._STATEMENTS:
                connection.execute(text(statement))
        product_tables = set(inspect(connection).get_table_names(schema="product"))
        if "community_method_card_delayed_verifications" not in product_tables:
            migration29._assert_no_mutable_outcomes(connection)
            for statement in migration29._STATEMENTS:
                connection.execute(text(statement))
        product_tables = set(inspect(connection).get_table_names(schema="product"))
        if "community_method_card_source_bindings" not in product_tables:
            for statement in migration30._STATEMENTS:
                connection.execute(text(statement))
    return migration30


def _seed_card_trial_and_report(connection: object) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        """
        insert into learner.users (user_id, display_name)
        values
          ('00000000-0000-0000-0000-000000000501', 'stage35d-author'),
          ('00000000-0000-0000-0000-000000000502', 'stage35d-learner'),
          ('00000000-0000-0000-0000-000000000503', 'stage35d-other')
        on conflict (user_id) do nothing;

        insert into product.questions (
          question_id, domain, status, stem, answer
        ) values ('stage35d-question', 'xingce', 'active', 'question', 'A')
        on conflict (question_id) do nothing;

        insert into product.question_content_versions (
          content_version_id, question_id, track, snapshot_payload,
          normalized_search_text, created_at
        ) values (
          repeat('1', 64), 'stage35d-question', 'xingce',
          '{"answer":"A"}'::jsonb, 'stage35d question A', now()
        ) on conflict (content_version_id) do nothing;

        insert into product.community_method_cards (
          card_id, author_user_id, track, state, head_version_id,
          state_version, created_at, updated_at
        ) values (
          '00000000-0000-0000-0000-000000000504',
          '00000000-0000-0000-0000-000000000501',
          'xingce', 'trial', null, 2, now(), now()
        );

        insert into product.community_method_card_versions (
          version_id, card_id, version_no, author_user_id, base_version_id,
          source_type, source_text_sha256, private_source_text, title,
          recognition, method_steps, applicability, exclusions, pitfalls,
          rights_declaration, author_confirmed_at, created_at
        ) values (
          '00000000-0000-0000-0000-000000000505',
          '00000000-0000-0000-0000-000000000504', 1,
          '00000000-0000-0000-0000-000000000501', null, 'text',
          repeat('a', 64), 'private source', 'method', 'recognition',
          '["step"]'::jsonb, '["logic"]'::jsonb, '[]'::jsonb, '[]'::jsonb,
          'original', now(), now()
        );

        update product.community_method_cards
        set head_version_id = '00000000-0000-0000-0000-000000000505'
        where card_id = '00000000-0000-0000-0000-000000000504';

        insert into product.community_method_card_exposures (
          exposure_id, card_id, version_id, learner_user_id,
          applicability_snapshot, ranking_policy_version, opted_in_at
        ) values (
          '00000000-0000-0000-0000-000000000506',
          '00000000-0000-0000-0000-000000000504',
          '00000000-0000-0000-0000-000000000505',
          '00000000-0000-0000-0000-000000000502',
          '["logic"]'::jsonb, 'ranking@stage35d', now()
        );

        insert into product.community_method_card_trials (
          trial_id, exposure_id, card_id, version_id, learner_user_id,
          question_attempt_id, question_version_id, correct_before,
          correct_after, duration_before_ms, duration_after_ms,
          eligibility_status, exclusion_reason, device_cluster_hash,
          delayed_verification_due_at, completed_at
        ) values (
          '00000000-0000-0000-0000-000000000507',
          '00000000-0000-0000-0000-000000000506',
          '00000000-0000-0000-0000-000000000504',
          '00000000-0000-0000-0000-000000000505',
          '00000000-0000-0000-0000-000000000502',
          'attempt-stage35d', repeat('1', 64), false, true,
          90000, 60000, 'eligible', null, repeat('b', 64), now(), now()
        );

        insert into product.community_method_card_reports (
          report_id, card_id, version_id, reporter_user_id, category,
          evidence_ref, evidence_sha256, decision, created_at
        ) values (
          '00000000-0000-0000-0000-000000000508',
          '00000000-0000-0000-0000-000000000504',
          '00000000-0000-0000-0000-000000000505',
          '00000000-0000-0000-0000-000000000502',
          'incorrect', 'evidence://stage35d/report', repeat('c', 64),
          'pending', now()
        );
        """
    )


def _seed_policy_resources(connection: object) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        """
        insert into product.community_method_card_policy_resources (
          policy_resource_id, operation, policy_version, state, rules,
          rules_sha256, prompt_version, provider, model,
          provider_release_id, output_schema_version, published_at, created_at
        ) values
          (
            '00000000-0000-0000-0000-000000000510',
            'candidate_generation', 'candidate@stage35d', 'published',
            '{"sourceGrounding":true}'::jsonb, repeat('d', 64),
            'candidate-prompt@stage35d', 'provider-stage35d', 'model-stage35d',
            'provider-release@stage35d', 'candidate-schema@stage35d', now(), now()
          ),
          (
            '00000000-0000-0000-0000-000000000511',
            'automated_review', 'review@stage35d', 'published',
            '{"checks":5}'::jsonb, repeat('e', 64),
            'review-prompt@stage35d', 'provider-stage35d', 'model-stage35d',
            'provider-release@stage35d', 'review-schema@stage35d', now(), now()
          ),
          (
            '00000000-0000-0000-0000-000000000512',
            'report_decision', 'report@stage35d', 'published',
            '{"beneficiaryDisclosure":true}'::jsonb, repeat('f', 64),
            'report-prompt@stage35d', 'provider-stage35d', 'model-stage35d',
            'provider-release@stage35d', 'report-schema@stage35d', now(), now()
          );
        """
    )


def _seed_reward_lots(connection: object) -> None:
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        """
        insert into product.community_method_card_milestone_events (
          event_id, author_user_id, card_id, version_id, milestone,
          evidence_refs, occurred_at
        ) values
          (
            '00000000-0000-0000-0000-000000000520',
            '00000000-0000-0000-0000-000000000501',
            '00000000-0000-0000-0000-000000000504',
            '00000000-0000-0000-0000-000000000505',
            'automated_review_passed', '["review:stage35d"]'::jsonb, now()
          ),
          (
            '00000000-0000-0000-0000-000000000521',
            '00000000-0000-0000-0000-000000000501',
            '00000000-0000-0000-0000-000000000504',
            '00000000-0000-0000-0000-000000000505',
            'independent_evidence_sufficient', '["trial:stage35d"]'::jsonb, now()
          );

        insert into commerce.community_reward_policies (
          policy_id, policy_version, milestone, reward_unit, reward_value,
          author_period_cap, card_period_cap, milestone_period_cap,
          global_period_cap, period_kind, state, rules_sha256,
          published_at, created_at
        ) values
          (
            '00000000-0000-0000-0000-000000000522', 'ai-reward@stage35d',
            'automated_review_passed', 'ai_quota', 10,
            100, 100, 100, 1000, 'campaign', 'published', repeat('1', 64),
            now(), now()
          ),
          (
            '00000000-0000-0000-0000-000000000523', 'member-reward@stage35d',
            'independent_evidence_sufficient', 'membership_seconds', 3600,
            36000, 36000, 36000, 360000, 'campaign', 'published', repeat('2', 64),
            now(), now()
          );

        insert into commerce.community_reward_policy_scopes (
          scope_resource_id, policy_id, reward_unit, scope_version, scope_kind,
          eligible_ai_features, membership_product_ref,
          membership_activation_contract_version, membership_cost_policy_version,
          cost_metadata, rules_sha256, created_at
        ) values
          (
            '00000000-0000-0000-0000-000000000524',
            '00000000-0000-0000-0000-000000000522', 'ai_quota',
            'ai-scope@stage35d', 'ai_features', '["community_candidate"]'::jsonb,
            null, null, null, null, repeat('3', 64), now()
          ),
          (
            '00000000-0000-0000-0000-000000000525',
            '00000000-0000-0000-0000-000000000523', 'membership_seconds',
            'member-scope@stage35d', 'membership_activation', null,
            'founder-membership', 'membership-activation@stage35d',
            'membership-cost@stage35d', '{"currency":"CNY","basis":"policy"}'::jsonb,
            repeat('4', 64), now()
          );

        insert into commerce.community_reward_awards (
          award_id, event_id, policy_id, beneficiary_user_id, card_id,
          version_id, milestone, period_start, reward_unit, granted_value,
          consumed_value, reversed_value, status, evidence_refs,
          entitlement_before, entitlement_after, cost_minor_units, created_at
        ) values
          (
            '00000000-0000-0000-0000-000000000526',
            '00000000-0000-0000-0000-000000000520',
            '00000000-0000-0000-0000-000000000522',
            '00000000-0000-0000-0000-000000000501',
            '00000000-0000-0000-0000-000000000504',
            '00000000-0000-0000-0000-000000000505',
            'automated_review_passed', current_date, 'ai_quota', 10, 0, 0,
            'granted', '["event:stage35d-ai"]'::jsonb, '{}'::jsonb, '{}'::jsonb,
            0, now()
          ),
          (
            '00000000-0000-0000-0000-000000000527',
            '00000000-0000-0000-0000-000000000521',
            '00000000-0000-0000-0000-000000000523',
            '00000000-0000-0000-0000-000000000501',
            '00000000-0000-0000-0000-000000000504',
            '00000000-0000-0000-0000-000000000505',
            'independent_evidence_sufficient', current_date,
            'membership_seconds', 3600, 0, 0, 'granted',
            '["event:stage35d-membership"]'::jsonb, '{}'::jsonb, '{}'::jsonb,
            0, now()
          );

        insert into commerce.community_reward_ledger_entries (
          ledger_entry_id, award_id, beneficiary_user_id, entry_type,
          reward_unit, value_delta, idempotency_key, reason_code,
          balance_before, balance_after, evidence_refs, created_at
        ) values
          (
            '00000000-0000-0000-0000-000000000528',
            '00000000-0000-0000-0000-000000000526',
            '00000000-0000-0000-0000-000000000501', 'consume', 'ai_quota', -3,
            'stage35d-ai-consume', 'provider_usage', 10, 7,
            '["usage:stage35d"]'::jsonb, now()
          ),
          (
            '00000000-0000-0000-0000-000000000529',
            '00000000-0000-0000-0000-000000000527',
            '00000000-0000-0000-0000-000000000501', 'consume',
            'membership_seconds', -3600, 'stage35d-member-consume',
            'membership_activation', 3600, 0,
            '["activation:stage35d"]'::jsonb, now()
          );
        """
    )


def test_stage35_production_schema_and_forward_only_downgrade(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration30 = _apply_stage35_schema(db_engine)

    product_tables = set(inspect(db_engine).get_table_names(schema="product"))
    commerce_tables = set(inspect(db_engine).get_table_names(schema="commerce"))
    assert {
        "community_method_card_source_bindings",
        "community_method_card_validation_bindings",
        "community_method_card_policy_resources",
        "community_method_card_state_events",
        "community_method_card_trial_session_evidence",
        "community_method_card_eligibility_decisions",
        "community_method_card_report_decisions",
    } <= product_tables
    assert {
        "community_reward_policy_scopes",
        "community_reward_ai_usage_reservations",
        "community_reward_ai_usage_reservation_events",
        "community_reward_membership_activations",
    } <= commerce_tables
    source_binding_columns = {
        column["name"]
        for column in inspect(db_engine).get_columns(
            "community_method_card_source_bindings", schema="product"
        )
    }
    assert "learning_media_artifact_version_id" in source_binding_columns
    learning_media_unique_constraints = {
        constraint["name"]
        for constraint in inspect(db_engine).get_unique_constraints(
            "learning_media_artifact_versions", schema="learning"
        )
    }
    assert "learning_media_artifact_versions_owner_key" in (
        learning_media_unique_constraints
    )

    with db_engine.connect() as connection:
        assert connection.execute(
            text("select count(*) from product.community_method_card_policy_resources")
        ).scalar_one() == 0
        assert connection.execute(
            text("select count(*) from commerce.community_reward_policy_scopes")
        ).scalar_one() == 0

    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        _seed_card_trial_and_report(connection)
        _seed_policy_resources(connection)

        connection.exec_driver_sql(
            """
            insert into product.community_method_card_source_bindings (
              source_binding_id, card_id, version_id, owner_user_id,
              binding_kind, source_role, direct_text_sha256,
              source_span_refs, bound_at
            ) values (
              '00000000-0000-0000-0000-000000000530',
              '00000000-0000-0000-0000-000000000504',
              '00000000-0000-0000-0000-000000000505',
              '00000000-0000-0000-0000-000000000501',
              'direct_text', 'primary', repeat('a', 64),
              '[{"start":0,"end":14}]'::jsonb, now()
            );

            insert into product.community_method_card_validation_bindings (
              validation_binding_id, card_id, version_id, owner_user_id, track,
              validation_scope, knowledge_node_version_ref,
              canonical_question_id, canonical_content_version_id,
              answer_evidence_refs, validation_policy_version, bound_at
            ) values (
              '00000000-0000-0000-0000-000000000531',
              '00000000-0000-0000-0000-000000000504',
              '00000000-0000-0000-0000-000000000505',
              '00000000-0000-0000-0000-000000000501', 'xingce',
              'canonical_question', 'knowledge-node@stage35d',
              'stage35d-question', repeat('1', 64),
              '["answer:A","question-version:stage35d"]'::jsonb,
              'validation@stage35d', now()
            );

            insert into product.community_method_card_state_events (
              state_event_id, card_id, version_id, state_version,
              from_state, to_state, actor_kind, actor_user_id,
              reason_code, authority_version, evidence_refs, occurred_at
            ) values
              (
                '00000000-0000-0000-0000-000000000532',
                '00000000-0000-0000-0000-000000000504',
                '00000000-0000-0000-0000-000000000505', 1,
                null, 'private_draft', 'author',
                '00000000-0000-0000-0000-000000000501',
                'author_confirmed', 'state-authority@stage35d',
                '["version:stage35d"]'::jsonb, now()
              ),
              (
                '00000000-0000-0000-0000-000000000533',
                '00000000-0000-0000-0000-000000000504',
                '00000000-0000-0000-0000-000000000505', 2,
                'private_draft', 'trial', 'system', null,
                'review_passed', 'state-authority@stage35d',
                '["review:stage35d"]'::jsonb, now()
              );

            insert into product.community_method_card_trial_session_evidence (
              session_evidence_id, trial_id, card_id, version_id,
              learner_user_id, server_session_kind, server_session_id,
              session_evidence_sha256, attestation_policy_version,
              evidence_refs, recorded_at
            ) values (
              '00000000-0000-0000-0000-000000000534',
              '00000000-0000-0000-0000-000000000507',
              '00000000-0000-0000-0000-000000000504',
              '00000000-0000-0000-0000-000000000505',
              '00000000-0000-0000-0000-000000000502',
              'community_trial_session', 'server-session-stage35d',
              repeat('5', 64), 'session-attestation@stage35d',
              '["server-session:stage35d"]'::jsonb, now()
            );

            insert into product.community_method_card_eligibility_decisions (
              eligibility_decision_id, session_evidence_id, trial_id, card_id,
              version_id, learner_user_id, actor_cluster_hash,
              cluster_policy_version, decision, reason_code,
              decision_snapshot, evidence_refs, decided_at
            ) values (
              '00000000-0000-0000-0000-000000000535',
              '00000000-0000-0000-0000-000000000534',
              '00000000-0000-0000-0000-000000000507',
              '00000000-0000-0000-0000-000000000504',
              '00000000-0000-0000-0000-000000000505',
              '00000000-0000-0000-0000-000000000502', repeat('6', 64),
              'actor-cluster@stage35d', 'eligible', null,
              '{"sameAuthorCluster":false,"serverAttested":true}'::jsonb,
              '["session-evidence:stage35d"]'::jsonb, now()
            );

            insert into product.community_method_card_report_decisions (
              report_decision_id, report_id, card_id, version_id,
              reporter_user_id, decision_version, decision, reason_code,
              policy_resource_id, reporter_is_beneficiary,
              beneficiary_user_id, evidence_refs, actor_ref, decided_at
            ) values (
              '00000000-0000-0000-0000-000000000536',
              '00000000-0000-0000-0000-000000000508',
              '00000000-0000-0000-0000-000000000504',
              '00000000-0000-0000-0000-000000000505',
              '00000000-0000-0000-0000-000000000502', 1,
              'confirmed', 'answer_conflict_confirmed',
              '00000000-0000-0000-0000-000000000512', false, null,
              '["evidence://stage35d/report"]'::jsonb,
              'moderator:stage35d', now()
            );
            """
        )

        savepoint = connection.begin_nested()
        try:
            with pytest.raises(DBAPIError):
                connection.exec_driver_sql(
                    """
                        insert into product.community_method_card_source_bindings (
                          source_binding_id, card_id, version_id, owner_user_id,
                          binding_kind, source_role, direct_text_sha256,
                          source_span_refs, bound_at
                        ) values (
                          '00000000-0000-0000-0000-000000000537',
                          '00000000-0000-0000-0000-000000000504',
                          '00000000-0000-0000-0000-000000000505',
                          '00000000-0000-0000-0000-000000000503',
                          'direct_text', 'supporting', repeat('7', 64),
                          '[{"start":0,"end":1}]'::jsonb, now()
                        )
                    """
                )
        finally:
            savepoint.rollback()

        savepoint = connection.begin_nested()
        try:
            with pytest.raises(DBAPIError, match="append-only"):
                connection.execute(
                    text(
                        """
                        update product.community_method_card_validation_bindings
                        set knowledge_node_version_ref = 'knowledge-node@mutated'
                        where validation_binding_id =
                          '00000000-0000-0000-0000-000000000531'
                        """
                    )
                )
        finally:
            savepoint.rollback()

        savepoint = connection.begin_nested()
        try:
            with pytest.raises(DBAPIError):
                connection.exec_driver_sql(
                    """
                        insert into product.community_method_card_policy_resources (
                          policy_resource_id, operation, policy_version, state, rules,
                          rules_sha256, prompt_version, provider, model,
                          provider_release_id, output_schema_version,
                          published_at, created_at
                        ) values (
                          '00000000-0000-0000-0000-000000000538',
                          'automated_review', 'review-duplicate@stage35d', 'published',
                          '{"checks":5}'::jsonb, repeat('8', 64),
                          'review-prompt@stage35d', 'provider-stage35d', 'model-stage35d',
                          'provider-release@stage35d', 'review-schema@stage35d',
                          now(), now()
                        )
                    """
                )
        finally:
            savepoint.rollback()

        _seed_reward_lots(connection)
        connection.exec_driver_sql(
            """
            insert into commerce.community_reward_ai_usage_reservations (
              reservation_id, award_id, beneficiary_user_id,
              scope_resource_id, policy_id, eligible_feature,
              reserved_value, reservation_key, request_fingerprint,
              server_session_ref, evidence_refs, reserved_at
            ) values (
              '00000000-0000-0000-0000-000000000540',
              '00000000-0000-0000-0000-000000000526',
              '00000000-0000-0000-0000-000000000501',
              '00000000-0000-0000-0000-000000000524',
              '00000000-0000-0000-0000-000000000522',
              'community_candidate', 3, 'stage35d-ai-reservation', repeat('9', 64),
              'server-session-stage35d-ai', '["request:stage35d-ai"]'::jsonb, now()
            );

            insert into commerce.community_reward_ai_usage_reservation_events (
              reservation_event_id, reservation_id, award_id,
              beneficiary_user_id, reserved_value, event_type, ledger_entry_id,
              reason_code, provider_usage_ref, evidence_refs, occurred_at
            ) values (
              '00000000-0000-0000-0000-000000000541',
              '00000000-0000-0000-0000-000000000540',
              '00000000-0000-0000-0000-000000000526',
              '00000000-0000-0000-0000-000000000501', 3, 'consumed',
              '00000000-0000-0000-0000-000000000528',
              'provider_completed', 'provider-usage-stage35d',
              '["usage:stage35d"]'::jsonb, now()
            );

            insert into commerce.community_reward_membership_activations (
              membership_activation_id, award_id, beneficiary_user_id,
              scope_resource_id, policy_id, ledger_entry_id, activation_seconds,
              activation_key, membership_entitlement_ref,
              purchase_history_snapshot_ref, prior_expires_at,
              activated_from, activated_until, cost_metadata_snapshot,
              evidence_refs, activated_at
            ) values (
              '00000000-0000-0000-0000-000000000542',
              '00000000-0000-0000-0000-000000000527',
              '00000000-0000-0000-0000-000000000501',
              '00000000-0000-0000-0000-000000000525',
              '00000000-0000-0000-0000-000000000523',
              '00000000-0000-0000-0000-000000000529', 3600,
              'stage35d-member-activation', 'membership-entitlement-stage35d',
              'purchase-history-snapshot-stage35d',
              '2026-07-16T00:00:00Z', '2026-07-16T00:00:00Z',
              '2026-07-16T01:00:00Z',
              '{"currency":"CNY","basis":"policy"}'::jsonb,
              '["ledger:stage35d-membership"]'::jsonb, now()
            );
            """
        )

        assert str(
            connection.execute(
                text(
                    """
                    select award_id from commerce.community_reward_ai_usage_reservations
                    where reservation_id = '00000000-0000-0000-0000-000000000540'
                    """
                )
            ).scalar_one()
        ) == "00000000-0000-0000-0000-000000000526"
        assert connection.execute(
            text(
                """
                select purchase_history_snapshot_ref
                from commerce.community_reward_membership_activations
                where membership_activation_id =
                  '00000000-0000-0000-0000-000000000542'
                """
            )
        ).scalar_one() == "purchase-history-snapshot-stage35d"

        savepoint = connection.begin_nested()
        try:
            with pytest.raises(DBAPIError, match="not eligible"):
                connection.exec_driver_sql(
                    """
                        insert into commerce.community_reward_ai_usage_reservations (
                          reservation_id, award_id, beneficiary_user_id,
                          scope_resource_id, policy_id, eligible_feature,
                          reserved_value, reservation_key, request_fingerprint,
                          server_session_ref, evidence_refs, reserved_at
                        ) values (
                          '00000000-0000-0000-0000-000000000543',
                          '00000000-0000-0000-0000-000000000526',
                          '00000000-0000-0000-0000-000000000501',
                          '00000000-0000-0000-0000-000000000524',
                          '00000000-0000-0000-0000-000000000522',
                          'unpublished_feature', 1, 'stage35d-invalid-feature',
                          repeat('0', 64), 'server-session-stage35d-invalid',
                          '["request:invalid"]'::jsonb, now()
                        )
                    """
                )
        finally:
            savepoint.rollback()

        assert migration30._has_records(connection) is True
        monkeypatch.setattr(migration30.op, "get_bind", lambda: connection)
        with pytest.raises(RuntimeError, match="destructive downgrade is forbidden"):
            migration30.downgrade()
    finally:
        transaction.rollback()
        connection.close()
