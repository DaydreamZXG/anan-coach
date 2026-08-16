from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
import json

import pytest
from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.domains.teaching_packages.publication import (
    PublicationReasonCode,
    RuntimeAsset,
    RuntimeQuestion,
    StructuredVisualPublicationRepository,
    build_publication_plan,
    project_candidate,
)
from app.domains.teaching_packages.publication.repository import (
    PublicationConflictError,
    publication_plan_sha256,
)


SOURCE_SHA = "a" * 64
CONTENT_VERSION = "b" * 64


def candidate(
    *,
    asset_id: str = "da_1234567890abcdef12345678",
    source_keys: tuple[str, ...] = ("pq_one",),
    chart_type: str = "combo",
) -> dict:
    return {
        "schema_version": "data-analysis-chart-candidate-v1",
        "asset_id": asset_id,
        "chart_type": chart_type,
        "title": "月度零售额及增幅",
        "unit": "亿元",
        "axes": {
            "x": {"title": "月", "unit": None, "min": 1, "max": 2, "ticks": [1, 2]},
            "y": [],
        },
        "categories": ["1", "2"],
        "series": [
            {
                "id": "series_1",
                "label": "零售额",
                "axis": "left",
                "unit": "亿元",
                "render_as": "bar",
                "values": [10, 1.2],
                "style": {},
            }
        ],
        "table": {
            "columns": ["零售额"],
            "rows": [
                {"label": "1", "values": [10]},
                {"label": "2", "values": [12]},
            ],
        },
        "pie_geometry": None,
        "structured_visual": None,
        "legend": [],
        "source": {
            "sha256": SOURCE_SHA,
            "local_path": "/private/source.png",
            "occurrences": [
                {
                    "question_id": source_key,
                    "paper_title": "测试卷",
                    "question_number": index + 1,
                    "source_jsonl_line": index + 1,
                    "html_field": "material_html",
                    "image_ref": "source.png",
                }
                for index, source_key in enumerate(source_keys)
            ],
        },
        "confidence": 0.95,
        "review_status": "candidate_auto",
        "verification": {
            "schema_valid": True,
            "ocr_engine": "fixture",
            "ocr_mean_confidence": 0.95,
            "local_crop_checks": {
                "total": 1,
                "matched": 1,
                "mismatched": 0,
                "reasons": [],
                "artifacts": [],
            },
            "missing_values_preserved": True,
            "notes": [],
        },
    }


def runtime_question(*, placeholder: bool = False) -> RuntimeQuestion:
    return RuntimeQuestion(
        question_id="q1",
        status="active",
        content_version_id=CONTENT_VERSION,
        placeholder=placeholder,
    )


def runtime_asset(*, sha: str | None = SOURCE_SHA, asset_id: str = "asset-1") -> RuntimeAsset:
    return RuntimeAsset(
        asset_id=asset_id,
        question_id="q1",
        object_key="questions/source.png",
        content_sha256=sha,
    )


def plan_for(
    source: dict | None = None,
    *,
    occurrence_map: dict[str, str] | None = None,
    question: RuntimeQuestion | None = None,
    assets: tuple[RuntimeAsset, ...] | None = None,
):
    return build_publication_plan(
        batch_id="batch-test-1",
        candidates=[source or candidate()],
        occurrence_question_ids=(
            occurrence_map if occurrence_map is not None else {"pq_one": "q1"}
        ),
        runtime_questions={"q1": question or runtime_question()},
        runtime_assets={"q1": assets or (runtime_asset(),)},
    )


def test_table_is_explicit_fact_authority_when_series_conflicts() -> None:
    source = candidate()
    before = deepcopy(source)

    projection = project_candidate(source)

    assert projection.authority == "table"
    values = [fact["value"] for fact in projection.payload["facts"]]
    assert "12" in values
    assert "1.2" not in values
    assert projection.payload["chartType"] == "combo"
    assert source == before
    serialized = json.dumps(projection.payload, ensure_ascii=False)
    for forbidden in ("answer", "explanation", "confidence", "local_path", "evidence"):
        assert forbidden not in serialized


def test_blank_table_corner_header_aligns_without_inventing_a_value() -> None:
    source = candidate()
    source["table"] = {
        "columns": ["", "企业1", "企业2"],
        "rows": [{"label": "延误", "values": [19.93, 11.12]}],
    }

    projection = project_candidate(source)

    cells = [
        fact for fact in projection.payload["facts"] if fact["kind"] == "table_cell"
    ]
    assert [fact["dimensions"] for fact in cells] == [
        ["延误", "企业1"],
        ["延误", "企业2"],
    ]


def test_structured_visual_projection_omits_evidence_and_complexity() -> None:
    source = candidate(chart_type="structured_visual")
    source["table"] = None
    source["series"] = []
    source["categories"] = []
    source["structured_visual"] = {
        "kind": "semanticMicrocharts",
        "schemaVersion": "semantic-microcharts-v1",
        "panels": [{"label": "A", "chart": {"points": [{"x": 0, "y": 1}]}}],
        "evidence": {"sourceSha256": SOURCE_SHA, "renderSha256": "c" * 64},
        "complexity": {"pointCount": 1},
    }

    projection = project_candidate(source)

    serialized = json.dumps(projection.payload, ensure_ascii=False)
    assert projection.authority == "structured_visual"
    assert "semanticMicrocharts" in serialized
    assert "sourceSha256" not in serialized
    assert "pointCount" not in serialized


def test_valid_binding_survives_stale_old_source_occurrence() -> None:
    source = candidate(source_keys=("pq_one", "pq_stale"))
    plan = plan_for(source)

    assert len(plan.publishable_items) == 1
    item = plan.publishable_items[0]
    assert item.quarantine_reasons == ()
    assert item.unresolved_occurrence_keys == ("pq_stale",)
    assert len(item.bindings) == 1


@pytest.mark.parametrize(
    ("occurrence_map", "question", "assets", "reason"),
    (
        ({}, runtime_question(), (runtime_asset(),), "SOURCE_OCCURRENCE_UNRESOLVED"),
        ({"pq_one": "q1"}, runtime_question(placeholder=True), (runtime_asset(),), "SOURCE_OCCURRENCE_UNRESOLVED"),
        ({"pq_one": "q1"}, runtime_question(), (runtime_asset(sha="c" * 64),), "SOURCE_ASSET_HASH_MISMATCH"),
    ),
)
def test_unresolved_placeholder_or_hash_mismatch_is_quarantined(
    occurrence_map, question, assets, reason
) -> None:
    plan = plan_for(
        occurrence_map=occurrence_map,
        question=question,
        assets=assets,
    )

    assert len(plan.quarantined_items) == 1
    assert reason in {item.value for item in plan.quarantined_items[0].quarantine_reasons}
    assert plan.binding_count == 0


def _seed_runtime(session: Session) -> None:
    session.execute(
        text(
            """
            insert into product.questions (question_id, status)
            values ('q1', 'active')
            """
        )
    )
    session.execute(
        text(
            """
            insert into product.question_content_versions (
              content_version_id, question_id, track, snapshot_payload,
              normalized_search_text, created_at
            ) values (
              :content_version_id, 'q1', 'xingce', '{}'::jsonb, '测试题', now()
            )
            """
        ),
        {"content_version_id": CONTENT_VERSION},
    )
    session.execute(
        text(
            """
            insert into product.question_content_heads (
              question_id, content_version_id, updated_at
            ) values ('q1', :content_version_id, now())
            """
        ),
        {"content_version_id": CONTENT_VERSION},
    )
    session.execute(
        text(
            """
            insert into product.question_assets (
              asset_id, question_id, part_kind, part_index, image_ordinal,
              image_src, object_key, is_representative_asset
            ) values (
              'asset-1', 'q1', 'material', 0, 0,
              'source.png', 'questions/source.png', true
            )
            """
        )
    )


@pytest.fixture()
def publication_engine(postgres_url, monkeypatch):
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    migration = import_module(
        "migrations.versions.20260813_107_structured_visual_evidence_publication"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("drop schema if exists product cascade")
        connection.exec_driver_sql("create schema product")
        connection.exec_driver_sql(
            "create table product.questions (question_id text primary key, status text not null)"
        )
        connection.exec_driver_sql(
            """
            create table product.question_content_versions (
              content_version_id text primary key,
              question_id text not null references product.questions(question_id),
              track text not null,
              snapshot_payload jsonb not null,
              normalized_search_text text not null,
              created_at timestamptz not null,
              unique (question_id, content_version_id)
            )
            """
        )
        connection.exec_driver_sql(
            """
            create table product.question_content_heads (
              question_id text primary key references product.questions(question_id),
              content_version_id text not null unique,
              updated_at timestamptz not null,
              foreign key (question_id, content_version_id) references
                product.question_content_versions(question_id, content_version_id)
            )
            """
        )
        connection.exec_driver_sql(
            """
            create table product.question_assets (
              asset_id text primary key,
              question_id text references product.questions(question_id),
              part_kind text, part_index integer, image_ordinal integer,
              image_src text, object_key text,
              is_representative_asset boolean default false
            )
            """
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        monkeypatch.setattr(migration.op, "execute", connection.exec_driver_sql)
        migration.upgrade()
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("drop schema if exists product cascade")
        engine.dispose()


def test_migration_publisher_replay_verify_and_exact_rollback(publication_engine) -> None:
    migration = import_module(
        "migrations.versions.20260813_107_structured_visual_evidence_publication"
    )
    assert migration.down_revision == "20260810_106"
    repository = StructuredVisualPublicationRepository()
    plan = plan_for()
    plan_sha = publication_plan_sha256(plan)
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)

    with Session(publication_engine) as session, session.begin():
        _seed_runtime(session)
        first = repository.apply(
            session, plan=plan, expected_plan_sha256=plan_sha, created_at=now
        )
    assert first.published_evidence == 1
    assert first.published_bindings == 1
    assert first.replayed is False

    with Session(publication_engine) as session, session.begin():
        replay = repository.apply(
            session, plan=plan, expected_plan_sha256=plan_sha, created_at=now
        )
        verified = repository.verify(session, plan=plan)
    assert replay.replayed is True
    assert verified.published_bindings == 1

    with Session(publication_engine) as session, session.begin():
        assert session.execute(
            text("select count(*) from product.structured_visual_evidence_versions")
        ).scalar_one() == 1
        assert session.execute(
            text("select count(*) from product.question_structured_visual_evidence_bindings")
        ).scalar_one() == 1
        withdrawn = repository.rollback(session, batch_id=plan.batch_id, withdrawn_at=now)
    assert withdrawn.state == "withdrawn"

    with Session(publication_engine) as session:
        # Immutable audit version is retained, only this batch's live pointers go.
        assert session.execute(
            text("select count(*) from product.structured_visual_evidence_versions")
        ).scalar_one() == 1
        assert session.execute(
            text("select count(*) from product.question_structured_visual_evidence_bindings")
        ).scalar_one() == 0
        assert session.execute(
            text("select count(*) from product.structured_visual_evidence_heads")
        ).scalar_one() == 0


def test_migration_adopts_preprovisioned_additive_tables(publication_engine, monkeypatch) -> None:
    migration = import_module(
        "migrations.versions.20260813_107_structured_visual_evidence_publication"
    )
    with publication_engine.begin() as connection:
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        monkeypatch.setattr(migration.op, "execute", connection.exec_driver_sql)
        migration.upgrade()
        assert connection.exec_driver_sql(
            "select to_regclass('product.structured_visual_evidence_versions')"
        ).scalar_one() == "product.structured_visual_evidence_versions"


def test_publication_contract_has_stable_reasons_and_exact_batch_scope() -> None:
    assert tuple(reason.value for reason in PublicationReasonCode) == (
        "SOURCE_SCHEMA_INVALID",
        "SEMANTIC_CONFLICT",
        "SOURCE_OCCURRENCE_UNRESOLVED",
        "QUESTION_NOT_ACTIVE",
        "CONTENT_HEAD_MISSING",
        "SOURCE_ASSET_HASH_MISMATCH",
        "SOURCE_ASSET_AMBIGUOUS",
        "SOURCE_AUDIENCE_UNRESOLVED",
        "PUBLICATION_STALE",
    )
    plan = plan_for()
    assert plan.batch_id == "batch-test-1"
    assert publication_plan_sha256(plan) == publication_plan_sha256(plan_for())


def test_stale_content_head_rejected_before_any_publication_write(publication_engine) -> None:
    repository = StructuredVisualPublicationRepository()
    plan = plan_for()
    with Session(publication_engine) as session, session.begin():
        _seed_runtime(session)
        session.execute(
            text("delete from product.question_content_heads where question_id = 'q1'")
        )
        with pytest.raises(PublicationConflictError, match="PUBLICATION_STALE"):
            repository.apply(session, plan=plan)
    with Session(publication_engine) as session:
        assert session.execute(
            text("select count(*) from product.structured_visual_evidence_versions")
        ).scalar_one() == 0
