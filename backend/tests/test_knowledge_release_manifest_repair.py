from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError


migration = import_module(
    "migrations.versions.20260815_113_restore_legacy_knowledge_release_manifest"
)


def _insert_release(connection: Connection, values: dict[str, Any]) -> None:
    connection.execute(
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
              activated_at,
              retired_at,
              supersedes_release_id,
              question_binding_count,
              question_binding_fingerprint
            ) values (
              cast(:release_id as uuid),
              :release_version,
              :exam_scope,
              :track,
              :source_data_version,
              :vocab_version,
              :generator_version,
              :source_fingerprint,
              :generated_at,
              :fresh_until,
              :status,
              :node_count,
              :activated_at,
              :retired_at,
              cast(:supersedes_release_id as uuid),
              :question_binding_count,
              :question_binding_fingerprint
            )
            """
        ),
        values,
    )


def _v4_release() -> dict[str, Any]:
    return {
        "release_id": "145d1371-f9dd-4bc5-9fe4-674921b4bb3a",
        "release_version": "knowledge-map@20260805T000000Z-v4",
        "exam_scope": "guokao",
        "track": "xingce",
        "source_data_version": "product-qbank@20260727",
        "vocab_version": "vocab@v1",
        "generator_version": "refresh_knowledge_map_nodes_v4",
        "source_fingerprint": ("38f8c22cfe9c0891af918b15961f2e84bd82a385bcae250384697623c6270959"),
        "generated_at": migration.datetime.fromisoformat("2026-08-05T22:48:13.592579+08:00"),
        "fresh_until": migration.datetime.fromisoformat("2027-01-27T08:00:00+08:00"),
        "status": "retired",
        "node_count": 640,
        "activated_at": migration.datetime.fromisoformat("2026-08-05T22:48:13.592579+08:00"),
        "retired_at": migration.datetime.fromisoformat("2026-08-05T23:35:26.386514+08:00"),
        "supersedes_release_id": migration._SUCCESSOR_RELEASE_ID,
        "question_binding_count": 158366,
        "question_binding_fingerprint": (
            "6098aa97fa606ac046c6a49851309d73a5ce1dc9f245980e1f3f2252be4db458"
        ),
    }


def _seed_exact_gap(
    connection: Connection,
    *,
    node_count: int = 3,
    successor_overrides: dict[str, Any] | None = None,
) -> migration._RepairEvidence:
    production = migration._PRODUCTION_EVIDENCE
    timestamp = production.legacy_release["generated_at"]
    connection.exec_driver_sql("set local session_replication_role = replica")
    for index in range(node_count):
        connection.execute(
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
                  near5_weight,
                  near5_occurrence_count,
                  stable_for_old_questions,
                  method_card_ids,
                  evidence,
                  status,
                  created_at,
                  updated_at,
                  near5_window_start_year,
                  near5_window_end_year
                ) values (
                  cast(:release_id as uuid),
                  :node_id,
                  'guokao',
                  'xingce',
                  '资料分析',
                  'knowledge_point',
                  'knowledge_point',
                  :tag_value,
                  :display_path,
                  null,
                  '[]'::jsonb,
                  'text_supported',
                  1,
                  1,
                  false,
                  '[]'::jsonb,
                  cast(:evidence as jsonb),
                  'published',
                  :created_at,
                  :updated_at,
                  2020,
                  2024
                )
                """
            ),
            {
                "release_id": migration._LEGACY_RELEASE_ID,
                "node_id": f"legacy-node-{index}",
                "tag_value": f"legacy-tag-{index}",
                "display_path": f"资料分析/legacy-{index}",
                "evidence": '{"fixture":%d}' % index,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
    stats = migration._legacy_node_stats(connection, migration._LEGACY_RELEASE_ID)
    legacy_release = {
        **production.legacy_release,
        "node_count": node_count,
        "source_fingerprint": stats["fingerprint"],
    }
    evidence = migration._RepairEvidence(
        legacy_release=legacy_release,
        expected_legacy_node_count=node_count,
        expected_legacy_node_fingerprint=stats["fingerprint"],
        successor_release=production.successor_release,
        active_release=production.active_release,
    )
    successor = {**evidence.successor_release, **(successor_overrides or {})}
    _insert_release(connection, successor)
    _insert_release(connection, _v4_release())
    _insert_release(connection, dict(evidence.active_release))
    connection.exec_driver_sql("set local session_replication_role = origin")
    return evidence


def _node_snapshot(connection: Connection) -> tuple[int, str]:
    stats = migration._legacy_node_stats(connection, migration._LEGACY_RELEASE_ID)
    return stats["node_count"], stats["fingerprint"]


def test_exact_historical_gap_is_repaired_without_touching_nodes_or_active_release(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as connection:
        evidence = _seed_exact_gap(connection)
        node_before = _node_snapshot(connection)
        active_before = migration._release_row(connection, migration._ACTIVE_RELEASE_ID)

        assert migration._repair_legacy_release_manifest(connection, evidence) == "repaired"

        assert _node_snapshot(connection) == node_before
        assert migration._release_row(connection, migration._ACTIVE_RELEASE_ID) == active_before
        assert migration._release_row(connection, migration._LEGACY_RELEASE_ID) == dict(
            evidence.legacy_release
        )
        successor = migration._release_row(connection, migration._SUCCESSOR_RELEASE_ID)
        assert successor is not None
        assert successor["supersedes_release_id"] == migration._LEGACY_RELEASE_ID


def test_mismatched_legacy_nodes_fail_closed_without_partial_write(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as connection:
        evidence = _seed_exact_gap(connection)
        connection.exec_driver_sql("set local session_replication_role = replica")
        connection.execute(
            text(
                "update product.knowledge_map_nodes "
                "set evidence = jsonb_build_object('changed', true) "
                "where release_id = cast(:release_id as uuid) and node_id = 'legacy-node-0'"
            ),
            {"release_id": migration._LEGACY_RELEASE_ID},
        )
        connection.exec_driver_sql("set local session_replication_role = origin")

        with pytest.raises(RuntimeError, match="node evidence changed"):
            migration._repair_legacy_release_manifest(connection, evidence)

        assert migration._release_row(connection, migration._LEGACY_RELEASE_ID) is None
        successor = migration._release_row(connection, migration._SUCCESSOR_RELEASE_ID)
        assert successor is not None
        assert successor["supersedes_release_id"] is None


def test_healthy_manifest_replay_is_idempotent(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        evidence = _seed_exact_gap(connection)
        assert migration._repair_legacy_release_manifest(connection, evidence) == "repaired"
        manifest_before = migration._release_row(connection, migration._LEGACY_RELEASE_ID)
        successor_before = migration._release_row(connection, migration._SUCCESSOR_RELEASE_ID)

        assert migration._repair_legacy_release_manifest(connection, evidence) == "unchanged"

        assert migration._release_row(connection, migration._LEGACY_RELEASE_ID) == manifest_before
        assert (
            migration._release_row(connection, migration._SUCCESSOR_RELEASE_ID) == successor_before
        )


def test_conflicting_parent_or_successor_identity_is_rejected(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as connection:
        evidence = _seed_exact_gap(
            connection,
            successor_overrides={"source_fingerprint": "0" * 64},
        )

        with pytest.raises(RuntimeError, match="successor identity changed"):
            migration._repair_legacy_release_manifest(connection, evidence)

        assert migration._release_row(connection, migration._LEGACY_RELEASE_ID) is None


def test_repair_takes_transaction_scoped_table_locks_before_validation(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as setup_connection:
        _seed_exact_gap(setup_connection)

    first = db_engine.connect()
    second = db_engine.connect()
    first_tx = first.begin()
    second_tx = second.begin()
    try:
        migration._lock_repair_tables(first)
        second.exec_driver_sql("set local lock_timeout = '100ms'")
        with pytest.raises(DBAPIError, match="lock timeout"):
            second.execute(
                text(
                    """
                    insert into product.knowledge_map_releases (
                      release_id, release_version, exam_scope, track,
                      source_data_version, vocab_version, generator_version,
                      source_fingerprint, generated_at, fresh_until, status,
                      node_count, activated_at, retired_at, supersedes_release_id,
                      question_binding_count, question_binding_fingerprint
                    ) values (
                      gen_random_uuid(), 'race-probe', 'guokao', 'xingce',
                      'probe', 'probe', 'probe', :fingerprint,
                      now(), now() + interval '1 day', 'draft', 0,
                      null, null, null, 0, null
                    )
                    """
                ),
                {"fingerprint": "1" * 64},
            )
    finally:
        second_tx.rollback()
        first_tx.rollback()
        second.close()
        first.close()


def test_release_scope_foreign_key_is_recreated_and_revalidated(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as connection:
        evidence = _seed_exact_gap(connection)
        migration._repair_legacy_release_manifest(connection, evidence)
        constraint = (
            connection.execute(
                text(
                    """
                select convalidated,
                       count(trigger.oid)::integer as trigger_count,
                       bool_and(trigger.tgenabled = 'O') as all_enabled
                from pg_constraint constraint_def
                join pg_trigger trigger on trigger.tgconstraint = constraint_def.oid
                where constraint_def.conrelid =
                      'product.knowledge_map_nodes'::regclass
                  and constraint_def.conname =
                      'product_knowledge_map_nodes_release_scope_fkey'
                group by convalidated
                """
                )
            )
            .mappings()
            .one()
        )
        assert constraint == {
            "convalidated": True,
            "trigger_count": 4,
            "all_enabled": True,
        }

        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    """
                    insert into product.knowledge_map_nodes (
                      release_id, node_id, exam_scope, track, module_name,
                      node_type, tag_type, tag_value, display_path,
                      support_status, stable_for_old_questions, method_card_ids,
                      evidence, status
                    ) values (
                      gen_random_uuid(), 'bad-orphan', 'guokao', 'xingce',
                      '资料分析', 'knowledge_point', 'knowledge_point',
                      'bad-orphan', '资料分析/bad-orphan', 'text_supported',
                      false, '[]'::jsonb, '{}'::jsonb, 'published'
                    )
                    """
                )
            )
        savepoint.rollback()


def test_revision_113_is_forward_only() -> None:
    assert migration.revision == "20260815_113"
    assert migration.down_revision == "20260814_112"
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()
