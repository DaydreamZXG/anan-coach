from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.essay_intelligence.rubric_compiler import (
    RUBRIC_COMPILATION_CONTRACT_VERSION,
    CompiledSystemRubric,
    EssayRubricCompilationError,
    RubricCompilationIssue,
    RubricCompilationSource,
    RubricSourceSegment,
    SystemRubricCompilationDraft,
    compile_system_rubric,
)
from app.domains.essay_intelligence.schemas import EssayRubricVersion


_ADVISORY_LOCK_KEY = "guokao-essay-rubric-v1-publisher"


class EssayRubricPublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishedRubricVersion:
    task_version_id: str
    material_bundle_version_id: str
    rubric_version_id: str
    version_no: int
    content_sha256: str
    action: str
    source_kind: str = "system_compiled"
    verification_status: str = "unverified"


@dataclass(frozen=True)
class EssayRubricPublishResult:
    contract_version: str
    inserted_count: int
    unchanged_count: int
    dry_run: bool
    versions: tuple[PublishedRubricVersion, ...]


class GuokaoEssayRubricPublisher:
    def publish(
        self,
        session: Session,
        *,
        drafts: list[SystemRubricCompilationDraft],
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> EssayRubricPublishResult:
        if not drafts:
            raise EssayRubricPublishError("At least one rubric draft is required")
        task_ids = [str(item.task_version_id) for item in drafts]
        if len(task_ids) != len(set(task_ids)):
            raise EssayRubricPublishError("A publish batch may contain one draft per task")

        generated_at = _as_utc(now or datetime.now(UTC))
        _lock(session)
        compiled: list[tuple[RubricCompilationSource, CompiledSystemRubric]] = []
        issues: list[RubricCompilationIssue] = []
        for draft in drafts:
            source = load_rubric_compilation_source(
                session,
                task_version_id=str(draft.task_version_id),
                material_bundle_version_id=str(draft.material_bundle_version_id),
            )
            if source is None:
                issues.append(
                    RubricCompilationIssue(
                        code="published_source_missing",
                        message=(
                            f"task {draft.task_version_id} is not a current published "
                            "Guokao task with the pinned complete material"
                        ),
                    )
                )
                continue
            try:
                compiled.append(
                    (source, compile_system_rubric(source=source, draft=draft))
                )
            except EssayRubricCompilationError as exc:
                issues.extend(exc.issues)
        if issues:
            raise EssayRubricCompilationError(issues)

        versions: list[PublishedRubricVersion] = []
        for source, rubric in compiled:
            if _has_official_rubric(
                session,
                task_version_id=str(source.task_version_id),
                material_bundle_version_id=str(source.material_bundle_version_id),
            ):
                raise EssayRubricPublishError(
                    f"Task {source.task_version_id} already has an official rubric"
                )
            current = _current_rubric(
                session,
                task_version_id=str(source.task_version_id),
                material_bundle_version_id=str(source.material_bundle_version_id),
            )
            if current is not None and _matches(current=current, compiled=rubric):
                versions.append(
                    PublishedRubricVersion(
                        task_version_id=str(source.task_version_id),
                        material_bundle_version_id=str(
                            source.material_bundle_version_id
                        ),
                        rubric_version_id=str(current["rubric_version_id"]),
                        version_no=int(current["version_no"]),
                        content_sha256=rubric.content_sha256,
                        action="unchanged",
                    )
                )
                continue

            version_no = _next_version_no(
                session,
                task_version_id=str(source.task_version_id),
            )
            rubric_version_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{RUBRIC_COMPILATION_CONTRACT_VERSION}:rubric:"
                    f"{source.task_version_id}:{version_no}:{rubric.content_sha256}",
                )
            )
            _validate_persisted_contract(
                source=source,
                compiled=rubric,
                rubric_version_id=rubric_version_id,
                version_no=version_no,
                supersedes=(
                    str(current["rubric_version_id"]) if current is not None else None
                ),
                created_at=generated_at,
            )
            versions.append(
                PublishedRubricVersion(
                    task_version_id=str(source.task_version_id),
                    material_bundle_version_id=str(source.material_bundle_version_id),
                    rubric_version_id=rubric_version_id,
                    version_no=version_no,
                    content_sha256=rubric.content_sha256,
                    action="would_insert" if dry_run else "inserted",
                )
            )
            if not dry_run:
                _insert(
                    session,
                    source=source,
                    compiled=rubric,
                    rubric_version_id=rubric_version_id,
                    version_no=version_no,
                    supersedes=(
                        str(current["rubric_version_id"])
                        if current is not None
                        else None
                    ),
                    created_at=generated_at,
                )

        return EssayRubricPublishResult(
            contract_version=RUBRIC_COMPILATION_CONTRACT_VERSION,
            inserted_count=sum(
                item.action in {"inserted", "would_insert"} for item in versions
            ),
            unchanged_count=sum(item.action == "unchanged" for item in versions),
            dry_run=dry_run,
            versions=tuple(versions),
        )


def load_rubric_compilation_source(
    session: Session,
    *,
    task_version_id: str,
    material_bundle_version_id: str,
) -> RubricCompilationSource | None:
    row = session.execute(
        text(
            """
            select task.task_version_id, task.task_key, task.question_type,
                   task.prompt, task.prompt_sha256, task.maximum_score,
                   material.material_bundle_version_id,
                   material.content_sha256 as material_content_sha256
            from product.essay_task_versions task
            join product.essay_material_bundle_versions material
              on material.material_bundle_version_id =
                 cast(:material_bundle_version_id as uuid)
             and material.task_version_id = task.task_version_id
             and material.completeness = 'complete'
            where task.task_version_id = cast(:task_version_id as uuid)
              and task.state = 'published'
              and task.task_key like 'guokao%'
              and not exists (
                select 1 from product.essay_task_versions successor
                where successor.task_key = task.task_key
                  and successor.version_no > task.version_no
                  and successor.state = 'published'
              )
              and not exists (
                select 1 from product.essay_material_bundle_versions successor
                where successor.task_version_id = material.task_version_id
                  and successor.version_no > material.version_no
                  and successor.completeness = 'complete'
              )
            """
        ),
        {
            "task_version_id": task_version_id,
            "material_bundle_version_id": material_bundle_version_id,
        },
    ).mappings().one_or_none()
    if row is None:
        return None
    segments = session.execute(
        text(
            """
            select material_id, segment_id, text, text_sha256
            from product.essay_material_segments
            where material_bundle_version_id = cast(:material_bundle_version_id as uuid)
            order by material_order, segment_order, segment_id
            """
        ),
        {"material_bundle_version_id": material_bundle_version_id},
    ).mappings().all()
    if not segments:
        return None
    return RubricCompilationSource(
        task_version_id=UUID(str(row["task_version_id"])),
        task_key=str(row["task_key"]),
        question_type=str(row["question_type"]),
        prompt=str(row["prompt"]),
        prompt_sha256=str(row["prompt_sha256"]),
        maximum_score=float(row["maximum_score"]),
        material_bundle_version_id=UUID(str(row["material_bundle_version_id"])),
        material_content_sha256=str(row["material_content_sha256"]),
        segments=tuple(
            RubricSourceSegment(
                material_id=str(item["material_id"]),
                segment_id=str(item["segment_id"]),
                text=str(item["text"]),
                text_sha256=str(item["text_sha256"]),
            )
            for item in segments
        ),
    )


def _current_rubric(
    session: Session,
    *,
    task_version_id: str,
    material_bundle_version_id: str,
) -> RowMapping | None:
    rows = session.execute(
        text(
            """
            select rubric.*
            from product.essay_rubric_versions rubric
            where rubric.task_version_id = cast(:task_version_id as uuid)
              and rubric.material_bundle_version_id =
                  cast(:material_bundle_version_id as uuid)
              and not exists (
                select 1 from product.essay_rubric_versions successor
                where successor.supersedes_rubric_version_id = rubric.rubric_version_id
                  and successor.task_version_id = rubric.task_version_id
                  and successor.material_bundle_version_id =
                      rubric.material_bundle_version_id
              )
            for update
            """
        ),
        {
            "task_version_id": task_version_id,
            "material_bundle_version_id": material_bundle_version_id,
        },
    ).mappings().all()
    if len(rows) > 1:
        raise EssayRubricPublishError(
            f"Task {task_version_id} has multiple current rubric versions"
        )
    return rows[0] if rows else None


def _has_official_rubric(
    session: Session,
    *,
    task_version_id: str,
    material_bundle_version_id: str,
) -> bool:
    return bool(
        session.execute(
            text(
                """
                select exists (
                  select 1 from product.essay_rubric_versions rubric
                  where rubric.task_version_id = cast(:task_version_id as uuid)
                    and rubric.material_bundle_version_id =
                        cast(:material_bundle_version_id as uuid)
                    and rubric.source_kind = 'official'
                    and rubric.state = 'published'
                    and not exists (
                      select 1 from product.essay_rubric_versions successor
                       where successor.supersedes_rubric_version_id =
                             rubric.rubric_version_id
                         and successor.task_version_id = rubric.task_version_id
                         and successor.material_bundle_version_id =
                             rubric.material_bundle_version_id
                    )
                )
                """
            ),
            {
                "task_version_id": task_version_id,
                "material_bundle_version_id": material_bundle_version_id,
            },
        ).scalar_one()
    )


def _next_version_no(
    session: Session,
    *,
    task_version_id: str,
) -> int:
    maximum = int(
        session.execute(
            text(
                "select coalesce(max(version_no), 0) "
                "from product.essay_rubric_versions "
                "where task_version_id = cast(:task_version_id as uuid)"
            ),
            {"task_version_id": task_version_id},
        ).scalar_one()
    )
    return maximum + 1


def _matches(*, current: RowMapping, compiled: CompiledSystemRubric) -> bool:
    return (
        str(current["source_kind"]) == "system_compiled"
        and str(current["verification_status"]) == "unverified"
        and str(current["state"]) == "published"
        and float(current["total_maximum"]) == compiled.total_maximum
        and str(current["content_sha256"]) == compiled.content_sha256
        and str(current["material_bundle_version_id"])
        == str(compiled.material_bundle_version_id)
        and str(current["compilation_input_fingerprint"])
        == compiled.input_fingerprint
        and current["criteria"]
        == [item.model_dump(by_alias=True, mode="json") for item in compiled.criteria]
        and current["points"]
        == [item.model_dump(by_alias=True, mode="json") for item in compiled.points]
    )


def _validate_persisted_contract(
    *,
    source: RubricCompilationSource,
    compiled: CompiledSystemRubric,
    rubric_version_id: str,
    version_no: int,
    supersedes: str | None,
    created_at: datetime,
) -> None:
    EssayRubricVersion.model_validate_json(
        json.dumps(
            {
                "rubricVersionId": rubric_version_id,
                "taskVersionId": str(source.task_version_id),
                "materialBundleVersionId": str(source.material_bundle_version_id),
                "versionNo": version_no,
                "sourceKind": "system_compiled",
                "verificationStatus": "unverified",
                "state": "published",
                "totalMaximum": compiled.total_maximum,
                "criteria": [
                    item.model_dump(by_alias=True, mode="json")
                    for item in compiled.criteria
                ],
                "points": [
                    item.model_dump(by_alias=True, mode="json")
                    for item in compiled.points
                ],
                "contentSha256": compiled.content_sha256,
                "supersedesRubricVersionId": supersedes,
                "createdAt": created_at.isoformat(),
            },
            ensure_ascii=False,
        )
    )


def _insert(
    session: Session,
    *,
    source: RubricCompilationSource,
    compiled: CompiledSystemRubric,
    rubric_version_id: str,
    version_no: int,
    supersedes: str | None,
    created_at: datetime,
) -> None:
    session.execute(
        text(
            """
            insert into product.essay_rubric_versions (
              rubric_version_id, task_version_id, task_key, version_no,
              material_bundle_version_id, compilation_input_fingerprint,
              source_kind, verification_status, state, total_maximum,
              criteria, points, content_sha256, supersedes_rubric_version_id,
              compilation_audit, created_at
            ) values (
              cast(:rubric_version_id as uuid), cast(:task_version_id as uuid),
              :task_key, :version_no, cast(:material_bundle_version_id as uuid),
              :compilation_input_fingerprint,
              'system_compiled', 'unverified', 'published',
              :total_maximum, cast(:criteria as jsonb), cast(:points as jsonb),
              :content_sha256, cast(:supersedes as uuid),
              cast(:compilation_audit as jsonb), :created_at
            )
            """
        ),
        {
            "rubric_version_id": rubric_version_id,
            "task_version_id": str(source.task_version_id),
            "task_key": source.task_key,
            "version_no": version_no,
            "material_bundle_version_id": str(source.material_bundle_version_id),
            "compilation_input_fingerprint": compiled.input_fingerprint,
            "total_maximum": compiled.total_maximum,
            "criteria": json.dumps(
                [
                    item.model_dump(by_alias=True, mode="json")
                    for item in compiled.criteria
                ],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "points": json.dumps(
                [
                    item.model_dump(by_alias=True, mode="json")
                    for item in compiled.points
                ],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "content_sha256": compiled.content_sha256,
            "compilation_audit": json.dumps(
                _compilation_audit_payload(source=source, compiled=compiled),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "supersedes": supersedes,
            "created_at": created_at,
        },
    )


def _compilation_audit_payload(
    *,
    source: RubricCompilationSource,
    compiled: CompiledSystemRubric,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contractVersion": RUBRIC_COMPILATION_CONTRACT_VERSION,
        "compilerVersion": compiled.compiler_version,
        "promptVersion": compiled.prompt_version,
        "provider": compiled.provider,
        "model": compiled.model,
        "inputFingerprint": compiled.input_fingerprint,
        "promptSha256": source.prompt_sha256,
        "materialContentSha256": source.material_content_sha256,
    }
    if compiled.pipeline_audit is not None:
        payload["pipelineAudit"] = compiled.pipeline_audit.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
        )
    return payload


def _lock(session: Session) -> None:
    session.execute(
        text("select pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": _ADVISORY_LOCK_KEY},
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Rubric publication time must include a timezone")
    return value.astimezone(UTC)
