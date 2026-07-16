from __future__ import annotations

from importlib import import_module

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError


def _seed_trial(connection: object, *, delayed_outcome: str | None) -> None:
    assert delayed_outcome in {None, "passed"}
    legacy_column = ", delayed_verification_outcome" if delayed_outcome else ""
    legacy_value = ", 'passed'" if delayed_outcome else ""
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        f"""
            insert into learner.users (user_id, display_name)
            values
              ('00000000-0000-0000-0000-000000000361', 'stage35-author'),
              ('00000000-0000-0000-0000-000000000362', 'stage35-learner')
            on conflict (user_id) do nothing;

            insert into product.community_method_cards (
              card_id, author_user_id, track, state, head_version_id,
              state_version, created_at, updated_at
            ) values (
              '00000000-0000-0000-0000-000000000363',
              '00000000-0000-0000-0000-000000000361',
              'xingce', 'trial', null, 1, now(), now()
            );

            insert into product.community_method_card_versions (
              version_id, card_id, version_no, author_user_id, base_version_id,
              source_type, source_text_sha256, private_source_text, title,
              recognition, method_steps, applicability, exclusions, pitfalls,
              rights_declaration, author_confirmed_at, created_at
            ) values (
              '00000000-0000-0000-0000-000000000364',
              '00000000-0000-0000-0000-000000000363', 1,
              '00000000-0000-0000-0000-000000000361', null, 'text',
              repeat('a', 64), 'private source', 'title', 'recognition',
              '["step"]'::jsonb, '["logic"]'::jsonb, '[]'::jsonb, '[]'::jsonb,
              'original', now(), now()
            );

            update product.community_method_cards
            set head_version_id = '00000000-0000-0000-0000-000000000364'
            where card_id = '00000000-0000-0000-0000-000000000363';

            insert into product.community_method_card_exposures (
              exposure_id, card_id, version_id, learner_user_id,
              applicability_snapshot, ranking_policy_version, opted_in_at
            ) values (
              '00000000-0000-0000-0000-000000000365',
              '00000000-0000-0000-0000-000000000363',
              '00000000-0000-0000-0000-000000000364',
              '00000000-0000-0000-0000-000000000362',
              '["logic"]'::jsonb, 'ranking-test-v1', now()
            );

            insert into product.community_method_card_trials (
              trial_id, exposure_id, card_id, version_id, learner_user_id,
              question_attempt_id, question_version_id, correct_before,
              correct_after, duration_before_ms, duration_after_ms,
              eligibility_status, exclusion_reason, device_cluster_hash,
              delayed_verification_due_at{legacy_column},
              completed_at
            ) values (
              '00000000-0000-0000-0000-000000000366',
              '00000000-0000-0000-0000-000000000365',
              '00000000-0000-0000-0000-000000000363',
              '00000000-0000-0000-0000-000000000364',
              '00000000-0000-0000-0000-000000000362',
              'attempt-independent-001', 'question-version-001', false, true,
              90000, 60000, 'eligible', null, repeat('b', 64), now(),
              {legacy_value[2:] + ',' if legacy_value else ''} now()
            )
            """
    )


def test_stage35_delayed_verification_is_independent_and_append_only(
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration28 = import_module(
        "migrations.versions.20260716_28_community_method_cards"
    )
    migration29 = import_module(
        "migrations.versions.20260716_29_community_delayed_verifications"
    )
    product_tables = set(inspect(db_engine).get_table_names(schema="product"))
    if "community_method_cards" not in product_tables:
        with db_engine.begin() as connection:
            for statement in migration28._STATEMENTS:
                connection.execute(text(statement))

    product_tables = set(inspect(db_engine).get_table_names(schema="product"))
    if "community_method_card_delayed_verifications" not in product_tables:
        connection = db_engine.connect()
        transaction = connection.begin()
        try:
            _seed_trial(connection, delayed_outcome="passed")
            with pytest.raises(RuntimeError, match="refuses to invent"):
                migration29._assert_no_mutable_outcomes(connection)
        finally:
            transaction.rollback()
            connection.close()
        with db_engine.begin() as connection:
            migration29._assert_no_mutable_outcomes(connection)
            for statement in migration29._STATEMENTS:
                connection.execute(text(statement))

    assert "community_method_card_delayed_verifications" in set(
        inspect(db_engine).get_table_names(schema="product")
    )
    trial_columns = {
        item["name"]
        for item in inspect(db_engine).get_columns(
            "community_method_card_trials", schema="product"
        )
    }
    assert "delayed_verification_outcome" not in trial_columns

    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        _seed_trial(connection, delayed_outcome=None)
        connection.execute(
            text(
                """
                insert into product.community_method_card_delayed_verifications (
                  verification_id, trial_id, question_attempt_id,
                  question_version_id, correct_after, duration_after_ms,
                  outcome, evidence_refs, completed_at
                ) values (
                  '00000000-0000-0000-0000-000000000367',
                  '00000000-0000-0000-0000-000000000366',
                  'attempt-delayed-001', 'question-version-002', true, 55000,
                  'passed', '["attempt-delayed-001"]'::jsonb, now()
                )
                """
            )
        )
        assert migration29._has_records(connection) is True
        monkeypatch.setattr(migration29.op, "get_bind", lambda: connection)
        with pytest.raises(RuntimeError, match="destructive downgrade is forbidden"):
            migration29.downgrade()

        savepoint = connection.begin_nested()
        try:
            with pytest.raises(DBAPIError, match="append-only"):
                connection.execute(
                    text(
                        """
                        update product.community_method_card_delayed_verifications
                        set outcome = 'failed'
                        where verification_id =
                          '00000000-0000-0000-0000-000000000367'
                        """
                    )
                )
        finally:
            savepoint.rollback()
    finally:
        transaction.rollback()
        connection.close()
