from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.external_ingestion.matching.catalog import (
    ContentPublicationError,
    ContentVersionIntegrityError,
    SQLQuestionContentCatalog,
)
from app.domains.external_ingestion.matching.models import MatchQuery


NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def _seed_question(session: Session, *, question_id: str = "q-catalog-1") -> None:
    session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type,
              stem, answer, material_text, visual_required
            ) values (
              :question_id, 'objective', 'active', '判断推理', 'single',
              '下列关于行政许可的说法正确的是', 'A', '阅读给定材料', true
            )
            """
        ),
        {"question_id": question_id},
    )
    session.execute(
        text(
            """
            insert into product.question_options (
              question_id, option_index, label, option_text
            ) values
              (:question_id, 1, 'A', '行政机关可以依法撤回'),
              (:question_id, 2, 'B', '行政许可永远不得撤回')
            """
        ),
        {"question_id": question_id},
    )
    session.execute(
        text(
            """
            insert into product.question_explanations (
              question_id, preferred_analysis, analysis_source,
              analysis_status, prefer_ai_analysis, analysis_needs_review
            ) values (
              :question_id, '依据信赖保护原则，符合法定条件时可以撤回。',
              'licensed_editorial', 'approved', false, false
            )
            """
        ),
        {"question_id": question_id},
    )
    session.execute(
        text(
            """
            insert into product.question_assets (
              asset_id, question_id, part_kind, part_index,
              image_ordinal, image_src, object_key, is_representative_asset
            ) values (
              :asset_id, :question_id, 'material', 1, 1,
              'https://assets.example.cn/material.png',
              'questions/material.png', true
            )
            """
        ),
        {"asset_id": f"asset-{question_id}", "question_id": question_id},
    )


def test_publish_current_creates_durable_version_and_reads_only_formal_visual_fingerprints(
    db_session: Session,
) -> None:
    _seed_question(db_session)
    catalog = SQLQuestionContentCatalog(db_session, clock=lambda: NOW)

    published = catalog.publish_current(question_id="q-catalog-1")

    assert len(published.content_version_id) == 64
    assert published.prompt_text == "下列关于行政许可的说法正确的是"
    assert published.visual_fingerprints == ()
    assert published.reference_answer == "A"
    assert published.solution_explanation is not None
    assert db_session.execute(
        text(
            "select content_version_id from product.question_content_heads "
            "where question_id = 'q-catalog-1'"
        )
    ).scalar_one() == published.content_version_id

    formal_fingerprint = "1" * 64
    db_session.execute(
        text(
            """
            insert into product.question_visual_fingerprints (
              visual_fingerprint_id, question_id, content_version_id,
              fingerprint_sha256, fingerprint_policy_version,
              source_asset_ref, created_at
            ) values (
              :id, 'q-catalog-1', :version, :fingerprint,
              'visual-perceptual-256@v1', 'asset:asset-q-catalog-1', :created_at
            )
            """
        ),
        {
            "id": str(uuid4()),
            "version": published.content_version_id,
            "fingerprint": formal_fingerprint,
            "created_at": NOW,
        },
    )
    reread = catalog.get(content_version_id=published.content_version_id)
    assert reread is not None
    assert reread.visual_fingerprints == (formal_fingerprint,)

    results = catalog.search(
        query=MatchQuery(
            track="xingce",
            promptText="行政许可依法撤回",
            visualFingerprints=(formal_fingerprint,),
        ),
        maximum_candidates=5,
    )
    assert [item.content_version_id for item in results] == [
        published.content_version_id
    ]


def test_republication_is_append_only_and_search_uses_only_atomic_current_head(
    db_session: Session,
) -> None:
    _seed_question(db_session)
    catalog = SQLQuestionContentCatalog(db_session, clock=lambda: NOW)
    first = catalog.publish_current(question_id="q-catalog-1")
    db_session.execute(
        text(
            "update product.questions set stem = '新版本：行政处罚程序的正确说法' "
            "where question_id = 'q-catalog-1'"
        )
    )
    second_catalog = SQLQuestionContentCatalog(
        db_session, clock=lambda: NOW + timedelta(seconds=1)
    )
    second = second_catalog.publish_current(question_id="q-catalog-1")

    assert first.content_version_id != second.content_version_id
    assert second_catalog.get(content_version_id=first.content_version_id) == first
    assert db_session.execute(
        text(
            "select count(*) from product.question_content_versions "
            "where question_id = 'q-catalog-1'"
        )
    ).scalar_one() == 2
    assert db_session.execute(
        text(
            "select content_version_id from product.question_content_heads "
            "where question_id = 'q-catalog-1'"
        )
    ).scalar_one() == second.content_version_id

    old_search = second_catalog.search(
        query=MatchQuery(track="xingce", promptText="下列关于行政许可的说法正确的是"),
        maximum_candidates=5,
    )
    assert all(item.content_version_id != first.content_version_id for item in old_search)
    new_search = second_catalog.search(
        query=MatchQuery(track="xingce", promptText="行政处罚程序正确说法"),
        maximum_candidates=5,
    )
    assert [item.content_version_id for item in new_search] == [second.content_version_id]


def test_unknown_domain_and_tampered_version_fail_closed(db_session: Session) -> None:
    _seed_question(db_session, question_id="q-unknown-domain")
    db_session.execute(
        text(
            "update product.questions set domain = 'unmapped' "
            "where question_id = 'q-unknown-domain'"
        )
    )
    catalog = SQLQuestionContentCatalog(db_session, clock=lambda: NOW)
    with pytest.raises(ContentPublicationError, match="no approved"):
        catalog.publish_current(question_id="q-unknown-domain")

    _seed_question(db_session, question_id="q-tampered")
    snapshot = {
        "question_id": "q-tampered",
        "stem": "合法题干",
        "options": [],
        "assets": [],
    }
    db_session.execute(
        text(
            """
            insert into product.question_content_versions (
              content_version_id, question_id, track, snapshot_payload,
              normalized_search_text, created_at
            ) values (
              :version, 'q-tampered', 'xingce', cast(:payload as jsonb),
              '合法题干', :created_at
            )
            """
        ),
        {
            "version": "f" * 64,
            "payload": json.dumps(snapshot, ensure_ascii=False),
            "created_at": NOW,
        },
    )
    with pytest.raises(ContentVersionIntegrityError, match="contentVersionId"):
        catalog.get(content_version_id="f" * 64)
