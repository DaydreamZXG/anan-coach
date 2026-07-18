from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.content_catalog.licensing.publication import (
    CatalogLicensePublicationService,
)
from app.domains.external_ingestion.matching.authorization import SQLMatchAuthorization
from app.domains.external_ingestion.matching.models import IndexedContent


MANIFEST = (
    Path(__file__).resolve().parents[5]
    / "ops"
    / "content-licenses"
    / "owner-question-bank-v1.json"
)


def test_owner_catalog_publication_is_idempotent_authorized_and_training_denied(
    db_session: Session,
) -> None:
    content_version_id = "a" * 64
    db_session.execute(
        text(
            "insert into product.questions (question_id, domain, status, stem) "
            "values ('q-license-1', 'xingce', 'active', '题干'), "
            "('q-license-essay', 'essay', 'active', '申论题干')"
        )
    )
    db_session.execute(
        text(
            """
            insert into product.question_occurrences (
              occurrence_id, question_id, source_dataset_id
            ) values (
              'occ-license-1', 'q-license-1',
              'fenbi-final-analysis-dedup-v2-20260613'
            )
            """
        )
    )
    db_session.execute(
        text(
            """
            insert into product.knowledge_chunks (
              knowledge_chunk_id, question_id, source_question_id,
              source_dataset_id, content
            ) values (
              'chunk-license-essay', 'q-license-essay', 'source-essay-1',
              'essay-gkzhenti-raw-20260621', '申论材料'
            )
            """
        )
    )
    db_session.execute(
        text(
            """
            insert into product.question_content_versions (
              content_version_id, question_id, track, snapshot_payload,
              normalized_search_text, created_at
            ) values
              (:version, 'q-license-1', 'xingce', '{}'::jsonb, '题干', now()),
              (:essay_version, 'q-license-essay', 'essay', '{}'::jsonb, '申论题干', now())
            """
        ),
        {"version": content_version_id, "essay_version": "b" * 64},
    )
    db_session.execute(
        text(
            "insert into product.question_content_heads "
            "(question_id, content_version_id, updated_at) "
            "values ('q-license-1', :version, now()), "
            "('q-license-essay', :essay_version, now())"
        ),
        {"version": content_version_id, "essay_version": "b" * 64},
    )
    service = CatalogLicensePublicationService(session=db_session)

    dry_run = service.publish(manifest_path=MANIFEST, apply=False)
    applied = service.publish(manifest_path=MANIFEST, apply=True, batch_size=1)
    replay = service.publish(manifest_path=MANIFEST, apply=True, batch_size=1)

    assert dry_run.eligible_content_versions == 2
    assert applied.published == 2
    assert replay.already_active == 2
    assert replay.published == 0
    record = db_session.execute(
        text(
            "select record_payload from product.content_license_record_versions "
            "where content_version_id = :version"
        ),
        {"version": content_version_id},
    ).scalar_one()
    assert record["state"] == "active"
    assert record["reviewerReceipt"]["reviewerId"] != record["createdByActorId"]
    matrix = record["rights"]["grantMatrix"]
    assert matrix["defaultDecision"] == "deny"
    assert matrix["modelTrainingDecision"] == "deny"
    assert all(cell["use"] != "model_training" for cell in matrix["allowedGrantCells"])

    content = IndexedContent(
        contentVersionId=content_version_id,
        track="xingce",
        promptText="题干",
        optionTexts=("A", "B"),
        materialTexts=("材料",),
        referenceAnswer="A",
        solutionExplanation="解析",
        distributionClass="licensed_transferable",
        crossUserMatchAllowed=True,
    )
    authorization = SQLMatchAuthorization(db_session)
    assert authorization.authorize(
        owner_user_id=uuid4(), content=content, purpose="cross_user_match"
    ).allowed
    assert authorization.authorize(
        owner_user_id=uuid4(), content=content, purpose="reference_answer"
    ).allowed
    assert authorization.authorize(
        owner_user_id=uuid4(), content=content, purpose="ai_grounding"
    ).allowed
