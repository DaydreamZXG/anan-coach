from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.essay_intelligence.catalog import EssayCatalogValidationError
from app.domains.essay_intelligence.catalog_publisher import GuokaoEssayCatalogPublisher


NOW = datetime(2026, 7, 13, 10, tzinfo=UTC)


def test_publisher_covers_national_and_provincial_catalog_and_replays_idempotently(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_question(
        db_session_factory,
        question_id="national-summary",
        paper_title="2025年国家公务员录用考试《申论》地市级卷",
        prompt="请概括给定资料反映的问题，不超过200字。（20分）",
        question_stem="请概括给定资料反映的问题。",
    )
    _seed_question(
        db_session_factory,
        question_id="provincial-summary",
        paper_title="2025年某省公务员录用考试《申论》卷",
        prompt="请概括问题，不超过200字。（20分）",
    )
    publisher = GuokaoEssayCatalogPublisher()

    with db_session_factory.begin() as session:
        first = publisher.publish(session, now=NOW)
    with db_session_factory.begin() as session:
        replay = publisher.publish(session, now=NOW)

    assert first.source_question_count == 2
    assert first.inserted_count == 2
    assert first.unchanged_count == 0
    assert first.rubric_pending_count == 2
    assert first.versions[0].rubric_status == "pending_ai_compilation"
    assert first.versions[0].question_id == "national-summary"
    assert replay.inserted_count == 0
    assert replay.unchanged_count == 2
    assert replay.versions[0].task_version_id == first.versions[0].task_version_id

    with db_session_factory() as session:
        counts = (
            session.execute(
                text(
                    """
                select
                  (select count(*) from product.essay_task_versions) as tasks,
                  (select count(*) from product.essay_material_bundle_versions) as bundles,
                  (select count(*) from product.essay_material_segments) as segments,
                  (select count(*) from product.essay_rubric_versions) as rubrics
                """
                )
            )
            .mappings()
            .one()
        )
        assert dict(counts) == {"tasks": 2, "bundles": 2, "segments": 4, "rubrics": 0}
        rows = (
            session.execute(
                text(
                    """
                select task.question_id, task.state, bundle.completeness,
                       bundle.source_kind as material_source_kind,
                       length(task.prompt_sha256) as prompt_hash_length,
                       length(bundle.content_sha256) as material_hash_length
                from product.essay_task_versions task
                join product.essay_material_bundle_versions bundle
                  on bundle.task_version_id = task.task_version_id
                """
                )
            )
            .mappings()
            .all()
        )
        assert {row["question_id"] for row in rows} == {
            "national-summary",
            "provincial-summary",
        }
        assert all(row["state"] == "published" for row in rows)
        assert all(row["completeness"] == "complete" for row in rows)
        assert all(row["material_source_kind"] == "own_bank" for row in rows)
        assert all(row["prompt_hash_length"] == 64 for row in rows)
        assert all(row["material_hash_length"] == 64 for row in rows)


def test_source_change_appends_coherent_task_catalog_version(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_question(
        db_session_factory,
        question_id="national-versioned",
        paper_title="国考2024年《申论》卷",
        prompt="请提出对策，不超过300字。（25分）",
        question_type="countermeasure",
    )
    publisher = GuokaoEssayCatalogPublisher()
    with db_session_factory.begin() as session:
        first = publisher.publish(session, now=NOW)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update product.knowledge_chunks set content = '更新后的完整材料上半部分。' "
                "where knowledge_chunk_id = 'national-versioned-chunk-1'"
            )
        )
    with db_session_factory.begin() as session:
        second = publisher.publish(session, now=NOW)

    assert second.inserted_count == 1
    assert second.versions[0].version_no == 2
    assert second.versions[0].task_version_id != first.versions[0].task_version_id
    with db_session_factory() as session:
        rows = (
            session.execute(
                text(
                    """
                select version_no, task_version_id, supersedes_task_version_id
                from product.essay_task_versions
                where task_key = :task_key
                order by version_no
                """
                ),
                {"task_key": first.versions[0].task_key},
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2
        assert rows[1]["supersedes_task_version_id"] == rows[0]["task_version_id"]
        assert (
            session.execute(
                text("select count(*) from product.essay_material_bundle_versions")
            ).scalar_one()
            == 2
        )
        assert (
            session.execute(text("select count(*) from product.essay_rubric_versions")).scalar_one()
            == 0
        )


def test_validation_failure_rolls_back_entire_catalog(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_question(
        db_session_factory,
        question_id="national-valid",
        paper_title="国家公务员考试《申论》卷",
        prompt="请概括问题，不超过200字。（20分）",
    )
    _seed_question(
        db_session_factory,
        question_id="national-incomplete",
        paper_title="国家公务员考试《申论》卷",
        prompt="请概括问题，不超过200字。（20分）",
        complete=False,
    )

    with pytest.raises(EssayCatalogValidationError):
        with db_session_factory.begin() as session:
            GuokaoEssayCatalogPublisher().publish(session, now=NOW)

    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from product.essay_task_versions")).scalar_one()
            == 0
        )


def test_partial_publication_keeps_valid_catalog_entries_and_reports_rejections(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_question(
        db_session_factory,
        question_id="national-valid-partial",
        paper_title="国家公务员考试《申论》卷",
        prompt="请概括问题，不超过200字。（20分）",
    )
    _seed_question(
        db_session_factory,
        question_id="national-invalid-partial",
        paper_title="国家公务员考试《申论》卷",
        prompt="请概括问题，不超过200字。（20分）",
        complete=False,
    )

    with db_session_factory.begin() as session:
        result = GuokaoEssayCatalogPublisher().publish(
            session,
            allow_partial=True,
            now=NOW,
        )

    assert result.source_question_count == 2
    assert result.eligible_question_count == 1
    assert result.rejected_question_count == 1
    assert result.inserted_count == 1
    assert {issue.question_id for issue in result.issues} == {"national-invalid-partial"}
    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from product.essay_task_versions")).scalar_one()
            == 1
        )


def test_dry_run_validates_without_writing(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_question(
        db_session_factory,
        question_id="national-dry-run",
        paper_title="2025国考《申论》卷",
        prompt="请概括问题，不超过200字。（20分）",
    )
    with db_session_factory.begin() as session:
        result = GuokaoEssayCatalogPublisher().publish(session, dry_run=True, now=NOW)

    assert result.dry_run is True
    assert result.inserted_count == 1
    assert result.versions[0].action == "would_insert"
    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from product.essay_task_versions")).scalar_one()
            == 0
        )


def test_cli_dry_run_uses_the_same_transactional_publisher(
    db_session_factory: sessionmaker[Session],
    postgres_url: str,
) -> None:
    _seed_question(
        db_session_factory,
        question_id="national-cli",
        paper_title="国家公务员录用考试《申论》卷",
        prompt="请概括问题，不超过200字。（20分）",
    )
    script = Path(__file__).resolve().parents[2] / "scripts" / "publish_guokao_essay_catalog.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--database-url",
            postgres_url,
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["sourceQuestionCount"] == 1
    assert payload["insertedCount"] == 1
    assert payload["rubricPendingCount"] == 1
    assert payload["dryRun"] is True
    with db_session_factory() as session:
        assert (
            session.execute(text("select count(*) from product.essay_task_versions")).scalar_one()
            == 0
        )


def _seed_question(
    factory: sessionmaker[Session],
    *,
    question_id: str,
    paper_title: str,
    prompt: str,
    question_type: str = "summary",
    complete: bool = True,
    question_stem: str | None = None,
) -> None:
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.questions (
                  question_id, domain, status, module_name, question_type,
                  display_level1, stem, shared_context_required
                ) values (
                  :question_id, 'essay', 'active', '申论', :question_type,
                  '申论', :prompt, true
                )
                """
            ),
            {
                "question_id": question_id,
                "question_type": question_type,
                "prompt": question_stem or prompt,
            },
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
                  repeat('a', 64), :question_type, '国考', 2025,
                  cast(:metadata as jsonb)
                )
                """
            ),
            {
                "chunk_id": f"{question_id}-task",
                "question_id": question_id,
                "prompt": prompt,
                "question_type": question_type,
                "metadata": json.dumps(
                    {"paperTitle": paper_title, "section": "申论"},
                    ensure_ascii=False,
                ),
            },
        )
        parts = [(1, 2)] if not complete else [(1, 2), (2, 2)]
        for part_index, part_count in parts:
            session.execute(
                text(
                    """
                    insert into product.knowledge_chunks (
                      knowledge_chunk_id, question_id, chunk_kind, chunk_index,
                      material_order, title, content, content_sha256,
                      question_type, province, year, metadata
                    ) values (
                      :chunk_id, :question_id, 'material', :chunk_index,
                      1, :title, :content, repeat('b', 64),
                      :question_type, '国考', 2025,
                      cast(:metadata as jsonb)
                    )
                    """
                ),
                {
                    "chunk_id": f"{question_id}-chunk-{part_index}",
                    "question_id": question_id,
                    "chunk_index": part_index,
                    "title": f"材料1（{part_index}/{part_count}）",
                    "content": f"完整材料第{part_index}部分。",
                    "question_type": question_type,
                    "metadata": json.dumps(
                        {
                            "paperTitle": paper_title,
                            "materialPartIndex": part_index,
                            "materialPartCount": part_count,
                        },
                        ensure_ascii=False,
                    ),
                },
            )
