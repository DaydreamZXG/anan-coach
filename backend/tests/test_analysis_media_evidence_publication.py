from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.domains.teaching_packages.publication import (
    AnalysisMediaPublicationRepository,
    AnalysisPublicationReasonCode,
    AnalysisRuntimeQuestion,
    build_analysis_publication_plan,
    project_formula_candidate,
    project_analysis_source_image,
)


SOURCE_SHA = "a" * 64
CONTENT_VERSION = "b" * 64


def formula_candidate() -> dict:
    return {
        "asset_id": "qi_abcdef1234567890abcdef12",
        "schema_version": "formula-asset-v1",
        "sha256": SOURCE_SHA,
        "priority": "explanation",
        "status": "candidate_auto",
        "blocks": [
            {
                "type": "formula",
                "format": "latex",
                "expression": r"x^2+1",
                "display": True,
            }
        ],
        "verification": {
            "source_hash_matches": True,
            "braces_balanced": True,
            "latex_nonempty": True,
        },
    }


def occurrence(*, role: str = "analysis") -> dict:
    return {
        "question_id": "pq_source",
        "role": role,
        "field": "original_analysis_html",
        "image_ref": "paper_images/formula.png",
    }


def runtime_question(
    *, analysis_source: str = "original_analysis"
) -> AnalysisRuntimeQuestion:
    return AnalysisRuntimeQuestion(
        question_id="q1",
        status="active",
        content_version_id=CONTENT_VERSION,
        analysis_source=analysis_source,
        final_analysis_asset_refs={
            "source": analysis_source,
            "assets": [
                {
                    "imageSrc": "paper_images/formula.png",
                    "objectKey": "source/formula.png",
                    "contentSha256": SOURCE_SHA,
                }
            ],
        },
    )


def plan_for(*, analysis_source: str = "original_analysis"):
    source_occurrences = (occurrence(),)
    return build_analysis_publication_plan(
        batch_id="analysis-batch-v1",
        entries=(
            (
                formula_candidate(),
                source_occurrences,
                lambda item: project_formula_candidate(
                    item, source_occurrences=source_occurrences
                ),
            ),
        ),
        occurrence_question_ids={"pq_source": "q1"},
        runtime_questions={"q1": runtime_question(analysis_source=analysis_source)},
    )


def test_analysis_plan_is_verifier_only_and_uses_analysis_identity() -> None:
    plan = plan_for()

    assert len(plan.publishable_items) == 1
    item = plan.publishable_items[0]
    assert item.payload["audiences"] == ["verifier"]
    assert item.payload["sourceRoles"] == ["analysis"]
    assert item.analysis_bindings[0].analysis_source == "original_analysis"
    assert item.analysis_bindings[0].runtime_object_key == "source/formula.png"
    assert item.evidence_ref.startswith("analysis-media:")


def test_non_current_analysis_source_is_quarantined() -> None:
    plan = plan_for(analysis_source="ai_rewrite")

    assert not plan.publishable_items
    assert plan.quarantined_items[0].quarantine_reasons == (
        AnalysisPublicationReasonCode.ANALYSIS_SOURCE_MISMATCH,
    )


def test_question_roles_do_not_enter_analysis_publication() -> None:
    source_occurrences = (occurrence(role="stem"),)
    plan = build_analysis_publication_plan(
        batch_id="analysis-batch-v1",
        entries=(
            (
                formula_candidate(),
                source_occurrences,
                lambda item: project_formula_candidate(
                    item, source_occurrences=source_occurrences
                ),
            ),
        ),
        occurrence_question_ids={"pq_source": "q1"},
        runtime_questions={"q1": runtime_question()},
    )
    assert not plan.publishable_items
    assert plan.quarantined_items[0].quarantine_reasons == (
        AnalysisPublicationReasonCode.ANALYSIS_ASSET_UNRESOLVED,
    )


def test_unstructured_analysis_image_stays_source_only() -> None:
    projection = project_analysis_source_image(
        {
            "asset_id": "qi_abcdef1234567890abcdef12",
            "sha256": SOURCE_SHA,
            "kind": "image",
        }
    )

    assert projection.payload["evidenceKind"] == "source_image"
    assert projection.payload["facts"] == [
        {
            "factRef": "visual:qi_abcdef1234567890abcdef12:fact:0001",
            "kind": "source_only",
            "dimensions": ["image_sha256"],
            "value": SOURCE_SHA,
            "unit": "",
        }
    ]
    assert projection.authority == "source_image_identity_only"



def _create_schema(session: Session) -> None:
    statements = (
        "create schema product",
        "create table product.questions (question_id text primary key, status text not null)",
        "create table product.question_content_versions (question_id text, content_version_id text, snapshot_payload jsonb, primary key(question_id,content_version_id))",
        "create table product.question_content_heads (question_id text primary key, content_version_id text not null)",
        "create table product.question_explanations (question_id text primary key, analysis_source text, final_analysis_asset_refs jsonb)",
        "create table product.question_occurrences (occurrence_id text primary key, question_id text, source_occurrence_question_key text)",
        "create table product.structured_visual_evidence_versions (evidence_version_id text primary key, evidence_ref text not null, revision integer not null, source_asset_sha256 text not null, source_payload_sha256 text not null, verification_policy_version text not null, chart_type text not null, payload jsonb not null, created_at timestamptz not null, unique(evidence_ref,source_payload_sha256,verification_policy_version))",
        "create table product.structured_visual_evidence_heads (evidence_ref text primary key, evidence_version_id text not null, activated_by_batch_id text not null, updated_at timestamptz not null)",
        "create table product.question_analysis_visual_evidence_bindings (binding_id text primary key, evidence_ref text not null, evidence_version_id text not null, question_id text not null, content_version_id text not null, analysis_source text not null, source_occurrence_question_key text not null, source_image_ref text not null, runtime_object_key text, source_asset_sha256 text not null, batch_id text not null, bound_at timestamptz not null, unique(evidence_ref,evidence_version_id,question_id,content_version_id,source_occurrence_question_key,source_image_ref,source_asset_sha256))",
        "create table product.structured_visual_evidence_import_receipts (batch_id text primary key, plan_sha256 text not null, verification_policy_version text not null, state text not null, evidence_count integer not null, binding_count integer not null, quarantined_count integer not null, previous_heads jsonb not null, report jsonb not null, created_at timestamptz not null, withdrawn_at timestamptz)",
    )
    for statement in statements:
        session.execute(text(statement))


def _seed_runtime(session: Session) -> None:
    session.execute(text("insert into product.questions values ('q1','active')"))
    session.execute(
        text(
            "insert into product.question_content_versions values "
            "('q1', :content, '{\"analysis_source\":\"original_analysis\"}'::jsonb)"
        ),
        {"content": CONTENT_VERSION},
    )
    session.execute(
        text("insert into product.question_content_heads values ('q1', :content)"),
        {"content": CONTENT_VERSION},
    )
    session.execute(
        text(
            "insert into product.question_explanations values "
            "('q1','original_analysis',cast(:refs as jsonb))"
        ),
        {
            "refs": '{"source":"original_analysis","assets":['
            '{"imageSrc":"paper_images/formula.png",'
            '"objectKey":"source/formula.png",'
            f'"contentSha256":"{SOURCE_SHA}"}}]}}',
        },
    )
    session.execute(
        text(
            "insert into product.question_occurrences values "
            "('o1','q1','pq_source')"
        )
    )


def test_repository_replay_verify_and_exact_rollback(postgres_url) -> None:
    engine = create_engine(postgres_url, pool_pre_ping=True, future=True)
    repository = AnalysisMediaPublicationRepository()
    plan = plan_for()
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("drop schema if exists product cascade"))
            _create_schema(session)
            _seed_runtime(session)
        with Session(engine) as session, session.begin():
            first = repository.apply(
                session,
                plan=plan,
                created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
            )
        assert first.published_evidence == 1
        assert first.published_bindings == 1
        with Session(engine) as session, session.begin():
            replay = repository.apply(session, plan=plan)
            verified = repository.verify(session, plan=plan)
        assert replay.replayed is True
        assert verified.published_bindings == 1
        with Session(engine) as session, session.begin():
            withdrawn = repository.rollback(session, batch_id=plan.batch_id)
        assert withdrawn.state == "withdrawn"
        with Session(engine) as session:
            assert session.execute(
                text(
                    "select count(*) from "
                    "product.question_analysis_visual_evidence_bindings"
                )
            ).scalar_one() == 0
            assert session.execute(
                text("select count(*) from product.structured_visual_evidence_versions")
            ).scalar_one() == 1
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql("drop schema if exists product cascade")
        engine.dispose()
