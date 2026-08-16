"""Restore the backup-proven legacy knowledge release manifest.

Revision ID: 20260815_113
Revises: 20260814_112

This is a deliberately narrow historical repair.  Two independent 2026-07-29
database backups retain the exact retired release row and the first formal
release's original ``supersedes_release_id``.  The production 104 snapshot
still contains all 2,462 immutable nodes but lost those two manifest facts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260815_113"
down_revision: str | Sequence[str] | None = "20260814_112"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class _RepairEvidence:
    def __init__(
        self,
        *,
        legacy_release: Mapping[str, Any],
        expected_legacy_node_count: int,
        expected_legacy_node_fingerprint: str,
        successor_release: Mapping[str, Any],
        active_release: Mapping[str, Any],
    ) -> None:
        self.legacy_release = legacy_release
        self.expected_legacy_node_count = expected_legacy_node_count
        self.expected_legacy_node_fingerprint = expected_legacy_node_fingerprint
        self.successor_release = successor_release
        self.active_release = active_release


_LEGACY_RELEASE_ID = "86064dad-c59f-1939-e8d6-1076e75ef9a3"
_SUCCESSOR_RELEASE_ID = "f4e8d3e7-fc73-4886-8d8d-8fa256b05b0b"
_ACTIVE_RELEASE_ID = "198bae5c-6008-44dc-97a2-b2f97284368e"

_PRODUCTION_EVIDENCE = _RepairEvidence(
    legacy_release={
        "release_id": _LEGACY_RELEASE_ID,
        "release_version": "knowledge-map@legacy-pre-20260727-65:guokao:xingce",
        "exam_scope": "guokao",
        "track": "xingce",
        "source_data_version": "legacy:source-version-unknown",
        "vocab_version": "legacy:vocab-version-unknown",
        "generator_version": "legacy:generator-version-unknown",
        "source_fingerprint": ("6012a30ea3b90ce00b11b0e13d758bbf6d5cfdad1515ba1d497a654cbcd94a0e"),
        "generated_at": datetime.fromisoformat("2026-07-03T21:22:15.517278+08:00"),
        "fresh_until": datetime.fromisoformat("2026-07-03T21:22:15.517278+08:00"),
        "status": "retired",
        "node_count": 2462,
        "activated_at": None,
        "retired_at": datetime.fromisoformat("2026-07-27T18:40:16.983459+08:00"),
        "supersedes_release_id": None,
        "question_binding_count": 0,
        "question_binding_fingerprint": None,
    },
    expected_legacy_node_count=2462,
    expected_legacy_node_fingerprint=(
        "6012a30ea3b90ce00b11b0e13d758bbf6d5cfdad1515ba1d497a654cbcd94a0e"
    ),
    successor_release={
        "release_id": _SUCCESSOR_RELEASE_ID,
        "release_version": "knowledge-map@20260727T103847Z-core2",
        "exam_scope": "guokao",
        "track": "xingce",
        "source_data_version": "product-qbank@20260727",
        "vocab_version": "vocab@v1",
        "generator_version": "refresh_knowledge_map_nodes_v3",
        "source_fingerprint": ("cc43dcd73fec95154b47b56c2dd83ab22c8e7b67c158d8c907a6cb6c11924114"),
        "generated_at": datetime.fromisoformat("2026-07-27T18:40:22.375686+08:00"),
        "fresh_until": datetime.fromisoformat("2027-01-27T08:00:00+08:00"),
        "status": "retired",
        "node_count": 563,
        "activated_at": datetime.fromisoformat("2026-07-27T18:40:22.375686+08:00"),
        "retired_at": datetime.fromisoformat("2026-08-05T22:48:13.592579+08:00"),
        "supersedes_release_id": None,
        "question_binding_count": 99409,
        "question_binding_fingerprint": (
            "ac68061e81839771170cbf69f1cbe92d20f04b1247c5e88fe5d2e8f7a9692409"
        ),
    },
    active_release={
        "release_id": _ACTIVE_RELEASE_ID,
        "release_version": "knowledge-map@20260805T000000Z-v5",
        "exam_scope": "guokao",
        "track": "xingce",
        "source_data_version": "product-qbank@20260727",
        "vocab_version": "vocab@v1",
        "generator_version": "refresh_knowledge_map_nodes_v4",
        "source_fingerprint": ("38f8c22cfe9c0891af918b15961f2e84bd82a385bcae250384697623c6270959"),
        "generated_at": datetime.fromisoformat("2026-08-05T23:35:26.386514+08:00"),
        "fresh_until": datetime.fromisoformat("2027-01-27T08:00:00+08:00"),
        "status": "active",
        "node_count": 640,
        "activated_at": datetime.fromisoformat("2026-08-05T23:35:26.386514+08:00"),
        "retired_at": None,
        "supersedes_release_id": "145d1371-f9dd-4bc5-9fe4-674921b4bb3a",
        "question_binding_count": 158366,
        "question_binding_fingerprint": (
            "81e354644e1a0bf5521420d9fc3e9972766ba5aa39eeaee0b14376995b64f85d"
        ),
    },
)

_RELEASE_COLUMNS = tuple(_PRODUCTION_EVIDENCE.legacy_release)
_RELEASE_SELECT_COLUMNS = tuple(
    (f"{column}::text as {column}" if column in {"release_id", "supersedes_release_id"} else column)
    for column in _RELEASE_COLUMNS
)


def _lock_repair_tables(bind: Connection) -> None:
    bind.exec_driver_sql("lock table product.knowledge_map_releases in share row exclusive mode")
    bind.exec_driver_sql("lock table product.knowledge_map_nodes in share row exclusive mode")


def _release_row(bind: Connection, release_id: str) -> dict[str, Any] | None:
    row = (
        bind.execute(
            text(
                "select "
                + ", ".join(_RELEASE_SELECT_COLUMNS)
                + " from product.knowledge_map_releases "
                "where release_id = cast(:release_id as uuid)"
            ),
            {"release_id": release_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _legacy_node_stats(bind: Connection, release_id: str) -> dict[str, Any]:
    row = (
        bind.execute(
            text(
                """
            select
              count(*)::integer as node_count,
              count(*) filter (
                where exam_scope is distinct from 'guokao'
                   or track is distinct from 'xingce'
                   or status is distinct from 'published'
              )::integer as invalid_scope_count,
              min(created_at) as min_created_at,
              max(created_at) as max_created_at,
              min(updated_at) as min_updated_at,
              max(updated_at) as max_updated_at,
              encode(
                digest(
                  convert_to(
                    coalesce(
                      string_agg(
                        concat_ws(
                          E'\\x1f',
                          node_id,
                          exam_scope,
                          track,
                          module_name,
                          node_type,
                          coalesce(tag_type, ''),
                          tag_value,
                          display_path,
                          coalesce(parent_node_id, ''),
                          status,
                          evidence::text
                        ),
                        E'\\x1e'
                        order by node_id
                      ),
                      ''
                    ),
                    'UTF8'
                  ),
                  'sha256'
                ),
                'hex'
              ) as fingerprint
            from product.knowledge_map_nodes
            where release_id = cast(:release_id as uuid)
            """
            ),
            {"release_id": release_id},
        )
        .mappings()
        .one()
    )
    return dict(row)


def _assert_exact_release(
    actual: Mapping[str, Any] | None,
    expected: Mapping[str, Any],
    *,
    message: str,
) -> None:
    if actual is None or any(actual.get(key) != value for key, value in expected.items()):
        raise RuntimeError(message)


def _assert_exact_legacy_nodes(
    stats: Mapping[str, Any],
    evidence: _RepairEvidence,
) -> None:
    expected_timestamp = evidence.legacy_release["generated_at"]
    if (
        stats["node_count"] != evidence.expected_legacy_node_count
        or stats["invalid_scope_count"] != 0
        or stats["fingerprint"] != evidence.expected_legacy_node_fingerprint
        or stats["min_created_at"] != expected_timestamp
        or stats["max_created_at"] != expected_timestamp
        or stats["min_updated_at"] != expected_timestamp
        or stats["max_updated_at"] != expected_timestamp
    ):
        raise RuntimeError("legacy knowledge-map node evidence changed; refusing manifest repair")


def _insert_legacy_release(
    bind: Connection,
    evidence: _RepairEvidence,
) -> None:
    params = dict(evidence.legacy_release)
    params["release_id"] = str(params["release_id"])
    bind.exec_driver_sql(
        "alter table product.knowledge_map_releases "
        "disable trigger knowledge_map_releases_lifecycle_guard"
    )
    inserted = bind.execute(
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
        params,
    )
    if inserted.rowcount != 1:
        raise RuntimeError("legacy knowledge-map release insert did not affect one row")
    updated = bind.execute(
        text(
            """
            update product.knowledge_map_releases
            set supersedes_release_id = cast(:legacy_release_id as uuid)
            where release_id = cast(:successor_release_id as uuid)
              and supersedes_release_id is null
            """
        ),
        {
            "legacy_release_id": evidence.legacy_release["release_id"],
            "successor_release_id": evidence.successor_release["release_id"],
        },
    )
    if updated.rowcount != 1:
        raise RuntimeError("knowledge-map successor link repair did not affect one row")
    bind.exec_driver_sql(
        "alter table product.knowledge_map_releases "
        "enable trigger knowledge_map_releases_lifecycle_guard"
    )


def _rebuild_release_scope_foreign_key(bind: Connection) -> None:
    bind.exec_driver_sql(
        "alter table product.knowledge_map_nodes "
        "drop constraint if exists product_knowledge_map_nodes_release_scope_fkey"
    )
    bind.exec_driver_sql(
        "alter table product.knowledge_map_nodes "
        "add constraint product_knowledge_map_nodes_release_scope_fkey "
        "foreign key (release_id, exam_scope, track) "
        "references product.knowledge_map_releases(release_id, exam_scope, track) "
        "not valid"
    )
    bind.exec_driver_sql(
        "alter table product.knowledge_map_nodes "
        "validate constraint product_knowledge_map_nodes_release_scope_fkey"
    )


def _assert_postconditions(
    bind: Connection,
    *,
    before_node_stats: Mapping[str, Any],
    before_active_release: Mapping[str, Any] | None,
    evidence: _RepairEvidence,
) -> None:
    after_node_stats = _legacy_node_stats(
        bind,
        str(evidence.legacy_release["release_id"]),
    )
    if dict(after_node_stats) != dict(before_node_stats):
        raise RuntimeError("legacy knowledge-map nodes changed during manifest repair")
    after_active_release = _release_row(
        bind,
        str(evidence.active_release["release_id"]),
    )
    if after_active_release != before_active_release:
        raise RuntimeError("active knowledge-map release changed during manifest repair")
    orphan_count = bind.execute(
        text(
            """
            select count(*)::integer
            from product.knowledge_map_nodes node
            left join product.knowledge_map_releases release
              on release.release_id = node.release_id
             and release.exam_scope = node.exam_scope
             and release.track = node.track
            where release.release_id is null
            """
        )
    ).scalar_one()
    if orphan_count != 0:
        raise RuntimeError("knowledge-map release-scope orphans remain after repair")
    constraint = (
        bind.execute(
            text(
                """
            select convalidated,
                   count(*) filter (where trigger.tgenabled <> 'O')::integer
                     as disabled_trigger_count,
                   count(trigger.oid)::integer as trigger_count
            from pg_constraint constraint_def
            left join pg_trigger trigger
              on trigger.tgconstraint = constraint_def.oid
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
    if (
        constraint["convalidated"] is not True
        or constraint["disabled_trigger_count"] != 0
        or constraint["trigger_count"] < 2
    ):
        raise RuntimeError("knowledge-map release-scope foreign key is not enforced")


def _repair_legacy_release_manifest(
    bind: Connection,
    evidence: _RepairEvidence = _PRODUCTION_EVIDENCE,
) -> str:
    _lock_repair_tables(bind)
    legacy_release_id = str(evidence.legacy_release["release_id"])
    successor_release_id = str(evidence.successor_release["release_id"])
    active_release_id = str(evidence.active_release["release_id"])
    before_node_stats = _legacy_node_stats(bind, legacy_release_id)
    legacy_release = _release_row(bind, legacy_release_id)
    successor_release = _release_row(bind, successor_release_id)
    before_active_release = _release_row(bind, active_release_id)
    state = "unchanged"

    if legacy_release is None and before_node_stats["node_count"] == 0:
        if successor_release is not None:
            raise RuntimeError("knowledge-map successor exists without legacy nodes or manifest")
    elif legacy_release is None:
        _assert_exact_legacy_nodes(before_node_stats, evidence)
        _assert_exact_release(
            successor_release,
            evidence.successor_release,
            message="knowledge-map successor identity changed; refusing manifest repair",
        )
        _assert_exact_release(
            before_active_release,
            evidence.active_release,
            message="active knowledge-map release changed; refusing manifest repair",
        )
        _insert_legacy_release(bind, evidence)
        state = "repaired"
    elif successor_release is not None:
        expected_successor = dict(evidence.successor_release)
        expected_successor["supersedes_release_id"] = legacy_release_id
        _assert_exact_legacy_nodes(before_node_stats, evidence)
        _assert_exact_release(
            legacy_release,
            evidence.legacy_release,
            message="legacy knowledge-map manifest conflicts with backup evidence",
        )
        _assert_exact_release(
            successor_release,
            expected_successor,
            message="knowledge-map successor link conflicts with backup evidence",
        )
        _assert_exact_release(
            before_active_release,
            evidence.active_release,
            message="active knowledge-map release changed; refusing manifest replay",
        )

    _rebuild_release_scope_foreign_key(bind)
    _assert_postconditions(
        bind,
        before_node_stats=before_node_stats,
        before_active_release=before_active_release,
        evidence=evidence,
    )
    return state


def upgrade() -> None:
    _repair_legacy_release_manifest(op.get_bind())


def downgrade() -> None:
    raise RuntimeError("restored historical knowledge-release manifests are forward-only")
