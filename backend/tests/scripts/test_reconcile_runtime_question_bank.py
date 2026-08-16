from __future__ import annotations

import json

import pytest

from scripts.reconcile_runtime_question_bank import (
    EXAM_SCOPES,
    RUNTIME_READONLY_SQL,
    RUNTIME_RELEASE_SQL,
    Entity,
    ReconciliationError,
    _insert_entity_batch,
    _open_catalog_database,
    credential_free_database_identity,
    database_fingerprint,
    database_url_from_env_text,
    _qbank_assets,
    reconcile_catalogs,
)


def _catalog(*entities: dict[str, object], runtime: bool = False) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 1,
        "examScopes": list(EXAM_SCOPES),
        "entities": list(entities),
    }
    if runtime:
        value.update(
            {
                "runtimeDatabaseFingerprint": "sha256:runtime",
                "activeReleaseRefs": ["guokao:xingce:release-1"],
            }
        )
    else:
        value["manifestRefs"] = ["source@sha256:local"]
    return value


def _entity(
    scope: str,
    kind: str,
    identity: str,
    *,
    version: str | None = None,
    status: str | None = None,
) -> dict[str, object]:
    return {
        "examScope": scope,
        "kind": kind,
        "identity": identity,
        "version": version,
        "status": status,
        "datasetId": identity.split(":", 1)[0],
    }


def test_credentials_are_consumed_but_never_serialized_or_fingerprinted() -> None:
    raw = (
        "OTHER=value\n"
        "DATABASE_URL=postgresql://runtime_user:super-secret@db.internal:5433/runtime_db\n"
    )

    identity = credential_free_database_identity(database_url_from_env_text(raw))
    rendered = json.dumps(identity, sort_keys=True)
    fingerprint = database_fingerprint(identity)

    assert identity == {
        "scheme": "postgresql",
        "host": "db.internal",
        "port": 5433,
        "database": "runtime_db",
    }
    assert "runtime_user" not in rendered
    assert "super-secret" not in rendered
    assert "super-secret" not in fingerprint


def test_reconciliation_covers_all_entities_scopes_and_difference_classes() -> None:
    local = _catalog(
        _entity("guokao", "question", "q:a", version="v1", status="active"),
        _entity("guokao", "occurrence", "o:a"),
        _entity("shengkao", "content_head", "h:a"),
        _entity("sydw", "asset", "a:a"),
        _entity("sydw", "question", "q:missing", status="active"),
    )
    runtime = _catalog(
        _entity("guokao", "question", "q:a", version="v2", status="active"),
        _entity("guokao", "occurrence", "o:a"),
        _entity("shengkao", "content_head", "h:a"),
        _entity("sydw", "asset", "a:a"),
        _entity("sydw", "question", "q:extra", status="quarantined"),
        _entity("shengkao", "question", "q:withdrawn", status="withdrawn"),
        runtime=True,
    )

    report = reconcile_catalogs(local, runtime)

    assert report["contract"] == {
        "reconciliationFingerprint": report["contract"]["reconciliationFingerprint"],
        "missingCount": 1,
        "extraCount": 2,
        "mismatchCount": 1,
        "quarantinedCount": 1,
        "withdrawnCount": 1,
        "mutationCount": 0,
    }
    assert report["breakdown"]["missing"] == {"sydw": {"question": 1}}
    assert report["breakdown"]["mismatched"] == {"guokao": {"question": 1}}


def test_replay_is_deterministic_and_does_not_mutate_inputs() -> None:
    local = _catalog(_entity("guokao", "question", "q:a", status="active"))
    runtime = _catalog(
        _entity("guokao", "question", "q:a", status="active"), runtime=True
    )
    before_local = json.dumps(local, sort_keys=True)
    before_runtime = json.dumps(runtime, sort_keys=True)

    first = reconcile_catalogs(local, runtime)
    second = reconcile_catalogs(local, runtime)

    assert first == second
    assert json.dumps(local, sort_keys=True) == before_local
    assert json.dumps(runtime, sort_keys=True) == before_runtime
    assert first["contract"]["mutationCount"] == 0


def test_conflicting_or_incomplete_manifest_fails_closed() -> None:
    local = _catalog(
        _entity("guokao", "question", "q:a", version="v1"),
        _entity("guokao", "question", "q:a", version="v2"),
    )
    runtime = _catalog(runtime=True)

    with pytest.raises(ReconciliationError, match="conflicting duplicate"):
        reconcile_catalogs(local, runtime)

    incomplete = _catalog()
    incomplete["examScopes"] = ["guokao", "sydw"]
    with pytest.raises(ReconciliationError, match="all three scopes"):
        reconcile_catalogs(incomplete, {**runtime, "entities": []})


def test_runtime_collector_is_transactionally_read_only() -> None:
    normalized = (RUNTIME_READONLY_SQL + RUNTIME_RELEASE_SQL).casefold()
    forbidden = ("insert into", "update ", "delete from", "create ", "alter ", "drop ")

    assert "begin transaction read only" in normalized
    assert normalized.rstrip().endswith("rollback;")
    assert all(statement not in normalized for statement in forbidden)


def test_runtime_collector_does_not_guess_an_unknown_exam_scope() -> None:
    normalized = RUNTIME_READONLY_SQL.casefold()

    assert "else null" in normalized
    assert "known_scopes" not in normalized
    assert "coalesce(known.exam_scope, 'shengkao')" not in normalized
    assert normalized.count("occurrence.exam_scope is not null") == 4
    assert "'unclassified'" in RUNTIME_RELEASE_SQL


def test_runtime_batch_insert_deduplicates_without_changing_identity(tmp_path) -> None:
    path = tmp_path / "catalog.sqlite"
    connection = _open_catalog_database(path, replace=True)
    try:
        entity = Entity("guokao", "question", "dataset:q1", status="active")
        _insert_entity_batch(connection, [entity, entity])
        rows = connection.execute(
            "select exam_scope,kind,identity,status from entities"
        ).fetchall()
    finally:
        connection.close()

    assert rows == [("guokao", "question", "dataset:q1", "active")]


def test_escaped_dump_html_asset_sources_are_normalized() -> None:
    raw = (
        r'{"fenbiFinalDisplay":{"stemHtml":"<img src=\\"省考/题_images/a.png\\"/>"}}'
    )

    assert _qbank_assets(raw) == {
        "data/question-bank-export/files/source-question-bank/"
        "fenbi-final-analysis-v1-20260613-assets/省考/题_images/a.png"
    }
