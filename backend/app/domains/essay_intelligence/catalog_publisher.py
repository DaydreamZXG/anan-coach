from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.essay_intelligence.catalog import (
    CatalogValidationIssue,
    CompiledEssayCatalogEntry,
    EssayCatalogValidationError,
    SourceMaterialChunk,
    SourceQuestion,
    compile_catalog_entry,
)
from app.domains.essay_intelligence.schemas import (
    EssayMaterialBundleVersion,
    EssayMaterialSegment,
    EssayTaskVersion,
)


CATALOG_CONTRACT_VERSION = "guokao-essay-catalog-v2"
_ADVISORY_LOCK_KEY = "guokao-essay-catalog-v2-publisher"


class EssayCatalogPublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishedCatalogVersion:
    question_id: str
    task_key: str
    task_version_id: str
    version_no: int
    aggregate_sha256: str
    action: str
    rubric_status: str


@dataclass(frozen=True)
class EssayCatalogPublishResult:
    contract_version: str
    source_question_count: int
    eligible_question_count: int
    rejected_question_count: int
    inserted_count: int
    unchanged_count: int
    rubric_pending_count: int
    dry_run: bool
    versions: tuple[PublishedCatalogVersion, ...]
    issues: tuple[CatalogValidationIssue, ...]


class GuokaoEssayCatalogPublisher:
    def publish(
        self,
        session: Session,
        *,
        dry_run: bool = False,
        allow_partial: bool = False,
        now: datetime | None = None,
    ) -> EssayCatalogPublishResult:
        generated_at = _as_utc(now or datetime.now(UTC))
        _lock_catalog(session)
        sources = _load_national_sources(session)
        if not sources:
            raise EssayCatalogValidationError(
                [
                    CatalogValidationIssue(
                        question_id="catalog",
                        code="national_sources_missing",
                        message="没有 paperTitle 包含“国考”或“国家公务员”的 active essay question",
                    )
                ]
            )

        compiled: list[CompiledEssayCatalogEntry] = []
        issues: list[CatalogValidationIssue] = []
        for question, chunks in sources:
            try:
                compiled.append(compile_catalog_entry(question, chunks))
            except EssayCatalogValidationError as exc:
                issues.extend(exc.issues)
        if issues and (not allow_partial or not compiled):
            raise EssayCatalogValidationError(issues)

        versions: list[PublishedCatalogVersion] = []
        for entry in compiled:
            current = _current_task(session, task_key=entry.task_key)
            if current is not None and _catalog_entry_matches(
                session,
                current=current,
                entry=entry,
            ):
                versions.append(
                    PublishedCatalogVersion(
                        question_id=entry.question_id,
                        task_key=entry.task_key,
                        task_version_id=str(current["task_version_id"]),
                        version_no=int(current["version_no"]),
                        aggregate_sha256=entry.aggregate_sha256,
                        action="unchanged",
                        rubric_status=_rubric_status(
                            session,
                            task_version_id=str(current["task_version_id"]),
                        ),
                    )
                )
                continue

            version_no = _next_task_version_no(
                session,
                task_key=entry.task_key,
                current=current,
            )
            task_version_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{CATALOG_CONTRACT_VERSION}:task:{entry.task_key}:{version_no}:{entry.aggregate_sha256}",
                )
            )
            versions.append(
                PublishedCatalogVersion(
                    question_id=entry.question_id,
                    task_key=entry.task_key,
                    task_version_id=task_version_id,
                    version_no=version_no,
                    aggregate_sha256=entry.aggregate_sha256,
                    action="would_insert" if dry_run else "inserted",
                    rubric_status="pending_ai_compilation",
                )
            )
            if not dry_run:
                _insert_catalog_entry(
                    session,
                    entry=entry,
                    task_version_id=task_version_id,
                    version_no=version_no,
                    supersedes_task_version_id=(
                        str(current["task_version_id"]) if current is not None else None
                    ),
                    created_at=generated_at,
                )

        return EssayCatalogPublishResult(
            contract_version=CATALOG_CONTRACT_VERSION,
            source_question_count=len(sources),
            eligible_question_count=len(compiled),
            rejected_question_count=len({item.question_id for item in issues}),
            inserted_count=sum(item.action in {"inserted", "would_insert"} for item in versions),
            unchanged_count=sum(item.action == "unchanged" for item in versions),
            rubric_pending_count=sum(
                item.rubric_status == "pending_ai_compilation" for item in versions
            ),
            dry_run=dry_run,
            versions=tuple(versions),
            issues=tuple(issues),
        )


def _load_national_sources(
    session: Session,
) -> list[tuple[SourceQuestion, list[SourceMaterialChunk]]]:
    rows = (
        session.execute(
            text(
                """
            select
              question.question_id,
              question.question_type,
              question.display_level1,
              question.display_level2,
              question.display_level3,
              task.task_prompt,
              task.task_question_type,
              task.task_chunk_count,
              task.task_paper_title,
              chunk.knowledge_chunk_id,
              chunk.source_question_id,
              chunk.source_dataset_id,
              chunk.source_paper_row_id,
              chunk.source_question_key,
              chunk.source_paper_key,
              chunk.chunk_index,
              chunk.material_order,
              chunk.title,
              chunk.content,
              chunk.content_sha256,
              chunk.province,
              chunk.year,
              chunk.tags,
              chunk.metadata
            from product.questions question
            join product.knowledge_chunks chunk
              on chunk.question_id = question.question_id
             and chunk.chunk_kind = 'material'
            left join lateral (
              select
                count(*) as task_chunk_count,
                case when count(*) = 1 then min(task_chunk.content) end as task_prompt,
                case when count(*) = 1 then min(task_chunk.question_type) end
                  as task_question_type,
                case when count(*) = 1
                  then min(task_chunk.metadata ->> 'paperTitle')
                end as task_paper_title
              from product.knowledge_chunks task_chunk
              where task_chunk.question_id = question.question_id
                and task_chunk.chunk_kind = 'task'
            ) task on true
            where question.domain = 'essay'
              and question.status = 'active'
              and exists (
                select 1
                from product.knowledge_chunks national_chunk
                where national_chunk.question_id = question.question_id
                  and national_chunk.chunk_kind = 'material'
                  and (
                    coalesce(national_chunk.metadata ->> 'paperTitle', '') like '%国考%'
                    or coalesce(national_chunk.metadata ->> 'paperTitle', '') like '%国家公务员%'
                  )
              )
            order by question.question_id,
                     chunk.chunk_index nulls last,
                     chunk.knowledge_chunk_id
            """
            )
        )
        .mappings()
        .all()
    )
    grouped: dict[str, tuple[SourceQuestion, list[SourceMaterialChunk]]] = {}
    for row in rows:
        question_id = str(row["question_id"])
        if question_id not in grouped:
            grouped[question_id] = (
                SourceQuestion(
                    question_id=question_id,
                    question_type=(
                        _optional_str(row["task_question_type"])
                        or _optional_str(row["question_type"])
                    ),
                    display_level1=_optional_str(row["display_level1"]),
                    display_level2=_optional_str(row["display_level2"]),
                    display_level3=_optional_str(row["display_level3"]),
                    stem=str(row["task_prompt"] or ""),
                    task_chunk_count=int(row["task_chunk_count"]),
                    task_paper_title=_optional_str(row["task_paper_title"]),
                ),
                [],
            )
        metadata = dict(row["metadata"]) if isinstance(row["metadata"], dict) else {}
        grouped[question_id][1].append(
            SourceMaterialChunk(
                knowledge_chunk_id=str(row["knowledge_chunk_id"]),
                chunk_index=(int(row["chunk_index"]) if row["chunk_index"] is not None else None),
                title=_optional_str(row["title"]),
                content=str(row["content"] or ""),
                metadata=metadata,
                material_order=(
                    int(row["material_order"]) if row["material_order"] is not None else None
                ),
                provenance={
                    "sourceQuestionId": _optional_str(row["source_question_id"]),
                    "sourceDatasetId": _optional_str(row["source_dataset_id"]),
                    "sourcePaperRowId": _optional_str(row["source_paper_row_id"]),
                    "sourceQuestionKey": _optional_str(row["source_question_key"]),
                    "sourcePaperKey": _optional_str(row["source_paper_key"]),
                    "contentSha256": _optional_str(row["content_sha256"]),
                    "province": _optional_str(row["province"]),
                    "year": int(row["year"]) if row["year"] is not None else None,
                    "tags": row["tags"],
                },
            )
        )
    return list(grouped.values())


def _lock_catalog(session: Session) -> None:
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": _ADVISORY_LOCK_KEY},
    )


def _current_task(session: Session, *, task_key: str) -> RowMapping | None:
    return (
        session.execute(
            text(
                """
            select task.*
            from product.essay_task_versions task
            where task.task_key = :task_key
              and not exists (
                select 1
                from product.essay_task_versions successor
                where successor.supersedes_task_version_id = task.task_version_id
              )
            for update
            """
            ),
            {"task_key": task_key},
        )
        .mappings()
        .one_or_none()
    )


def _rubric_status(session: Session, *, task_version_id: str) -> str:
    rows = (
        session.execute(
            text(
                """
            select rubric.source_kind, rubric.verification_status, rubric.state
            from product.essay_rubric_versions rubric
            join product.essay_material_bundle_versions material
              on material.material_bundle_version_id =
                 rubric.material_bundle_version_id
             and material.task_version_id = rubric.task_version_id
             and material.completeness = 'complete'
             and not exists (
               select 1 from product.essay_material_bundle_versions successor
                where successor.supersedes_material_bundle_version_id =
                      material.material_bundle_version_id
             )
            where rubric.task_version_id = cast(:task_version_id as uuid)
              and not exists (
                select 1 from product.essay_rubric_versions successor
                where successor.supersedes_rubric_version_id = rubric.rubric_version_id
                  and successor.task_version_id = rubric.task_version_id
                  and successor.material_bundle_version_id =
                      rubric.material_bundle_version_id
              )
            """
            ),
            {"task_version_id": task_version_id},
        )
        .mappings()
        .all()
    )
    if len(rows) > 1:
        raise EssayCatalogPublishError(
            f"Task {task_version_id} has multiple current rubrics for its current material version"
        )
    row = rows[0] if rows else None
    if row is None or str(row["state"]) != "published":
        return "pending_ai_compilation"
    return f"published_{row['source_kind']}_{row['verification_status']}"


def _next_task_version_no(
    session: Session,
    *,
    task_key: str,
    current: RowMapping | None,
) -> int:
    maximum = int(
        session.execute(
            text(
                "select coalesce(max(version_no), 0) "
                "from product.essay_task_versions where task_key = :task_key"
            ),
            {"task_key": task_key},
        ).scalar_one()
    )
    if maximum > 0 and current is None:
        raise EssayCatalogPublishError(f"Task {task_key} has history but no unique current version")
    if current is not None and int(current["version_no"]) != maximum:
        raise EssayCatalogPublishError(
            f"Task {task_key} current version is not the maximum version"
        )
    return maximum + 1


def _catalog_entry_matches(
    session: Session,
    *,
    current: RowMapping,
    entry: CompiledEssayCatalogEntry,
) -> bool:
    if not (
        str(current["question_id"]) == entry.question_id
        and str(current["question_type"]) == entry.question_type
        and str(current["prompt"]) == entry.prompt
        and str(current["prompt_sha256"]) == entry.prompt_sha256
        and int(current["word_limit"]) == entry.word_limit
        and Decimal(str(current["maximum_score"])) == entry.maximum_score
        and str(current["state"]) == "published"
    ):
        return False
    material = (
        session.execute(
            text(
                """
            select bundle.*,
                   (select count(*)
                    from product.essay_material_segments segment
                    where segment.material_bundle_version_id = bundle.material_bundle_version_id)
                     as segment_count
            from product.essay_material_bundle_versions bundle
            where bundle.task_version_id = cast(:task_version_id as uuid)
              and not exists (
                select 1
                from product.essay_material_bundle_versions successor
                where successor.supersedes_material_bundle_version_id =
                      bundle.material_bundle_version_id
              )
            """
            ),
            {"task_version_id": str(current["task_version_id"])},
        )
        .mappings()
        .one_or_none()
    )
    if material is None or not (
        str(material["completeness"]) == "complete"
        and str(material["source_kind"]) == "own_bank"
        and str(material["content_sha256"]) == entry.material_content_sha256
        and int(material["segment_count"]) == len(entry.material_segments)
    ):
        return False
    return True


def _insert_catalog_entry(
    session: Session,
    *,
    entry: CompiledEssayCatalogEntry,
    task_version_id: str,
    version_no: int,
    supersedes_task_version_id: str | None,
    created_at: datetime,
) -> None:
    material_bundle_version_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{CATALOG_CONTRACT_VERSION}:material:{task_version_id}:{entry.material_content_sha256}",
        )
    )
    _validate_contracts(
        entry=entry,
        task_version_id=task_version_id,
        material_bundle_version_id=material_bundle_version_id,
        version_no=version_no,
        supersedes_task_version_id=supersedes_task_version_id,
        created_at=created_at,
    )
    session.execute(
        text(
            """
            insert into product.essay_task_versions (
              task_version_id, task_key, version_no, question_id, question_type,
              prompt, prompt_sha256, word_limit, maximum_score, state,
              supersedes_task_version_id, created_at
            ) values (
              cast(:task_version_id as uuid), :task_key, :version_no, :question_id,
              :question_type, :prompt, :prompt_sha256, :word_limit, :maximum_score,
              'published', cast(:supersedes_task_version_id as uuid), :created_at
            )
            """
        ),
        {
            "task_version_id": task_version_id,
            "task_key": entry.task_key,
            "version_no": version_no,
            "question_id": entry.question_id,
            "question_type": entry.question_type,
            "prompt": entry.prompt,
            "prompt_sha256": entry.prompt_sha256,
            "word_limit": entry.word_limit,
            "maximum_score": entry.maximum_score,
            "supersedes_task_version_id": supersedes_task_version_id,
            "created_at": created_at,
        },
    )
    session.execute(
        text(
            """
            insert into product.essay_material_bundle_versions (
              material_bundle_version_id, task_version_id, task_key, version_no,
              completeness, source_kind, content_sha256,
              supersedes_material_bundle_version_id, created_at
            ) values (
              cast(:material_bundle_version_id as uuid), cast(:task_version_id as uuid),
              :task_key, 1, 'complete', 'own_bank', :content_sha256, null, :created_at
            )
            """
        ),
        {
            "material_bundle_version_id": material_bundle_version_id,
            "task_version_id": task_version_id,
            "task_key": entry.task_key,
            "content_sha256": entry.material_content_sha256,
            "created_at": created_at,
        },
    )
    for segment in entry.material_segments:
        material_segment_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{CATALOG_CONTRACT_VERSION}:segment:{material_bundle_version_id}:{segment.segment_id}",
            )
        )
        session.execute(
            text(
                """
                insert into product.essay_material_segments (
                  material_segment_id, material_bundle_version_id, material_id,
                  segment_id, material_order, segment_order, text, text_sha256,
                  source_metadata, created_at
                ) values (
                  cast(:material_segment_id as uuid),
                  cast(:material_bundle_version_id as uuid), :material_id,
                  :segment_id, :material_order, :segment_order, :text, :text_sha256,
                  cast(:source_metadata as jsonb), :created_at
                )
                """
            ),
            {
                "material_segment_id": material_segment_id,
                "material_bundle_version_id": material_bundle_version_id,
                "material_id": segment.material_id,
                "segment_id": segment.segment_id,
                "material_order": segment.material_order,
                "segment_order": segment.segment_order,
                "text": segment.text,
                "text_sha256": segment.text_sha256,
                "source_metadata": _json(segment.source_metadata),
                "created_at": created_at,
            },
        )


def _validate_contracts(
    *,
    entry: CompiledEssayCatalogEntry,
    task_version_id: str,
    material_bundle_version_id: str,
    version_no: int,
    supersedes_task_version_id: str | None,
    created_at: datetime,
) -> None:
    EssayTaskVersion.model_validate(
        {
            "taskVersionId": UUID(task_version_id),
            "taskKey": entry.task_key,
            "versionNo": version_no,
            "questionId": entry.question_id,
            "questionType": entry.question_type,
            "prompt": entry.prompt,
            "promptSha256": entry.prompt_sha256,
            "wordLimit": entry.word_limit,
            "maximumScore": float(entry.maximum_score),
            "state": "published",
            "supersedesTaskVersionId": (
                UUID(supersedes_task_version_id) if supersedes_task_version_id else None
            ),
            "createdAt": created_at,
        }
    )
    EssayMaterialBundleVersion.model_validate(
        {
            "materialBundleVersionId": UUID(material_bundle_version_id),
            "taskVersionId": UUID(task_version_id),
            "versionNo": 1,
            "completeness": "complete",
            "sourceKind": "own_bank",
            "contentSha256": entry.material_content_sha256,
            "segments": [
                EssayMaterialSegment(
                    materialId=item.material_id,
                    segmentId=item.segment_id,
                    materialOrder=item.material_order,
                    segmentOrder=item.segment_order,
                    text=item.text,
                    textSha256=item.text_sha256,
                )
                for item in entry.material_segments
            ],
            "supersedesMaterialBundleVersionId": None,
            "createdAt": created_at,
        }
    )


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
