from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.essay_intelligence.catalog_publisher import (
    GuokaoEssayCatalogPublisher,
    _rubric_status,
)
from app.domains.essay_intelligence.rubric_ai_pipeline import (
    GuokaoRubricAIPipeline,
)
from app.domains.essay_intelligence.rubric_compiler import (
    RubricCompilationSource,
    SystemRubricCompilationDraft,
    rubric_input_fingerprint,
)
from app.domains.essay_intelligence.rubric_publisher import (
    EssayRubricPublishError,
    GuokaoEssayRubricPublisher,
    load_rubric_compilation_source,
)


NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


def test_publisher_appends_only_system_compiled_unverified_rubrics(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_catalog_source(db_session_factory, question_id="rubric-publish")
    source = _published_source(db_session_factory)
    first_draft = _draft(source)
    publisher = GuokaoEssayRubricPublisher()

    with db_session_factory.begin() as session:
        first = publisher.publish(session, drafts=[first_draft], now=NOW)
    with db_session_factory.begin() as session:
        replay = publisher.publish(session, drafts=[first_draft], now=NOW)

    changed_payload = first_draft.model_dump(by_alias=True, mode="python")
    changed_payload["criteria"][0]["scoringRule"] = (
        "按已锚定要点计分，不得重复计分。"
    )
    changed_draft = SystemRubricCompilationDraft.model_validate(changed_payload)
    with db_session_factory.begin() as session:
        changed = publisher.publish(session, drafts=[changed_draft], now=NOW)
    with db_session_factory.begin() as session:
        catalog_after_rubric = GuokaoEssayCatalogPublisher().publish(session, now=NOW)

    assert first.inserted_count == 1
    assert first.versions[0].source_kind == "system_compiled"
    assert first.versions[0].verification_status == "unverified"
    assert replay.unchanged_count == 1
    assert replay.versions[0].rubric_version_id == first.versions[0].rubric_version_id
    assert changed.inserted_count == 1
    assert changed.versions[0].version_no == 2
    assert catalog_after_rubric.rubric_pending_count == 0
    assert (
        catalog_after_rubric.versions[0].rubric_status
        == "published_system_compiled_unverified"
    )
    with db_session_factory() as session:
        rows = session.execute(
            text(
                """
                select rubric_version_id, version_no, source_kind,
                       verification_status, state, criteria, points,
                       supersedes_rubric_version_id
                from product.essay_rubric_versions
                order by version_no
                """
            )
        ).mappings().all()
        assert len(rows) == 2
        assert all(item["source_kind"] == "system_compiled" for item in rows)
        assert all(item["verification_status"] == "unverified" for item in rows)
        assert all(item["state"] == "published" for item in rows)
        assert rows[1]["supersedes_rubric_version_id"] == rows[0]["rubric_version_id"]
        point = rows[1]["points"][0]
        assert point["criterionCode"] == "content"
        assert point["materialEvidenceRefs"][0]["quote"] == "材料"


def test_publisher_dry_run_and_cli_do_not_write(
    db_session_factory: sessionmaker[Session],
    postgres_url: str,
    tmp_path: Path,
) -> None:
    _seed_catalog_source(db_session_factory, question_id="rubric-cli")
    source = _published_source(db_session_factory)
    draft = _draft(source)
    manifest = tmp_path / "rubrics.json"
    manifest.write_text(
        json.dumps(
            {"drafts": [draft.model_dump(by_alias=True, mode="json")]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[2] / "scripts" / "publish_guokao_essay_rubrics.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--database-url",
            postgres_url,
            "--input",
            str(manifest),
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["insertedCount"] == 1
    assert payload["versions"][0]["action"] == "would_insert"
    assert payload["versions"][0]["verification_status"] == "unverified"
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from product.essay_rubric_versions")
        ).scalar_one() == 0


def test_system_publisher_never_supersedes_an_official_rubric(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_catalog_source(db_session_factory, question_id="rubric-official")
    source = _published_source(db_session_factory)
    draft = _draft(source)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key, version_no,
                  material_bundle_version_id,
                  source_kind, verification_status, state, total_maximum,
                  criteria, points, content_sha256
                ) values (
                  cast(:rubric_id as uuid), cast(:task_id as uuid), :task_key, 1,
                  cast(:material_id as uuid),
                  'official', 'verified', 'published', :maximum,
                  '[{}]'::jsonb, '[{}]'::jsonb, repeat('e', 64)
                )
                """
            ),
            {
                "rubric_id": str(uuid4()),
                "task_id": str(source.task_version_id),
                "task_key": source.task_key,
                "material_id": str(source.material_bundle_version_id),
                "maximum": source.maximum_score,
            },
        )

    with pytest.raises(EssayRubricPublishError, match="official rubric"):
        with db_session_factory.begin() as session:
            GuokaoEssayRubricPublisher().publish(
                session,
                drafts=[draft],
                now=NOW,
            )


def test_legacy_unbound_official_rubric_does_not_block_exact_material_lineage(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_catalog_source(db_session_factory, question_id="rubric-legacy-unbound")
    source = _published_source(db_session_factory)
    legacy_rubric_id = uuid4()
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key, version_no,
                  material_bundle_version_id,
                  source_kind, verification_status, state, total_maximum,
                  criteria, points, content_sha256
                ) values (
                  cast(:rubric_id as uuid), cast(:task_id as uuid), :task_key, 1,
                  null,
                  'official', 'verified', 'published', :maximum,
                  '[{}]'::jsonb, '[{}]'::jsonb, repeat('e', 64)
                )
                """
            ),
            {
                "rubric_id": str(legacy_rubric_id),
                "task_id": str(source.task_version_id),
                "task_key": source.task_key,
                "maximum": source.maximum_score,
            },
        )

    pipeline = GuokaoRubricAIPipeline(
        settings=Settings(
            ai_price_version="test-price-v1",
            ai_input_cny_per_million_tokens="2",
            ai_cached_input_cny_per_million_tokens="0.5",
            ai_output_cny_per_million_tokens="4",
        )
    )
    with db_session_factory() as session:
        assert pipeline.discover_pending_sources(session) == (source,)
    with db_session_factory.begin() as session:
        before = GuokaoEssayCatalogPublisher().publish(session, now=NOW)
    assert before.rubric_pending_count == 1
    assert before.versions[0].rubric_status == "pending_ai_compilation"

    with db_session_factory.begin() as session:
        published = GuokaoEssayRubricPublisher().publish(
            session,
            drafts=[_draft(source)],
            now=NOW,
        )

    assert published.inserted_count == 1
    assert published.versions[0].version_no == 2
    with db_session_factory.begin() as session:
        after = GuokaoEssayCatalogPublisher().publish(session, now=NOW)
    assert after.rubric_pending_count == 0
    assert (
        after.versions[0].rubric_status
        == "published_system_compiled_unverified"
    )
    with db_session_factory() as session:
        rows = session.execute(
            text(
                """
                select rubric_version_id, version_no, material_bundle_version_id,
                       source_kind, supersedes_rubric_version_id
                from product.essay_rubric_versions
                order by version_no
                """
            )
        ).mappings().all()
        assert [item["rubric_version_id"] for item in rows] == [
            legacy_rubric_id,
            UUID(published.versions[0].rubric_version_id),
        ]
        assert rows[0]["material_bundle_version_id"] is None
        assert rows[0]["source_kind"] == "official"
        assert rows[1]["material_bundle_version_id"] == source.material_bundle_version_id
        assert rows[1]["supersedes_rubric_version_id"] is None
        assert pipeline.discover_pending_sources(session) == ()


@pytest.mark.parametrize("foreign_lineage", ["other_material", "legacy_unbound"])
def test_foreign_material_successor_cannot_hide_the_exact_rubric_lineage(
    db_session_factory: sessionmaker[Session],
    foreign_lineage: str,
) -> None:
    _seed_catalog_source(
        db_session_factory,
        question_id=f"rubric-foreign-successor-{foreign_lineage}",
    )
    source = _published_source(db_session_factory)
    draft = _draft(source)
    publisher = GuokaoEssayRubricPublisher()
    with db_session_factory.begin() as session:
        original = publisher.publish(session, drafts=[draft], now=NOW)
    original_rubric_id = original.versions[0].rubric_version_id
    foreign_material_id = (
        str(uuid4()) if foreign_lineage == "other_material" else None
    )
    with db_session_factory.begin() as session:
        if foreign_material_id is not None:
            session.execute(
                text(
                    """
                    insert into product.essay_material_bundle_versions (
                      material_bundle_version_id, task_version_id, task_key,
                      version_no, completeness, source_kind, content_sha256
                    ) values (
                      cast(:material_id as uuid), cast(:task_id as uuid),
                      :task_key, 2, 'incomplete', 'official', repeat('7', 64)
                    )
                    """
                ),
                {
                    "material_id": foreign_material_id,
                    "task_id": str(source.task_version_id),
                    "task_key": source.task_key,
                },
            )
        session.execute(
            text(
                """
                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key, version_no,
                  material_bundle_version_id, source_kind,
                  verification_status, state, total_maximum, criteria, points,
                  content_sha256, supersedes_rubric_version_id
                ) values (
                  cast(:rubric_id as uuid), cast(:task_id as uuid), :task_key, 2,
                  cast(:material_id as uuid), 'official', 'verified',
                  'published', :maximum, '[{}]'::jsonb, '[{}]'::jsonb,
                  repeat('8', 64), cast(:original_rubric_id as uuid)
                )
                """
            ),
            {
                "rubric_id": str(uuid4()),
                "task_id": str(source.task_version_id),
                "task_key": source.task_key,
                "material_id": foreign_material_id,
                "maximum": source.maximum_score,
                "original_rubric_id": original_rubric_id,
            },
        )

    pipeline = GuokaoRubricAIPipeline(
        settings=Settings(
            ai_price_version="test-price-v1",
            ai_input_cny_per_million_tokens="2",
            ai_cached_input_cny_per_million_tokens="0.5",
            ai_output_cny_per_million_tokens="4",
        )
    )
    with db_session_factory() as session:
        assert pipeline.discover_pending_sources(session) == ()
        catalog_rubric_status = _rubric_status(
            session,
            task_version_id=str(source.task_version_id),
        )
    with db_session_factory.begin() as session:
        replay = publisher.publish(session, drafts=[draft], now=NOW)

    assert catalog_rubric_status == "published_system_compiled_unverified"
    assert replay.unchanged_count == 1
    assert replay.versions[0].rubric_version_id == original_rubric_id
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from product.essay_rubric_versions")
        ).scalar_one() == 2


def _published_source(factory: sessionmaker[Session]) -> RubricCompilationSource:
    with factory.begin() as session:
        catalog = GuokaoEssayCatalogPublisher().publish(session, now=NOW)
        task_version_id = catalog.versions[0].task_version_id
    with factory() as session:
        material_bundle_version_id = str(
            session.execute(
                text(
                    "select material_bundle_version_id "
                    "from product.essay_material_bundle_versions "
                    "where task_version_id = cast(:task_version_id as uuid)"
                ),
                {"task_version_id": task_version_id},
            ).scalar_one()
        )
        source = load_rubric_compilation_source(
            session,
            task_version_id=task_version_id,
            material_bundle_version_id=material_bundle_version_id,
        )
    assert source is not None
    return source


def _draft(source: RubricCompilationSource) -> SystemRubricCompilationDraft:
    compiler_version = "compiler-v1"
    prompt_version = "prompt-v1"
    provider = "provider"
    model = "model"
    quote = "材料"
    segment = source.segments[0]
    start = segment.text.index(quote)
    return SystemRubricCompilationDraft.model_validate(
        {
            "contractVersion": "guokao-essay-rubric-v1",
            "taskVersionId": source.task_version_id,
            "materialBundleVersionId": source.material_bundle_version_id,
            "promptSha256": source.prompt_sha256,
            "materialContentSha256": source.material_content_sha256,
            "compilerVersion": compiler_version,
            "promptVersion": prompt_version,
            "provider": provider,
            "model": model,
            "inputFingerprint": rubric_input_fingerprint(
                source=source,
                compiler_version=compiler_version,
                prompt_version=prompt_version,
                provider=provider,
                model=model,
            ),
            "sourceKind": "system_compiled",
            "verificationStatus": "unverified",
            "totalMaximum": source.maximum_score,
            "criteria": [
                {
                    "criterionCode": "content",
                    "label": "内容要点",
                    "maximum": source.maximum_score,
                    "scoringRule": "只按已锚定的材料要点计分。",
                    "materialEvidenceRequired": True,
                }
            ],
            "points": [
                {
                    "rubricPointId": "point-1",
                    "criterionCode": "content",
                    "category": "problem",
                    "label": "材料要点",
                    "maximumCredit": source.maximum_score,
                    "materialSegmentIds": [segment.segment_id],
                    "materialEvidenceRefs": [
                        {
                            "materialBundleVersionId": source.material_bundle_version_id,
                            "materialId": segment.material_id,
                            "segmentId": segment.segment_id,
                            "startOffset": start,
                            "endOffset": start + len(quote),
                            "quote": quote,
                            "quoteSha256": sha256(quote.encode("utf-8")).hexdigest(),
                        }
                    ],
                }
            ],
        }
    )


def _seed_catalog_source(
    factory: sessionmaker[Session],
    *,
    question_id: str,
) -> None:
    paper_title = "2025年国家公务员录用考试《申论》"
    prompt = "请概括给定资料反映的问题，不超过200字。（20分）"
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.questions (
                  question_id, domain, status, module_name, question_type,
                  display_level1, stem, shared_context_required
                ) values (
                  :question_id, 'essay', 'active', '申论', 'summary',
                  '申论', :prompt, true
                )
                """
            ),
            {"question_id": question_id, "prompt": prompt},
        )
        session.execute(
            text(
                """
                insert into product.knowledge_chunks (
                  knowledge_chunk_id, question_id, chunk_kind, chunk_index,
                  title, content, content_sha256, question_type, province,
                  year, metadata
                ) values (
                  :chunk_id, :question_id, 'task', 1, '作答任务', :prompt,
                  repeat('a', 64), 'summary', '国考', 2025,
                  cast(:metadata as jsonb)
                )
                """
            ),
            {
                "chunk_id": f"{question_id}-task",
                "question_id": question_id,
                "prompt": prompt,
                "metadata": json.dumps({"paperTitle": paper_title}, ensure_ascii=False),
            },
        )
        for part_index in (1, 2):
            content = f"完整材料第{part_index}部分。"
            session.execute(
                text(
                    """
                    insert into product.knowledge_chunks (
                      knowledge_chunk_id, question_id, chunk_kind, chunk_index,
                      material_order, title, content, content_sha256,
                      question_type, province, year, metadata
                    ) values (
                      :chunk_id, :question_id, 'material', :chunk_index,
                      1, :title, :content, :content_sha256,
                      'summary', '国考', 2025, cast(:metadata as jsonb)
                    )
                    """
                ),
                {
                    "chunk_id": f"{question_id}-chunk-{part_index}",
                    "question_id": question_id,
                    "chunk_index": part_index,
                    "title": f"材料1（{part_index}/2）",
                    "content": content,
                    "content_sha256": sha256(content.encode("utf-8")).hexdigest(),
                    "metadata": json.dumps(
                        {
                            "paperTitle": paper_title,
                            "materialPartIndex": part_index,
                            "materialPartCount": 2,
                        },
                        ensure_ascii=False,
                    ),
                },
            )
